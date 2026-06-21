/**
 * 智能模型路由器 (Intelligent Model Router) — TypeScript 实现
 * ============================================================
 *
 * 自动检测任务类型 → 选择最优模型 → 分发到子代理 → 结果回归主模型。
 *
 * 核心原则:
 *  - 主模型 (Orchestrator) 保持不变，维护完整会话上下文
 *  - 子任务通过子代理分发到最优模型
 *  - 子代理接收最小化、精准的上下文
 *  - 完成后结果无缝回归主会话
 *
 * 依赖: @anthropic-ai/sdk >= 0.45.0, yaml >= 2.0
 */

import { Anthropic } from "@anthropic-ai/sdk";
import * as fs from "fs";
import * as path from "path";
import * as yaml from "yaml";

// ============================================================
// 类型定义
// ============================================================

type Complexity = "simple" | "moderate" | "complex" | "very_complex";

type Domain =
  | "coding"
  | "architecture"
  | "review"
  | "testing"
  | "docs"
  | "research"
  | "data_analysis"
  | "conversation";

type BudgetLevel = "high" | "normal" | "low";

type ThinkingConfig =
  | { type: "adaptive" }
  | { type: "disabled" };

interface TaskClassification {
  taskType: string;
  complexity: Complexity;
  domain: Domain;
  needsDeepReasoning: boolean;
  estimatedTokens: number;
  tags: string[];
  confidence: number;
  reasoning: string;
}

interface RoutingDecision {
  modelId: string;
  modelDisplayName: string;
  thinking: ThinkingConfig;
  effort: string;
  maxTokens: number;
  matchedRule: string;
  reasoning: string;
}

interface SubagentResult {
  taskId: string;
  modelUsed: string;
  success: boolean;
  output: string;
  usage: { inputTokens: number; outputTokens: number };
  elapsedMs: number;
  error?: string;
}

interface ModelSpec {
  id: string;
  displayName: string;
  description: string;
  defaultEffort: string;
  thinking: ThinkingConfig;
  costPer1mInput: number;
  costPer1mOutput: number;
}

interface RoutingRule {
  name: string;
  priority: number;
  conditions: Record<string, unknown>;
  target: { model: string; effort?: string };
}

interface RouterConfig {
  models: Record<string, {
    id: string;
    description: string;
    default_effort: string;
    thinking: string;
    cost_per_1m_input: number;
    cost_per_1m_output: number;
  }>;
  routing_rules: RoutingRule[];
  budget_strategies?: Record<string, Record<string, boolean>>;
}

// ============================================================
// 任务分类器
// ============================================================

const CLASSIFIER_SYSTEM_PROMPT = `你是一个任务分类专家。分析用户的任务，输出 JSON 格式的分类结果。

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
}`;

class TaskClassifier {
  private client: Anthropic;
  private classifierModel: string;

  constructor(client: Anthropic, classifierModel: string = "claude-haiku-4-5") {
    this.client = client;
    this.classifierModel = classifierModel;
  }

  async classify(task: string): Promise<TaskClassification> {
    const response = await this.client.messages.create({
      model: this.classifierModel,
      max_tokens: 512,
      system: CLASSIFIER_SYSTEM_PROMPT,
      messages: [{ role: "user", content: `请分类以下任务:\n\n${task}` }],
    });

    const text = response.content[0].type === "text" ? response.content[0].text : "";
    const data = JSON.parse(text);

    console.log(
      `任务分类: ${data.task_type} (复杂度=${data.complexity}, 领域=${data.domain}, 置信度=${data.confidence})`
    );

    return {
      taskType: data.task_type,
      complexity: data.complexity as Complexity,
      domain: data.domain as Domain,
      needsDeepReasoning: data.needs_deep_reasoning,
      estimatedTokens: data.estimated_tokens,
      tags: data.tags || [],
      confidence: data.confidence,
      reasoning: data.reasoning,
    };
  }
}

// ============================================================
// 模型路由器
// ============================================================

class ModelRouter {
  private models: Map<string, ModelSpec> = new Map();
  private routingRules: RoutingRule[] = [];
  private budgetStrategies: Record<string, Record<string, boolean>> = {};

  constructor(configPath?: string) {
    this.loadConfig(configPath);
  }

  private loadConfig(configPath?: string): void {
    let config: RouterConfig;

    if (configPath && fs.existsSync(configPath)) {
      const content = fs.readFileSync(configPath, "utf-8");
      config = yaml.parse(content);
    } else if (fs.existsSync(path.join(__dirname, "config.yaml"))) {
      const content = fs.readFileSync(path.join(__dirname, "config.yaml"), "utf-8");
      config = yaml.parse(content);
    } else {
      config = this.defaultConfig();
    }

    // 解析模型定义
    for (const [name, spec] of Object.entries(config.models)) {
      this.models.set(name, {
        id: spec.id,
        displayName: name,
        description: spec.description,
        defaultEffort: spec.default_effort,
        thinking: spec.thinking === "disabled"
          ? { type: "disabled" }
          : { type: "adaptive" },
        costPer1mInput: spec.cost_per_1m_input,
        costPer1mOutput: spec.cost_per_1m_output,
      });
    }

    // 按优先级降序排列规则
    this.routingRules = [...config.routing_rules].sort(
      (a, b) => b.priority - a.priority
    );
    this.budgetStrategies = config.budget_strategies || {};
  }

  private defaultConfig(): RouterConfig {
    return {
      models: {
        opus: {
          id: "claude-opus-4-7",
          description: "最强推理模型",
          default_effort: "high",
          thinking: "adaptive",
          cost_per_1m_input: 15.0,
          cost_per_1m_output: 75.0,
        },
        sonnet: {
          id: "claude-sonnet-4-6",
          description: "平衡性能与成本",
          default_effort: "high",
          thinking: "adaptive",
          cost_per_1m_input: 3.0,
          cost_per_1m_output: 15.0,
        },
        haiku: {
          id: "claude-haiku-4-5",
          description: "最快最省",
          default_effort: "low",
          thinking: "disabled",
          cost_per_1m_input: 0.8,
          cost_per_1m_output: 4.0,
        },
      },
      routing_rules: [
        { name: "architecture", priority: 100, conditions: { domain: "architecture" }, target: { model: "opus", effort: "max" } },
        { name: "review", priority: 90, conditions: { domain: "review" }, target: { model: "opus", effort: "high" } },
        { name: "complex_coding", priority: 80, conditions: { domain: "coding", complexity: ["complex", "very_complex"] }, target: { model: "opus", effort: "high" } },
        { name: "moderate_coding", priority: 70, conditions: { domain: "coding", complexity: "moderate" }, target: { model: "sonnet", effort: "high" } },
        { name: "testing", priority: 60, conditions: { domain: "testing" }, target: { model: "sonnet", effort: "medium" } },
        { name: "docs", priority: 60, conditions: { domain: "docs" }, target: { model: "sonnet", effort: "medium" } },
        { name: "research", priority: 60, conditions: { domain: "research" }, target: { model: "sonnet", effort: "high" } },
        { name: "simple", priority: 50, conditions: { complexity: "simple" }, target: { model: "haiku", effort: "low" } },
        { name: "default", priority: 0, conditions: {}, target: { model: "opus", effort: "high" } },
      ],
    };
  }

  private matchRule(classification: TaskClassification, rule: RoutingRule): boolean {
    const conditions = rule.conditions;
    if (Object.keys(conditions).length === 0) return true;

    for (const [key, expected] of Object.entries(conditions)) {
      const actual = (classification as Record<string, unknown>)[key];

      if (Array.isArray(expected)) {
        if (Array.isArray(actual)) {
          if (!expected.some((e) => actual.includes(e as string))) return false;
        } else if (!expected.includes(actual as string)) {
          return false;
        }
      } else if (actual !== expected) {
        return false;
      }
    }

    return true;
  }

  route(classification: TaskClassification, budget: BudgetLevel = "normal"): RoutingDecision {
    // 1. 规则匹配
    let matchedRule: RoutingRule | undefined;
    for (const rule of this.routingRules) {
      if (this.matchRule(classification, rule)) {
        matchedRule = rule;
        break;
      }
    }

    if (!matchedRule) {
      matchedRule = { name: "fallback", priority: 0, conditions: {}, target: { model: "opus", effort: "high" } };
    }

    const target = matchedRule.target;
    let modelName = target.model;
    let effort = target.effort || this.models.get(modelName)!.defaultEffort;

    // 2. 预算调整
    if (budget === "low") {
      const strategy = this.budgetStrategies.low || {};
      if (strategy.model_downgrade) {
        const downgradeMap: Record<string, string> = { opus: "sonnet", sonnet: "haiku", haiku: "haiku" };
        modelName = downgradeMap[modelName] || modelName;
      }
      if (strategy.effort_downgrade) {
        const effortMap: Record<string, string> = { max: "high", xhigh: "high", high: "medium", medium: "low", low: "low" };
        effort = effortMap[effort] || effort;
      }
    } else if (budget === "high") {
      const strategy = this.budgetStrategies.high || {};
      if (strategy.effort_upgrade && effort !== "max") {
        const effortMap: Record<string, string> = { low: "medium", medium: "high", high: "xhigh", xhigh: "max", max: "max" };
        effort = effortMap[effort] || effort;
      }
    }

    const modelSpec = this.models.get(modelName)!;

    // 3. 构造决策
    const maxTokens = Math.min(classification.estimatedTokens * 3, 64000);

    const reasoning =
      `任务=${classification.taskType}, ` +
      `复杂度=${classification.complexity}, ` +
      `规则=${matchedRule.name}, ` +
      `预算=${budget} → ${modelName}/${effort}`;

    console.log(`路由决策: ${reasoning}`);

    return {
      modelId: modelSpec.id,
      modelDisplayName: modelName,
      thinking: modelSpec.thinking,
      effort,
      maxTokens: Math.max(maxTokens, 4096),
      matchedRule: matchedRule.name,
      reasoning,
    };
  }
}

// ============================================================
// 子代理调度器
// ============================================================

class SubagentDispatcher {
  private client: Anthropic;

  constructor(client: Anthropic) {
    this.client = client;
  }

  private buildSubagentContext(
    task: string,
    decision: RoutingDecision,
    relevantCode?: string,
    systemInstruction?: string
  ): { systemPrompt: string; messages: Anthropic.MessageParam[] } {
    const systemPrompt = `你是一个专业助手，正在执行一个具体任务。

## 任务类型
${decision.matchedRule}

## 执行要求
- 专注完成指定的任务，不要做额外的事
- 直接输出结果，不需要过多解释
- 如果任务涉及代码，请确保代码正确可用

## 输出格式
${systemInstruction || "直接输出任务结果。"}`;

    const messages: Anthropic.MessageParam[] = [];

    if (relevantCode) {
      messages.push({
        role: "user",
        content: `## 相关代码\n\n\`\`\`\n${relevantCode}\n\`\`\`\n\n## 任务\n\n${task}`,
      });
    } else {
      messages.push({ role: "user", content: task });
    }

    return { systemPrompt, messages };
  }

  async dispatch(
    task: string,
    decision: RoutingDecision,
    relevantCode?: string,
    systemInstruction?: string,
    timeoutSeconds: number = 300
  ): Promise<SubagentResult> {
    const taskId = `subagent_${Date.now()}`;
    const startTime = Date.now();

    const { systemPrompt, messages } = this.buildSubagentContext(
      task, decision, relevantCode, systemInstruction
    );

    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), timeoutSeconds * 1000);

      const response = await this.client.messages.create({
        model: decision.modelId,
        max_tokens: decision.maxTokens,
        system: systemPrompt,
        messages,
        // @ts-expect-error - thinking is a valid parameter in newer SDK versions
        thinking: decision.thinking,
      }, {
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      const elapsed = Date.now() - startTime;
      const output = response.content[0].type === "text" ? response.content[0].text : "";

      console.log(
        `子代理完成: ${taskId}, 模型=${decision.modelDisplayName}, 耗时=${elapsed}ms, token=${response.usage.output_tokens}`
      );

      return {
        taskId,
        modelUsed: decision.modelId,
        success: true,
        output,
        usage: {
          inputTokens: response.usage.input_tokens,
          outputTokens: response.usage.output_tokens,
        },
        elapsedMs: elapsed,
      };

    } catch (error: unknown) {
      const elapsed = Date.now() - startTime;
      const errorMessage = error instanceof Error ? error.message : String(error);
      const isTimeout = error instanceof DOMException && error.name === "AbortError";

      console.warn(
        `子代理${isTimeout ? "超时" : "失败"}: ${taskId}, 耗时=${elapsed}ms, 错误=${errorMessage}`
      );

      return {
        taskId,
        modelUsed: decision.modelId,
        success: false,
        output: "",
        usage: { inputTokens: 0, outputTokens: 0 },
        elapsedMs: elapsed,
        error: isTimeout ? `任务超时 (${timeoutSeconds}s)` : errorMessage,
      };
    }
  }

  async dispatchParallel(
    tasks: Array<{
      task: string;
      decision: RoutingDecision;
      relevantCode?: string;
      systemInstruction?: string;
      timeoutSeconds?: number;
    }>
  ): Promise<SubagentResult[]> {
    const promises = tasks.map((t) =>
      this.dispatch(t.task, t.decision, t.relevantCode, t.systemInstruction, t.timeoutSeconds)
    );

    return Promise.all(promises);
  }
}

// ============================================================
// 主路由器 (统一入口)
// ============================================================

class IntelligentModelRouter {
  client: Anthropic;
  private mainModel: string;
  private classifier: TaskClassifier;
  private router: ModelRouter;
  private dispatcher: SubagentDispatcher;

  constructor(
    apiKey?: string,
    configPath?: string,
    mainModel: string = "claude-opus-4-7"
  ) {
    this.client = new Anthropic({ apiKey });
    this.mainModel = mainModel;
    this.classifier = new TaskClassifier(this.client);
    this.router = new ModelRouter(configPath);
    this.dispatcher = new SubagentDispatcher(this.client);
  }

  async execute(options: {
    task: string;
    budget?: BudgetLevel;
    relevantCode?: string;
    systemInstruction?: string;
    timeoutSeconds?: number;
    skipClassification?: boolean;
    forceModel?: string;
  }): Promise<SubagentResult> {
    const {
      task,
      budget = "normal",
      relevantCode,
      systemInstruction,
      timeoutSeconds = 300,
      skipClassification = false,
      forceModel,
    } = options;

    // 1. 分类
    let classification: TaskClassification;
    if (skipClassification) {
      classification = {
        taskType: "unknown",
        complexity: "moderate",
        domain: "coding",
        needsDeepReasoning: false,
        estimatedTokens: 4000,
        tags: [],
        confidence: 1.0,
        reasoning: "跳过分类",
      };
    } else {
      classification = await this.classifier.classify(task);
    }

    // 2. 路由
    let decision: RoutingDecision;
    if (forceModel) {
      decision = {
        modelId: forceModel,
        modelDisplayName: forceModel,
        thinking: { type: "adaptive" },
        effort: "high",
        maxTokens: 64000,
        matchedRule: "forced",
        reasoning: `强制指定模型: ${forceModel}`,
      };
    } else {
      decision = this.router.route(classification, budget);
    }

    // 3. 分发执行
    const result = await this.dispatcher.dispatch(
      task, decision, relevantCode, systemInstruction, timeoutSeconds
    );

    result.taskId = `${result.taskId}|type=${classification.taskType}|model=${decision.modelDisplayName}`;

    return result;
  }

  async executeParallel(
    tasks: string[],
    budget: BudgetLevel = "normal",
    timeoutSeconds: number = 300
  ): Promise<SubagentResult[]> {
    // 1. 并行分类
    const classifications = await Promise.all(
      tasks.map((task) => this.classifier.classify(task))
    );

    // 2. 路由决策
    const decisions = classifications.map((c) => this.router.route(c, budget));

    // 3. 并行分发
    const dispatchTasks = tasks.map((task, i) => ({
      task,
      decision: decisions[i],
      timeoutSeconds,
    }));

    const results = await this.dispatcher.dispatchParallel(dispatchTasks);

    // 4. 输出摘要
    tasks.forEach((task, i) => {
      const r = results[i];
      console.log(
        `[${decisions[i].modelDisplayName}/${decisions[i].effort}] ` +
        `${task.slice(0, 60)}... → ${r.success ? "✓" : "✗"} (${r.elapsedMs}ms)`
      );
    });

    return results;
  }
}

// ============================================================
// 便捷函数
// ============================================================

async function routeAndExecute(
  task: string,
  apiKey?: string,
  budget: BudgetLevel = "normal",
  forceModel?: string
): Promise<SubagentResult> {
  const router = new IntelligentModelRouter(apiKey);
  return router.execute({ task, budget, forceModel });
}

// ============================================================
// 导出
// ============================================================

export {
  // 类型
  Complexity,
  Domain,
  BudgetLevel,
  ThinkingConfig,
  TaskClassification,
  RoutingDecision,
  SubagentResult,
  ModelSpec,
  RoutingRule,
  RouterConfig,

  // 类
  TaskClassifier,
  ModelRouter,
  SubagentDispatcher,
  IntelligentModelRouter,

  // 函数
  routeAndExecute,
};

// ============================================================
// 使用示例
// ============================================================

async function example() {
  const router = new IntelligentModelRouter(process.env.ANTHROPIC_API_KEY);

  // 示例 1: 架构设计 → 自动路由到 Opus
  const r1 = await router.execute({
    task: "设计一个支持千万级 QPS 的分布式消息队列架构，需要考虑高可用和容灾",
  });
  console.log(`[${r1.modelUsed}] ${r1.output.slice(0, 200)}...`);

  // 示例 2: 简单编辑 → 自动路由到 Haiku
  const r2 = await router.execute({
    task: "把 config.ts 里的 maxConnections 变量名改为 maxConns",
  });
  console.log(`[${r2.modelUsed}] ${r2.output.slice(0, 200)}...`);

  // 示例 3: 并行审查
  const results = await router.executeParallel([
    "审查 auth.ts 的安全漏洞",
    "审查 payment.ts 的事务处理",
    "审查 cache.ts 的缓存策略",
  ]);
  results.forEach((r) => {
    console.log(`[${r.modelUsed}] ${r.success ? "✓" : "✗"} ${r.elapsedMs}ms`);
  });

  // 示例 4: 强制指定模型
  const r4 = await router.execute({
    task: "写一个快速排序的实现",
    forceModel: "claude-haiku-4-5",
  });

  // 示例 5: 低成本模式
  const r5 = await router.execute({
    task: "给这个函数写注释",
    budget: "low",
  });
}

// 仅在直接运行时执行示例
if (require.main === module) {
  example().catch(console.error);
}
