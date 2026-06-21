# Model Router Skill

智能模型路由技能 —— 自动检测任务类型，选择最优模型执行，完成后回归主模型。

## 触发条件

当用户输入以下模式时自动激活：
- `/model-router <任务>`
- 提到"自动选择模型"、"模型路由"、"智能分发"
- 复杂任务需要多模型协作时

## 工作流程

### Step 1: 任务分析

分析用户任务，输出分类结果：

```
任务分类维度：
├── 复杂度: simple | moderate | complex | very_complex
├── 领域: coding | architecture | review | testing | docs | research | data_analysis | conversation
├── 紧急度: low | normal | high
├── 需要深度推理: true | false
└── 估计 Token 量: <number>
```

### Step 2: 模型选择

根据分类结果选择最优模型和参数：

| 条件 | 模型 | Thinking | Effort |
|------|------|----------|--------|
| complexity=very_complex | claude-opus-4-7 | adaptive | xhigh |
| domain=architecture | claude-opus-4-7 | adaptive | max |
| domain=review | claude-opus-4-7 | adaptive | high |
| domain=coding + complexity=complex | claude-opus-4-7 | adaptive | high |
| domain=coding + complexity=moderate | claude-sonnet-4-6 | adaptive | high |
| domain=testing | claude-sonnet-4-6 | adaptive | medium |
| domain=docs | claude-sonnet-4-6 | adaptive | medium |
| domain=research | claude-sonnet-4-6 | adaptive | high |
| complexity=simple | claude-haiku-4-5 | disabled | low |
| cost_sensitive=true | 降级一档 | - | - |

### Step 3: 子代理调度

使用 `Agent` 工具分发任务到子代理：

```markdown
## 子代理构造原则

1. **最小化上下文**: 只传递任务所需信息
2. **明确约束**: 边界清晰，不越界
3. **结构化输出**: 要求子代理以特定格式返回
4. **超时控制**: 复杂任务可拆分
```

### Step 4: 结果回归

子代理结果返回后，主模型：
1. 验证输出完整性
2. 提取关键信息
3. 无缝整合到对话流中
4. 继续处理后续任务

## 使用示例

```
用户: /model-router 帮我审查这个认证中间件的安全性

路由决策:
  → 任务类型: security_review
  → 复杂度: complex
  → 选择模型: claude-opus-4-7
  → 参数: thinking=adaptive, effort=high
  → 子代理执行中...
  → 结果已返回，主模型继续

用户: /model-router 把这个变量名从 x 改为 userId

路由决策:
  → 任务类型: simple_edit
  → 复杂度: simple
  → 选择模型: claude-haiku-4-5
  → 参数: thinking=disabled, effort=low
  → 子代理执行中...
  → 结果已返回，主模型继续
```

## 高级功能

### 并行调度

当任务可分解时，同时启动多个子代理：

```
用户: /model-router 同时审查这三个模块的安全性

路由决策:
  → 分解为 3 个独立审查任务
  → 并行启动 3 个 Opus 子代理
  → 等待全部完成
  → 聚合结果返回
```

### 级联路由

复杂任务链：前一个子代理的输出成为下一个的输入：

```
用户: /model-router 设计数据库 schema 然后生成对应的 ORM 代码

路由决策:
  → Step 1: 架构设计 → Opus (生成 schema 设计)
  → Step 2: 代码生成 → Sonnet (根据 schema 生成 ORM 代码)
  → 结果聚合返回
```

### 成本控制

根据预算自动调整模型选择：

```
用户: /model-router --budget low 帮我写单元测试

路由决策:
  → 预算: low
  → 原选模型: Opus → 降级为 Sonnet
  → 最终模型: claude-sonnet-4-6
```
