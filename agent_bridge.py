"""
智能体通用桥接层 (Agent-Agnostic Bridge)
=========================================

支持任意智能体框架的统一调度接口。

设计理念:
  所有智能体框架本质上都是 "接收任务 → 调用 LLM → 使用工具 → 返回结果"。
  本桥接层在 LLM 调用层面拦截，将请求路由到最优模型，对智能体框架完全透明。

支持的智能体框架:
  - LangChain / LangGraph
  - CrewAI
  - AutoGen / AutoGen Studio
  - Microsoft Semantic Kernel
  - OpenAI Agents SDK / Swarm
  - Anthropic Managed Agents
  - Custom agents (任何实现了 call_llm 接口的代理)
  - Claude Code agents (通过 Agent 工具)

核心机制:
  1. OpenAI 兼容代理端点 → 任何支持 base_url 配置的框架都可接入
  2. 模型别名 → 将 "router:auto" 映射为实际的最优模型
  3. 请求拦截中间件 → 在 LLM 调用前进行任务分类和模型重定向
  4. 回调钩子 → 在关键节点注入自定义逻辑
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional, Protocol, TypeVar

logger = logging.getLogger(__name__)


# ============================================================
# 通用智能体类型
# ============================================================

class AgentFramework(str, Enum):
    """智能体框架标识"""
    LANGCHAIN = "langchain"
    LANGGRAPH = "langgraph"
    CREWAI = "crewai"
    AUTOGEN = "autogen"
    OPENAI_AGENTS = "openai_agents"
    ANTHROPIC_MANAGED = "anthropic_managed"
    SEMANTIC_KERNEL = "semantic_kernel"
    CLAUDE_CODE = "claude_code"
    CUSTOM = "custom"


@dataclass
class AgentConfig:
    """智能体配置"""
    framework: AgentFramework
    name: str
    description: str = ""
    model: str = "router:auto"  # "router:auto" 表示自动路由
    tools: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    max_iterations: int = 10
    temperature: Optional[float] = None
    budget: str = "normal"  # high, normal, low
    allow_fallback: bool = True
    timeout_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentEvent:
    """智能体事件"""
    event_type: str  # task_start, llm_call, tool_call, task_end, error
    agent_name: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)


# ============================================================
# LLM 调用接口 (所有智能体框架最终都调用这个)
# ============================================================

class LLMCallable(Protocol):
    """LLM 调用协议 —— 所有智能体框架都遵循此模式"""

    async def __call__(
        self,
        messages: list[dict],
        model: str,
        tools: Optional[list[dict]] = None,
        **kwargs,
    ) -> dict:
        ...


@dataclass
class LLMCallRequest:
    """标准化的 LLM 调用请求"""
    messages: list[dict]
    model: str
    tools: Optional[list[dict]] = None
    tool_choice: Optional[str] = None
    max_tokens: int = 4096
    temperature: Optional[float] = None
    stream: bool = False
    call_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class LLMCallResponse:
    """标准化的 LLM 调用响应"""
    content: str
    model: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"


# ============================================================
# 路由器中间件 (核心)
# ============================================================

class RouterMiddleware:
    """
    路由中间件 —— 在 LLM 调用前拦截并重定向到最优模型。

    这是实现 "对智能体透明" 的关键：智能体认为自己调用的是某个模型，
    实际上中间件将其重定向到最优模型。

    支持自动交叉验证：设置 verify_mode=True 后，每次 LLM 调用的产出物
    会经多模型交叉验证，消除幻觉和编造。
    """

    def __init__(
        self,
        router: Any = None,
        provider_registry: Any = None,
        verify_mode: bool = False,
        verify_models: Optional[list[str]] = None,
    ):
        self.router = router
        self.providers = provider_registry
        self.verify_mode = verify_mode
        self.verify_models = verify_models
        self._call_history: list[dict] = []
        self._event_hooks: list[Callable[[AgentEvent], None]] = []
        self._last_verification: Optional[dict] = None

    def on_event(self, hook: Callable[[AgentEvent], None]):
        """注册事件钩子"""
        self._event_hooks.append(hook)

    async def _emit_event(self, event: AgentEvent):
        for hook in self._event_hooks:
            try:
                hook(event)
            except Exception as e:
                logger.warning(f"事件钩子异常: {e}")

    async def intercept(
        self,
        request: LLMCallRequest,
        agent_config: Optional[AgentConfig] = None,
    ) -> LLMCallResponse:
        """
        拦截 LLM 调用并路由到最优模型。

        Args:
            request: 标准化的 LLM 请求
            agent_config: 智能体配置（可选，用于预算控制等）

        Returns:
            LLMCallResponse: 模型响应
        """
        # 1. 检查是否需要路由
        effective_model = request.model
        if request.model == "router:auto" or request.model.startswith("router:"):
            effective_model = await self._resolve_model(request, agent_config)

        # 2. 发出事件
        await self._emit_event(AgentEvent(
            event_type="llm_call",
            agent_name=agent_config.name if agent_config else "unknown",
            data={
                "original_model": request.model,
                "effective_model": effective_model,
                "message_count": len(request.messages),
                "has_tools": request.tools is not None,
            },
        ))

        # 3. 执行调用（通过提供商适配器）
        start_time = time.time()

        try:
            if self.providers:
                provider = self.providers.resolve_provider(effective_model)
                adapter = self.providers.get_adapter(provider)

                from providers import UnifiedMessage, UnifiedRequest

                unified = UnifiedRequest(
                    messages=[UnifiedMessage(role=m["role"], content=m["content"]) for m in request.messages],
                    model=effective_model,
                    max_tokens=request.max_tokens,
                    temperature=request.temperature,
                    tools=request.tools,
                )
                unified_resp = await adapter.chat(unified)

                response = LLMCallResponse(
                    content=unified_resp.content,
                    model=effective_model,
                    tool_calls=unified_resp.tool_calls,
                    usage=unified_resp.usage,
                    finish_reason=unified_resp.finish_reason,
                )
            else:
                # 降级：直接调用 Anthropic API
                response = await self._direct_anthropic_call(request, effective_model)

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            await self._emit_event(AgentEvent(
                event_type="error",
                agent_name=agent_config.name if agent_config else "unknown",
                data={"error": str(e), "model": effective_model},
            ))
            raise

        elapsed = (time.time() - start_time) * 1000

        # 4. 交叉验证 (如果启用)
        if self.verify_mode and response.content and len(response.content) > 50:
            try:
                from verifier import CrossVerifier
                verifier = CrossVerifier(
                    api_key=getattr(self.providers, '_api_key', None),
                    verification_models=self.verify_models,
                )
                vreport = await verifier.verify_text(response.content)
                self._last_verification = {
                    "overall_score": vreport.overall_score,
                    "hallucination_rate": vreport.hallucination_rate,
                    "summary": vreport.summary,
                    "verified_by": vreport.verified_by,
                    "flagged_count": sum(1 for c in vreport.claims if c.flagged),
                    "risk_flags": vreport.risk_flags,
                }
                # 将验证结果追加到响应
                if vreport.hallucination_rate > 0.3:
                    response.content += f"\n\n⚠️ [Verified] Credibility: {vreport.overall_score:.0%}. {len(vreport.risk_flags)} issues found."
                await self._emit_event(AgentEvent(
                    event_type="verification_complete",
                    agent_name=agent_config.name if agent_config else "unknown",
                    data=self._last_verification,
                ))
            except Exception as ve:
                logger.warning(f"验证跳过: {ve}")
                self._last_verification = None

        # 5. 记录历史
        self._call_history.append({
            "call_id": request.call_id,
            "model": effective_model,
            "tokens": response.usage.get("total_tokens", 0),
            "elapsed_ms": elapsed,
            "timestamp": time.time(),
        })

        await self._emit_event(AgentEvent(
            event_type="task_end",
            agent_name=agent_config.name if agent_config else "unknown",
            data={"model": effective_model, "elapsed_ms": elapsed, "tokens": response.usage},
        ))

        return response

    async def _resolve_model(
        self,
        request: LLMCallRequest,
        agent_config: Optional[AgentConfig] = None,
    ) -> str:
        """根据请求内容自动选择最优模型"""
        # 提取用户最后一条消息作为任务描述
        user_messages = [m for m in request.messages if m.get("role") == "user"]
        task = user_messages[-1]["content"] if user_messages else ""

        # 如果有路由器，使用路由器进行任务分类和模型选择
        if self.router:
            try:
                result = await self.router.execute(
                    task=str(task)[:500],
                    skip_classification=False,
                    force_model=None,
                )
                return result.model_used
            except Exception as e:
                logger.warning(f"自动路由失败: {e}，使用默认模型")

        # 降级逻辑：根据工具和消息复杂度推断
        if request.tools and len(request.tools) > 5:
            return "claude-opus-4-7"
        elif len(request.messages) > 10:
            return "claude-sonnet-4-6"
        else:
            return "claude-haiku-4-5"

    async def _direct_anthropic_call(self, request: LLMCallRequest, model: str) -> LLMCallResponse:
        """直接调用 Anthropic API（降级方案）"""
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic()

            system_msg = None
            messages = []
            for m in request.messages:
                if m["role"] == "system":
                    system_msg = m["content"]
                else:
                    messages.append({"role": m["role"], "content": m["content"]})

            kwargs = {
                "model": model,
                "max_tokens": request.max_tokens,
                "messages": messages,
            }
            if system_msg:
                kwargs["system"] = system_msg
            if request.tools:
                kwargs["tools"] = request.tools

            resp = await client.messages.create(**kwargs)

            # 安全提取文本（跳过 thinking blocks）
            content = ""
            for block in (resp.content or []):
                if hasattr(block, 'text'):
                    content += block.text
                elif isinstance(block, dict) and block.get('type') == 'text':
                    content += block.get('text', '')
            return LLMCallResponse(
                content=content,
                model=model,
                usage={
                    "input_tokens": resp.usage.input_tokens,
                    "output_tokens": resp.usage.output_tokens,
                },
            )
        except ImportError:
            raise RuntimeError("需要安装 anthropic 包: pip install anthropic")

    def get_stats(self) -> dict:
        """获取调用统计"""
        if not self._call_history:
            return {"total_calls": 0}

        total_tokens = sum(c["tokens"] for c in self._call_history)
        total_time = sum(c["elapsed_ms"] for c in self._call_history)
        model_counts = {}
        for c in self._call_history:
            model = c["model"]
            model_counts[model] = model_counts.get(model, 0) + 1

        return {
            "total_calls": len(self._call_history),
            "total_tokens": total_tokens,
            "avg_time_ms": total_time / len(self._call_history),
            "model_distribution": model_counts,
        }


# ============================================================
# 框架适配器
# ============================================================

class FrameworkAdapter(ABC):
    """
    框架适配器基类 —— 将各种智能体框架接入统一路由。

    每个具体适配器负责:
      1. 拦截该框架的 LLM 调用
      2. 转换为标准化的 LLMCallRequest
      3. 通过 RouterMiddleware 路由到最优模型
      4. 将响应转换回框架原生格式
    """

    def __init__(self, middleware: RouterMiddleware, agent_config: AgentConfig):
        self.middleware = middleware
        self.agent_config = agent_config

    @abstractmethod
    def wrap(self, agent: Any) -> Any:
        """包装智能体，注入路由能力"""
        ...


class LangChainAdapter(FrameworkAdapter):
    """
    LangChain / LangGraph 适配器

    通过自定义 ChatModel 包装器实现透明路由。
    """

    def wrap(self, agent: Any) -> Any:
        """为 LangChain agent 注入模型路由"""
        adapter = self

        class RoutedChatModel:
            """实现 LangChain BaseChatModel 接口的路由包装器"""

            def __init__(self):
                self._middleware = adapter.middleware
                self._config = adapter.agent_config

            async def _agenerate(self, messages, stop=None, **kwargs):
                # 转换为标准请求
                request = LLMCallRequest(
                    messages=[{"role": _get_lc_role(m), "content": _get_lc_content(m)} for m in messages],
                    model="router:auto",
                    tools=kwargs.get("tools"),
                    max_tokens=kwargs.get("max_tokens", 4096),
                )
                response = await self._middleware.intercept(request, self._config)

                # 转换回 LangChain 格式
                from langchain_core.messages import AIMessage
                return type('GenerationResult', (), {
                    'generations': [[type('Generation', (), {
                        'message': AIMessage(content=response.content),
                        'text': response.content,
                    })()]],
                })()

            def invoke(self, messages, **kwargs):
                import asyncio
                return asyncio.run(self._agenerate(messages, **kwargs))

            async def ainvoke(self, messages, **kwargs):
                return await self._agenerate(messages, **kwargs)

        # 注入路由模型
        if hasattr(agent, 'llm'):
            agent._original_llm = agent.llm
            agent.llm = RoutedChatModel()
        return agent


def _get_lc_role(msg) -> str:
    """从 LangChain 消息提取角色"""
    type_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
    msg_type = getattr(msg, 'type', 'human')
    return type_map.get(msg_type, "user")


def _get_lc_content(msg) -> str:
    """从 LangChain 消息提取内容"""
    return getattr(msg, 'content', str(msg))


class CrewAIAdapter(FrameworkAdapter):
    """CrewAI 适配器 —— 拦截 CrewAI agent 的 LLM 调用"""

    def wrap(self, agent: Any) -> Any:
        """为 CrewAI agent 注入模型路由"""
        adapter = self

        original_execute = agent.execute_task

        async def routed_execute(task, context=None, tools=None):
            # 在任务执行前进行路由决策
            if adapter.agent_config.model == "router:auto":
                request = LLMCallRequest(
                    messages=[{"role": "user", "content": str(task)}],
                    model="router:auto",
                    tools=[{"name": t.name, "description": t.description} for t in (tools or [])],
                )
                try:
                    response = await adapter.middleware.intercept(request, adapter.agent_config)
                    logger.info(f"CrewAI agent 路由: {response.model}")
                except Exception:
                    pass

            return await original_execute(task, context, tools)

        agent.execute_task = routed_execute
        return agent


class AutoGenAdapter(FrameworkAdapter):
    """AutoGen 适配器"""

    def wrap(self, agent: Any) -> Any:
        """为 AutoGen agent 注入模型路由"""
        adapter = self

        # AutoGen agent 使用 model_client 进行 LLM 调用
        if hasattr(agent, '_model_client'):
            original_create = agent._model_client.create

            async def routed_create(messages, **kwargs):
                request = LLMCallRequest(
                    messages=[{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages],
                    model="router:auto",
                    tools=kwargs.get("tools"),
                )
                try:
                    response = await adapter.middleware.intercept(request, adapter.agent_config)
                    # 返回 AutoGen 兼容格式
                    return type('ModelResult', (), {
                        'content': response.content,
                        'usage': type('Usage', (), {
                            'prompt_tokens': response.usage.get('input_tokens', 0),
                            'completion_tokens': response.usage.get('output_tokens', 0),
                        })(),
                    })()
                except Exception:
                    return await original_create(messages, **kwargs)

            agent._model_client.create = routed_create

        return agent


class GenericAdapter(FrameworkAdapter):
    """
    通用适配器 —— 通过 Monkey-patch 方式适配任何智能体框架。

    原理: 在智能体的 LLM 调用方法上注入路由逻辑。
    """

    def __init__(self, middleware: RouterMiddleware, agent_config: AgentConfig, llm_call_attr: str = "call_llm"):
        super().__init__(middleware, agent_config)
        self.llm_call_attr = llm_call_attr

    def wrap(self, agent: Any) -> Any:
        """通用包装"""
        adapter = self

        if hasattr(agent, self.llm_call_attr):
            original_call = getattr(agent, self.llm_call_attr)

            async def routed_call(prompt, **kwargs):
                if adapter.agent_config.model == "router:auto":
                    request = LLMCallRequest(
                        messages=[{"role": "user", "content": prompt}],
                        model="router:auto",
                    )
                    try:
                        response = await adapter.middleware.intercept(request, adapter.agent_config)
                        return response.content
                    except Exception:
                        pass
                return await original_call(prompt, **kwargs)

            setattr(agent, self.llm_call_attr, routed_call)

        return agent


# ============================================================
# 智能体注册中心
# ============================================================

class AgentRegistry:
    """智能体注册中心 —— 管理所有已注册的智能体和它们的路由配置"""

    def __init__(self, middleware: RouterMiddleware):
        self.middleware = middleware
        self._agents: dict[str, tuple[Any, AgentConfig]] = {}
        self._adapters: dict[AgentFramework, type[FrameworkAdapter]] = {
            AgentFramework.LANGCHAIN: LangChainAdapter,
            AgentFramework.CREWAI: CrewAIAdapter,
            AgentFramework.AUTOGEN: AutoGenAdapter,
            AgentFramework.CUSTOM: GenericAdapter,
        }

    def register(
        self,
        agent: Any,
        config: AgentConfig,
        framework: Optional[AgentFramework] = None,
    ) -> Any:
        """
        注册智能体并注入路由能力。

        Args:
            agent: 智能体实例
            config: 智能体配置
            framework: 框架类型（自动检测如果未指定）

        Returns:
            包装后的智能体（已注入路由能力）
        """
        if framework is None:
            framework = self._detect_framework(agent)

        adapter_cls = self._adapters.get(framework, GenericAdapter)
        adapter = adapter_cls(self.middleware, config)
        wrapped_agent = adapter.wrap(agent)

        self._agents[config.name] = (wrapped_agent, config)
        logger.info(f"智能体已注册: {config.name} (框架={framework}, 模型策略={config.model})")

        return wrapped_agent

    def _detect_framework(self, agent: Any) -> AgentFramework:
        """自动检测智能体框架类型"""
        module_name = type(agent).__module__

        if "langchain" in module_name or "langgraph" in module_name:
            return AgentFramework.LANGCHAIN
        if "crewai" in module_name:
            return AgentFramework.CREWAI
        if "autogen" in module_name:
            return AgentFramework.AUTOGEN
        if "semantic_kernel" in module_name:
            return AgentFramework.SEMANTIC_KERNEL
        if "openai.agents" in module_name:
            return AgentFramework.OPENAI_AGENTS

        return AgentFramework.CUSTOM

    def get_agent(self, name: str) -> Optional[Any]:
        entry = self._agents.get(name)
        return entry[0] if entry else None

    def list_agents(self) -> list[dict]:
        return [
            {"name": name, "framework": config.framework, "model_strategy": config.model}
            for name, (_, config) in self._agents.items()
        ]


# ============================================================
# OpenAI 兼容端点 (关键!)
# ============================================================

"""
提供 OpenAI 兼容的 API 端点，让任何支持 base_url 配置的智能体框架
都能无缝接入模型路由。

使用方式:
  在智能体框架的配置中设置:
    base_url = "http://localhost:8700/v1"
    api_key = "router-key"
    model = "router:auto"  # 自动路由

框架会自动将所有 LLM 调用发往本网关，由网关进行任务分类和路由。
"""


# ============================================================
# 便捷函数
# ============================================================

def create_routed_agent(
    agent: Any,
    name: str = "default",
    model_strategy: str = "router:auto",
    middleware: Optional[RouterMiddleware] = None,
    budget: str = "normal",
) -> Any:
    """
    一键创建已路由的智能体。

    Args:
        agent: 原始智能体实例
        name: 智能体名称
        model_strategy: 模型策略 ("router:auto" | "claude-opus-4-7" | 等)
        middleware: 路由器中间件（自动创建如果为 None）
        budget: 预算级别

    Returns:
        已注入路由能力的智能体

    Example:
        from langchain.agents import create_openai_agent
        from agent_bridge import create_routed_agent

        raw_agent = create_openai_agent(llm, tools, prompt)
        smart_agent = create_routed_agent(raw_agent, name="code-reviewer")
        # smart_agent 现在会自动路由到最优模型
    """
    if middleware is None:
        middleware = RouterMiddleware()

    config = AgentConfig(
        framework=AgentFramework.CUSTOM,
        name=name,
        model=model_strategy,
        budget=budget,
    )

    registry = AgentRegistry(middleware)
    return registry.register(agent, config)


# ============================================================
# 示例
# ============================================================

async def _example_langchain():
    """LangChain 集成示例"""
    print("=== LangChain 集成 ===")

    middleware = RouterMiddleware()
    config = AgentConfig(
        framework=AgentFramework.LANGCHAIN,
        name="code-reviewer",
        model="router:auto",
        system_prompt="你是一个资深的代码审查专家",
    )

    # 在 LangChain 中：
    # from langchain.agents import create_agent
    # raw_agent = create_agent(llm, tools, system_prompt)
    # routed_agent = LangChainAdapter(middleware, config).wrap(raw_agent)
    # result = await routed_agent.ainvoke({"input": "审查这段代码"})


async def _example_crewai():
    """CrewAI 集成示例"""
    print("=== CrewAI 集成 ===")

    middleware = RouterMiddleware()
    config = AgentConfig(
        framework=AgentFramework.CREWAI,
        name="dev-team-lead",
        model="router:auto",
    )

    # 在 CrewAI 中：
    # from crewai import Agent
    # dev_agent = Agent(role="Tech Lead", goal="...", backstory="...")
    # smart_agent = CrewAIAdapter(middleware, config).wrap(dev_agent)


async def _example_openai_compatible():
    """OpenAI 兼容端点示例 —— 最通用的方式"""
    print("=== OpenAI 兼容端点 ===")
    print("""
    在任何智能体框架中配置:

    # 1. LangChain
    from langchain_openai import ChatOpenAI
    llm = ChatOpenAI(
        base_url="http://localhost:8700/v1",
        api_key="router-key",
        model="router:auto",
    )

    # 2. AutoGen
    agent_config = {
        "config_list": [{
            "api_type": "openai",
            "base_url": "http://localhost:8700/v1",
            "api_key": "router-key",
            "model": "router:auto",
        }]
    }

    # 3. CrewAI
    agent = Agent(
        llm={"model": "router:auto", "base_url": "http://localhost:8700/v1"}
    )
    """)


if __name__ == "__main__":
    asyncio.run(_example_openai_compatible())
