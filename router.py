"""
智能模型路由器 (Intelligent Model Router)
==========================================

自动检测任务类型 → 选择最优模型 → 分发到子代理 → 结果回归主模型。

核心原则:
  - 主模型 (Orchestrator) 保持不变，维护完整会话上下文
  - 子任务通过子代理分发到最优模型
  - 子代理接收最小化、精准的上下文
  - 完成后结果无缝回归主会话

依赖: anthropic >= 0.45.0, pyyaml >= 6.0
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import yaml
from anthropic import Anthropic, AsyncAnthropic
from anthropic.types import Message, MessageParam

logger = logging.getLogger(__name__)

# ============================================================
# 类型定义
# ============================================================


class Complexity(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    VERY_COMPLEX = "very_complex"


class Domain(str, Enum):
    CODING = "coding"
    ARCHITECTURE = "architecture"
    REVIEW = "review"
    TESTING = "testing"
    DOCS = "docs"
    RESEARCH = "research"
    DATA_ANALYSIS = "data_analysis"
    CONVERSATION = "conversation"


class BudgetLevel(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


@dataclass
class TaskClassification:
    """任务分类结果"""

    task_type: str
    complexity: Complexity
    domain: Domain
    needs_deep_reasoning: bool
    estimated_tokens: int
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.8
    reasoning: str = ""


@dataclass
class RoutingDecision:
    """路由决策"""

    model_id: str
    model_display_name: str
    thinking: dict[str, Any]
    effort: str
    max_tokens: int
    matched_rule: str
    reasoning: str = ""


@dataclass
class SubagentResult:
    """子代理执行结果"""

    task_id: str
    model_used: str
    success: bool
    output: str
    usage: dict[str, int] = field(default_factory=dict)
    elapsed_ms: float = 0
    error: Optional[str] = None


# ============================================================
# 任务分类器
# ============================================================


CLASSIFIER_SYSTEM_PROMPT = """你是一个任务分类专家。分析用户的任务，输出 JSON 格式的分类结果。

## 分类维度

### complexity (复杂度)
- simple: 单步操作、简单编辑、格式修改、变量重命名
- moderate: 需要思考但范围明确，如单个函数实现、常规测试编写
- complex: 多步骤、跨文件、需要深度分析
- very_complex: 系统级设计、架构决策、多系统集成

### domain (领域)
- coding: 编写/修改代码
- architecture: 系统设计、架构规划、技术选型
- review: 代码审查、安全审计、质量检查
- testing: 测试编写、测试策略
- docs: 文档编写、注释、README
- research: 信息搜索、技术调研
- data_analysis: 数据分析、指标计算
- conversation: 一般对话、问答

### needs_deep_reasoning (是否需要深度推理)
- true: 需要多维度权衡、因果分析、创新性思考
- false: 模式匹配、规则应用、常规操作

### tags (标签)
识别任务中的关键标签: security, auth, performance, refactoring, bug_fix, api, database, ui, devops, ml

## 输出格式

严格输出 JSON，不要添加其他文字:
{
  "task_type": "简短的任务类型名",
  "complexity": "simple|moderate|complex|very_complex",
  "domain": "coding|architecture|review|testing|docs|research|data_analysis|conversation",
  "needs_deep_reasoning": true/false,
  "estimated_tokens": 数字(估计输出token量),
  "tags": ["标签1", "标签2"],
  "confidence": 0.0-1.0,
  "reasoning": "简短分类理由"
}"""


def _extract_text(content: list) -> str:
    """从 Anthropic response.content 中安全提取文本（跳过 thinking blocks）"""
    text_parts = []
    for block in content:
        if hasattr(block, 'text'):
            text_parts.append(block.text)
        elif isinstance(block, dict):
            if block.get('type') == 'text':
                text_parts.append(block.get('text', ''))
    return ''.join(text_parts)


class TaskClassifier:
    """使用轻量级 LLM 调用进行任务分类"""

    def __init__(self, client: AsyncAnthropic, classifier_model: str = "claude-haiku-4-5"):
        self.client = client
        self.classifier_model = classifier_model

    async def classify(self, task: str) -> TaskClassification:
        """分析任务并返回分类结果"""
        response = await self.client.messages.create(
            model=self.classifier_model,
            max_tokens=512,
            system=CLASSIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"请分类以下任务:\n\n{task}"}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "task_type": {"type": "string"},
                            "complexity": {"type": "string", "enum": ["simple", "moderate", "complex", "very_complex"]},
                            "domain": {"type": "string", "enum": ["coding", "architecture", "review", "testing", "docs", "research", "data_analysis", "conversation"]},
                            "needs_deep_reasoning": {"type": "boolean"},
                            "estimated_tokens": {"type": "integer"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "confidence": {"type": "number"},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["task_type", "complexity", "domain", "needs_deep_reasoning", "estimated_tokens", "tags", "confidence", "reasoning"],
                        "additionalProperties": False,
                    },
                }
            },
        )

        data = json.loads(_extract_text(response.content))
        logger.info(f"任务分类: {data['task_type']} (复杂度={data['complexity']}, 领域={data['domain']}, 置信度={data['confidence']})")

        return TaskClassification(
            task_type=data["task_type"],
            complexity=Complexity(data["complexity"]),
            domain=Domain(data["domain"]),
            needs_deep_reasoning=data["needs_deep_reasoning"],
            estimated_tokens=data["estimated_tokens"],
            tags=data.get("tags", []),
            confidence=data["confidence"],
            reasoning=data["reasoning"],
        )


# ============================================================
# 模型路由器
# ============================================================


@dataclass
class ModelSpec:
    """模型规格"""
    id: str
    display_name: str
    description: str
    default_effort: str
    thinking: dict[str, Any]
    cost_per_1m_input: float
    cost_per_1m_output: float


class ModelRouter:
    """
    根据任务分类结果选择最优模型和参数。

    使用规则引擎 + 配置驱动的方式进行路由决策。
    """

    def __init__(self, config_path: Optional[str] = None):
        self.models: dict[str, ModelSpec] = {}
        self.routing_rules: list[dict] = []
        self.budget_strategies: dict[str, dict] = {}
        self._load_config(config_path)

    def _load_config(self, config_path: Optional[str] = None):
        """加载配置 (兼容 v1 和 v2 格式)"""
        if config_path is None:
            config_path = str(Path(__file__).parent / "config.yaml")

        if Path(config_path).exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
        else:
            config = self._default_config()

        config_version = str(config.get("version", "1.0"))

        if config_version.startswith("2"):
            self._load_v2_config(config)
        else:
            self._load_v1_config(config)

    def _load_v2_config(self, config: dict):
        """加载 v2 配置格式"""
        # 解析 v2 providers.models 为内部 ModelSpec 格式
        for provider_name, provider in config.get("providers", {}).items():
            if not provider.get("enabled", True):
                continue
            for model in provider.get("models", []):
                # 直接用模型 ID 作为 key (如 "claude-opus-4-7", "gpt-5")
                key = model["id"]
                thinking_raw = model.get("thinking", "adaptive")
                self.models[key] = ModelSpec(
                    id=model["id"],
                    display_name=model.get("display_name", model["id"]),
                    description=model.get("description", ""),
                    default_effort="high",
                    thinking={"type": thinking_raw} if thinking_raw != "disabled" else {"type": "disabled"},
                    cost_per_1m_input=model.get("cost_per_1m_input", 0),
                    cost_per_1m_output=model.get("cost_per_1m_output", 0),
                )

        # 如果没有模型定义，使用默认
        if not self.models:
            default = self._default_config()
            self._load_v1_config(default)

        # 解析 v2 routing.rules 为内部格式
        routing = config.get("routing", {})
        raw_rules = routing.get("rules", [])
        self.routing_rules = []
        for rule in raw_rules:
            v2_match = rule.get("match", {})
            v2_route = rule.get("route", {})

            # 将 v2 match/route 转换为 v1 conditions/target
            conditions = {}
            for k, v in v2_match.items():
                if k == "tags":
                    conditions["tags"] = v
                else:
                    conditions[k] = v

            target_model = v2_route.get("model", "claude-opus-4-7")
            # v2 的 model 字段已经是模型 ID (如 "claude-opus-4-7")，直接使用

            self.routing_rules.append({
                "name": rule.get("name", "unknown"),
                "priority": rule.get("priority", 0),
                "conditions": conditions,
                "target": {
                    "model": target_model,  # 直接使用模型 ID
                    "effort": v2_route.get("effort", "high"),
                },
            })

        # 按优先级排序
        self.routing_rules.sort(key=lambda r: r["priority"], reverse=True)

        # 预算策略
        budget = config.get("budget", {})
        strategies = budget.get("strategies", {})
        self.budget_strategies = {}
        for name, strat in strategies.items():
            self.budget_strategies[name] = {
                "model_upgrade": strat.get("model_upgrade", False),
                "model_downgrade": strat.get("model_downgrade", False),
                "effort_upgrade": strat.get("effort_upgrade", False),
                "effort_downgrade": strat.get("effort_downgrade", False),
                "prefer_cheapest": strat.get("prefer_cheapest", False),
            }

    def _load_v1_config(self, config: dict):
        """加载 v1 配置格式 (向后兼容)"""
        # 解析模型定义
        for name, spec in config.get("models", {}).items():
            self.models[name] = ModelSpec(
                id=spec["id"],
                display_name=name,
                description=spec.get("description", ""),
                default_effort=spec.get("default_effort", "high"),
                thinking={"type": spec["thinking"]} if spec.get("thinking") != "disabled" else {"type": "disabled"},
                cost_per_1m_input=spec.get("cost_per_1m_input", 0),
                cost_per_1m_output=spec.get("cost_per_1m_output", 0),
            )

        # 按优先级排序规则
        self.routing_rules = sorted(config.get("routing_rules", []), key=lambda r: r.get("priority", 0), reverse=True)
        self.budget_strategies = config.get("budget_strategies", {})

    @staticmethod
    def _default_config() -> dict:
        """默认配置 (当 config.yaml 不存在时使用)"""
        return {
            "models": {
                "opus": {"id": "claude-opus-4-7", "default_effort": "high", "thinking": "adaptive"},
                "sonnet": {"id": "claude-sonnet-4-6", "default_effort": "high", "thinking": "adaptive"},
                "haiku": {"id": "claude-haiku-4-5", "default_effort": "low", "thinking": "disabled"},
            },
            "routing_rules": [
                {"name": "architecture", "priority": 100, "conditions": {"domain": "architecture"}, "target": {"model": "opus", "effort": "max"}},
                {"name": "review", "priority": 90, "conditions": {"domain": "review"}, "target": {"model": "opus", "effort": "high"}},
                {"name": "complex_coding", "priority": 80, "conditions": {"domain": "coding", "complexity": ["complex", "very_complex"]}, "target": {"model": "opus", "effort": "high"}},
                {"name": "moderate_coding", "priority": 70, "conditions": {"domain": "coding", "complexity": "moderate"}, "target": {"model": "sonnet", "effort": "high"}},
                {"name": "testing", "priority": 60, "conditions": {"domain": "testing"}, "target": {"model": "sonnet", "effort": "medium"}},
                {"name": "docs", "priority": 60, "conditions": {"domain": "docs"}, "target": {"model": "sonnet", "effort": "medium"}},
                {"name": "research", "priority": 60, "conditions": {"domain": "research"}, "target": {"model": "sonnet", "effort": "high"}},
                {"name": "simple", "priority": 50, "conditions": {"complexity": "simple"}, "target": {"model": "haiku", "effort": "low"}},
                {"name": "default", "priority": 0, "conditions": {}, "target": {"model": "opus", "effort": "high"}},
            ],
        }

    def _match_rule(self, classification: TaskClassification, rule: dict) -> bool:
        """检查分类结果是否匹配规则条件"""
        conditions = rule.get("conditions", {})
        if not conditions:
            return True

        for key, expected in conditions.items():
            actual = getattr(classification, key, None)
            if actual is None:
                actual = classification.tags if key == "tags" else None

            if isinstance(expected, list):
                if isinstance(actual, list):
                    # tags 匹配: 任意标签命中
                    if not any(t in actual for t in expected):
                        return False
                elif actual.value not in expected:
                    return False
            elif isinstance(actual, list):
                if expected not in actual:
                    return False
            elif hasattr(actual, 'value'):
                if actual.value != expected:
                    return False
            elif actual != expected:
                return False

        return True

    def _resolve_model_key(self, model_name: str) -> str:
        """将模型名/别名解析为 self.models 中的 key"""
        # 直接命中
        if model_name in self.models:
            return model_name
        # 通过 model id 查找
        for key, spec in self.models.items():
            if spec.id == model_name or key == model_name:
                return key
        # v1 别名映射
        v1_alias_map = {"opus": "claude-opus-4-7", "sonnet": "claude-sonnet-4-6", "haiku": "claude-haiku-4-5"}
        if model_name in v1_alias_map:
            return self._resolve_model_key(v1_alias_map[model_name])
        # 返回第一个可用模型
        if self.models:
            return next(iter(self.models))
        return model_name

    def _lookup_model_spec(self, model_name: str) -> ModelSpec:
        """查找模型规格，带降级"""
        key = self._resolve_model_key(model_name)
        if key in self.models:
            return self.models[key]
        # 兜底：构造一个默认规格
        return ModelSpec(
            id=model_name, display_name=model_name, description="",
            default_effort="high", thinking={"type": "adaptive"},
            cost_per_1m_input=0, cost_per_1m_output=0,
        )

    def route(self, classification: TaskClassification, budget: BudgetLevel = BudgetLevel.NORMAL) -> RoutingDecision:
        """
        根据任务分类和预算选择最优模型。
        """
        # 1. 规则匹配
        matched_rule = None
        for rule in self.routing_rules:
            if self._match_rule(classification, rule):
                matched_rule = rule
                break

        if matched_rule is None:
            fallback_model = "claude-opus-4-7" if "claude-opus-4-7" in self.models else \
                             (next(iter(self.models)) if self.models else "claude-opus-4-7")
            matched_rule = {"name": "fallback", "target": {"model": fallback_model, "effort": "high"}}

        target = matched_rule["target"]
        model_name = target["model"]
        model_key = self._resolve_model_key(model_name)
        model_spec = self._lookup_model_spec(model_name)
        effort = target.get("effort", model_spec.default_effort)

        # 2. 预算调整
        if budget == BudgetLevel.LOW:
            strategy = self.budget_strategies.get("low", {})
            if strategy.get("model_downgrade") or strategy.get("prefer_cheapest"):
                # v2 降级: 按成本排序选更便宜的
                cheap_models = sorted(
                    [(k, s) for k, s in self.models.items() if s.cost_per_1m_output > 0],
                    key=lambda x: x[1].cost_per_1m_output,
                )
                if cheap_models and cheap_models[0][1].cost_per_1m_output < model_spec.cost_per_1m_output:
                    model_key = cheap_models[0][0]
                    model_spec = cheap_models[0][1]
                    model_name = model_spec.id
            if strategy.get("effort_downgrade"):
                effort_map = {"max": "high", "xhigh": "high", "high": "medium", "medium": "low", "low": "low"}
                effort = effort_map.get(effort, effort)

        elif budget == BudgetLevel.HIGH:
            strategy = self.budget_strategies.get("high", {})
            if strategy.get("effort_upgrade") and effort != "max":
                effort_map = {"low": "medium", "medium": "high", "high": "xhigh", "xhigh": "max", "max": "max"}
                effort = effort_map.get(effort, effort)

        # 3. 构造决策
        max_tokens = min(classification.estimated_tokens * 3, 64000)
        max_tokens = max(max_tokens, 4096)

        decision = RoutingDecision(
            model_id=model_spec.id,
            model_display_name=model_name,
            thinking=model_spec.thinking,
            effort=effort,
            max_tokens=max_tokens,
            matched_rule=matched_rule["name"],
            reasoning=f"任务={classification.task_type}, "
                      f"复杂度={classification.complexity.value}, "
                      f"规则={matched_rule['name']}, "
                      f"预算={budget.value} → {model_name}/{effort}",
        )

        logger.info(f"路由决策: {decision.reasoning}")
        return decision


# ============================================================
# 子代理调度器
# ============================================================


class SubagentDispatcher:
    """
    子代理调度器：将任务分发到指定模型的子代理执行。

    关键设计:
    - 子代理接收最小化上下文（不传递主会话全部历史）
    - 支持并行调度多个子代理
    - 超时控制和重试机制
    """

    def __init__(self, client: AsyncAnthropic):
        self.client = client

    def _build_subagent_context(
        self,
        task: str,
        decision: RoutingDecision,
        relevant_code: Optional[str] = None,
        system_instruction: Optional[str] = None,
    ) -> tuple[str, list[MessageParam]]:
        """构造子代理的精准上下文"""

        system_prompt = f"""你是一个专业助手，正在执行一个具体任务。

## 任务类型
{decision.matched_rule}

## 执行要求
- 专注完成指定的任务，不要做额外的事
- 直接输出结果，不需要过多解释
- 如果任务涉及代码，请确保代码正确可用

## 输出格式
{system_instruction or "直接输出任务结果。"}"""

        messages: list[MessageParam] = []

        if relevant_code:
            messages.append({
                "role": "user",
                "content": f"## 相关代码\n\n```\n{relevant_code}\n```\n\n## 任务\n\n{task}",
            })
        else:
            messages.append({"role": "user", "content": task})

        return system_prompt, messages

    async def dispatch(
        self,
        task: str,
        decision: RoutingDecision,
        relevant_code: Optional[str] = None,
        system_instruction: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> SubagentResult:
        """
        分发任务到子代理执行。

        Args:
            task: 任务描述
            decision: 路由决策（含模型和参数）
            relevant_code: 相关代码片段
            system_instruction: 自定义系统指令
            timeout_seconds: 超时时间

        Returns:
            SubagentResult: 子代理执行结果
        """
        task_id = f"subagent_{int(time.time() * 1000)}"
        start_time = time.time()

        system_prompt, messages = self._build_subagent_context(task, decision, relevant_code, system_instruction)

        try:
            response: Message = await asyncio.wait_for(
                self.client.messages.create(
                    model=decision.model_id,
                    max_tokens=decision.max_tokens,
                    system=system_prompt,
                    messages=messages,
                    thinking=decision.thinking,
                    output_config={"effort": decision.effort},
                ),
                timeout=timeout_seconds,
            )

            elapsed = (time.time() - start_time) * 1000
            output = _extract_text(response.content) if response.content else ""

            logger.info(f"子代理完成: {task_id}, 模型={decision.model_display_name}, 耗时={elapsed:.0f}ms, token={response.usage.output_tokens}")

            return SubagentResult(
                task_id=task_id,
                model_used=decision.model_id,
                success=True,
                output=output,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                elapsed_ms=elapsed,
            )

        except asyncio.TimeoutError:
            elapsed = (time.time() - start_time) * 1000
            logger.warning(f"子代理超时: {task_id}, 耗时={elapsed:.0f}ms")
            return SubagentResult(
                task_id=task_id,
                model_used=decision.model_id,
                success=False,
                output="",
                elapsed_ms=elapsed,
                error=f"任务超时 ({timeout_seconds}s)",
            )

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(f"子代理失败: {task_id}, 错误={e}")
            return SubagentResult(
                task_id=task_id,
                model_used=decision.model_id,
                success=False,
                output="",
                elapsed_ms=elapsed,
                error=str(e),
            )

    async def dispatch_parallel(
        self,
        tasks: list[dict],
        decisions: list[RoutingDecision],
    ) -> list[SubagentResult]:
        """
        并行分发多个任务到子代理。

        Args:
            tasks: 任务列表，每个元素是 dispatch() 的 kwargs
            decisions: 对应的路由决策列表

        Returns:
            子代理结果列表
        """
        if len(tasks) != len(decisions):
            raise ValueError(f"任务数({len(tasks)})与决策数({len(decisions)})不匹配")

        coroutines = []
        for i, (task_kwargs, decision) in enumerate(zip(tasks, decisions)):
            coroutines.append(self.dispatch(**task_kwargs, decision=decision))

        results = await asyncio.gather(*coroutines, return_exceptions=True)

        # 处理异常
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                final_results.append(SubagentResult(
                    task_id=f"error_{i}",
                    model_used=decisions[i].model_id,
                    success=False,
                    output="",
                    error=str(result),
                ))
            else:
                final_results.append(result)

        return final_results


# ============================================================
# 主路由器 (统一入口)
# ============================================================


class IntelligentModelRouter:
    """
    智能模型路由器 —— 统一入口

    将 任务分类 → 模型选择 → 子代理调度 → 结果聚合 串联为一条流水线。

    使用示例:

        router = IntelligentModelRouter(api_key="...")

        # 自动路由
        result = await router.execute("设计一个分布式缓存系统")
        print(f"使用模型: {result.model_used}")
        print(f"输出: {result.output}")

        # 并行执行
        results = await router.execute_parallel([
            "审查 auth.py 的安全性",
            "审查 payment.py 的安全性",
            "审查 upload.py 的安全性",
        ])
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        config_path: Optional[str] = None,
        main_model: str = "claude-opus-4-7",
    ):
        self.client = AsyncAnthropic(api_key=api_key)
        self.main_model = main_model
        self.classifier = TaskClassifier(self.client)
        self.router = ModelRouter(config_path)
        self.dispatcher = SubagentDispatcher(self.client)

    async def execute(
        self,
        task: str,
        budget: BudgetLevel = BudgetLevel.NORMAL,
        relevant_code: Optional[str] = None,
        system_instruction: Optional[str] = None,
        timeout_seconds: int = 300,
        skip_classification: bool = False,
        force_model: Optional[str] = None,
    ) -> SubagentResult:
        """
        执行任务 —— 自动分类、路由、分发。

        Args:
            task: 任务描述
            budget: 预算级别
            relevant_code: 相关代码
            system_instruction: 自定义系统指令
            timeout_seconds: 超时时间
            skip_classification: 跳过分类，直接使用默认路由
            force_model: 强制使用指定模型

        Returns:
            SubagentResult: 执行结果
        """
        # 1. 分类
        if skip_classification:
            classification = TaskClassification(
                task_type="unknown",
                complexity=Complexity.MODERATE,
                domain=Domain.CODING,
                needs_deep_reasoning=False,
                estimated_tokens=4000,
            )
        else:
            classification = await self.classifier.classify(task)

        # 2. 路由
        if force_model:
            decision = RoutingDecision(
                model_id=force_model,
                model_display_name=force_model,
                thinking={"type": "adaptive"},
                effort="high",
                max_tokens=64000,
                matched_rule="forced",
                reasoning=f"强制指定模型: {force_model}",
            )
        else:
            decision = self.router.route(classification, budget)

        # 3. 分发执行
        result = await self.dispatcher.dispatch(
            task=task,
            decision=decision,
            relevant_code=relevant_code,
            system_instruction=system_instruction,
            timeout_seconds=timeout_seconds,
        )

        # 4. 附加分类信息
        result.task_id = f"{result.task_id}|type={classification.task_type}|model={decision.model_display_name}"

        return result

    async def execute_parallel(
        self,
        tasks: list[str],
        budget: BudgetLevel = BudgetLevel.NORMAL,
        timeout_seconds: int = 300,
    ) -> list[SubagentResult]:
        """
        并行执行多个任务。

        Args:
            tasks: 任务描述列表
            budget: 预算级别
            timeout_seconds: 超时时间

        Returns:
            执行结果列表
        """
        # 1. 并行分类
        classifications = await asyncio.gather(*[
            self.classifier.classify(task) for task in tasks
        ])

        # 2. 路由决策
        decisions = [self.router.route(c, budget) for c in classifications]

        # 3. 构造任务参数
        task_kwargs_list = [
            {"task": task, "timeout_seconds": timeout_seconds}
            for task in tasks
        ]

        # 4. 并行分发
        results = await self.dispatcher.dispatch_parallel(task_kwargs_list, decisions)

        # 5. 输出路由摘要
        for task, decision, result in zip(tasks, decisions, results):
            logger.info(f"[{decision.model_display_name}/{decision.effort}] "
                        f"{task[:60]}... → {'✓' if result.success else '✗'} "
                        f"({result.elapsed_ms:.0f}ms)")

        return results


# ============================================================
# 便捷函数
# ============================================================


async def route_and_execute(
    task: str,
    api_key: Optional[str] = None,
    budget: str = "normal",
    force_model: Optional[str] = None,
) -> SubagentResult:
    """
    便捷函数：一键路由并执行任务。

    Args:
        task: 任务描述
        api_key: Anthropic API Key
        budget: 预算级别 ("high", "normal", "low")
        force_model: 强制指定模型

    Returns:
        SubagentResult
    """
    router = IntelligentModelRouter(api_key=api_key)
    return await router.execute(
        task=task,
        budget=BudgetLevel(budget),
        force_model=force_model,
    )


# ============================================================
# 使用示例
# ============================================================

async def _example():
    """演示用法"""
    router = IntelligentModelRouter()

    # 示例 1: 架构设计 → 自动路由到 Opus
    result = await router.execute(
        "设计一个支持千万级 QPS 的分布式消息队列架构，需要考虑高可用和容灾"
    )
    print(f"[{result.model_used}] {result.output[:200]}...")

    # 示例 2: 简单编辑 → 自动路由到 Haiku
    result = await router.execute(
        "把 config.py 里的 max_connections 变量名改为 max_conns"
    )
    print(f"[{result.model_used}] {result.output[:200]}...")

    # 示例 3: 并行审查
    results = await router.execute_parallel([
        "审查 auth.py 的安全漏洞",
        "审查 payment.py 的事务处理",
        "审查 cache.py 的缓存策略",
    ])
    for r in results:
        print(f"[{r.model_used}] {'✓' if r.success else '✗'} {r.elapsed_ms:.0f}ms")

    # 示例 4: 强制指定模型
    result = await router.execute(
        "写一个快速排序的实现", force_model="claude-haiku-4-5"
    )

    # 示例 5: 低成本模式
    result = await router.execute(
        "给这个函数写注释", budget=BudgetLevel.LOW
    )


if __name__ == "__main__":
    asyncio.run(_example())
