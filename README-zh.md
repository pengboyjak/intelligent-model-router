**[English](README.md) | [中文](README-zh.md)**

<h1 align="center">
  <br>
  ◈ 智能模型路由网关
  <br>
  <sub>全提供商 · 全智能体 · 可视化配置 · 多语言</sub>
</h1>

<p align="center">
  <a href="#快速开始"><img src="https://img.shields.io/badge/版本-2.4.0-blue?style=flat-square" alt="version"></a>
  <a href="#开源许可"><img src="https://img.shields.io/badge/许可-MIT-brightgreen?style=flat-square" alt="license"></a>
  <a href="#"><img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square" alt="python"></a>
  <a href="#"><img src="https://img.shields.io/badge/平台-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey?style=flat-square" alt="platform"></a>
  <a href="#智能体兼容性"><img src="https://img.shields.io/badge/智能体-8+-purple?style=flat-square" alt="agents"></a>
  <a href="#支持的提供商"><img src="https://img.shields.io/badge/提供商-50+-orange?style=flat-square" alt="providers"></a>
  <br>
  <sub>自动检测任务类型，选择最优 AI 模型执行，完成后自动回归主模型——一个网关全部搞定。</sub>
</p>

---

## 这是什么？

**智能模型路由网关**—— 一个统一的 LLM 网关，架设在您的应用与 50+ AI 模型提供商之间。它能自动分析每个任务（架构设计？代码审查？简单编辑？），然后将其路由到最适合的模型执行（Opus 做架构、Haiku 做简单任务），完成后无缝返回结果。

**解决的核心问题：** 不同 AI 模型各有所长。Opus 擅长系统设计但简单任务用它是浪费；Haiku 快速便宜但无法处理复杂推理。手动选择模型既费时又不精准。本网关用**一个统一端点**解决了这个问题——您只需描述任务，路由、执行、结果聚合全部自动完成。

---

## 快速开始

### 环境要求
- Python 3.10+
- 至少一个模型提供商的 API 密钥（推荐 Anthropic）

### 三步启动
```bash
git clone https://github.com/pengboyjak/intelligent-model-router.git
cd intelligent-model-router && pip install -r requirements.txt
python gateway.py --port 8701
```

设置 API 密钥，打开浏览器访问 **http://localhost:8701**：
```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Windows 免安装版
从 [Releases](https://github.com/pengboyjak/intelligent-model-router/releases) 下载 `ModelRouter.exe`，双击运行即可。

---

## 功能特性

| # | 功能 | 说明 |
|---|------|------|
| 🔀 | **自动任务路由** | 按领域/复杂度分类任务 → 选择最优模型 → 执行 → 返回结果 |
| 🌐 | **50+ 模型提供商** | OpenAI、Anthropic、Google、DeepSeek、通义千问、豆包、文心、Kimi 等 |
| 🤖 | **一键接入智能体** | 自动配置 Claude Code、Codex、OpenClaw、Hermes、Cursor 等配置文件 |
| 🎨 | **5 种界面主题** | 工业灰 · 暗蓝 · 墨绿 · 暖棕 · 亮色 |
| 🌍 | **5 种界面语言** | English · 中文 · 日本語 · 한국어 · Français |
| 📊 | **可视化仪表盘** | 实时请求统计、Token 用量、模型分布、延迟监控 |
| 🔌 | **双协议支持** | OpenAI 兼容 `/v1/chat/completions` + Anthropic `/v1/messages` 端点 |
| 💰 | **资源配额控制** | 节省 / 标准 / 充裕 三档，自动调整模型选择 |
| ⚡ | **并行执行** | 多个独立任务同时分发给不同模型处理 |
| 🔄 | **故障转移** | 主模型失败后自动切换备用模型重试 |

### 不做的事情
- **不是 AI 智能体框架**—— 它是一个路由网关，可与任何智能体框架配合使用
- **不是模型训练/推理平台**—— 它路由到现有提供商 API，不运行模型
- **不是聊天 UI 产品**—— 它是后端网关，附带可选管理面板

---

## 支持的提供商

### 🌍 国际提供商（15 家）
OpenAI（GPT-5 / GPT-4o / o4）· Anthropic（Claude Opus 4.7 / Sonnet 4.6 / Haiku 4.5）· Google（Gemini 2.5 Pro / Flash）· xAI（Grok 4）· Meta（Llama 4）· Mistral · Cohere · Groq · Together AI · Fireworks · OpenRouter · Replicate · DeepInfra · Ollama

### 🇨🇳 国内提供商（14 家）
**DeepSeek**（V4 / R1）· **通义千问**（Qwen3 Max / Plus / Flash）· **智谱AI**（GLM 5.1 / 4.5）· **豆包**（Pro / Flash / Thinking 1.6）· **文心一言**（ERNIE 5.0 / 4.5）· **混元**（TurboS / T1）· **Kimi**（K2.6 / K2.5）· **MiniMax**（M2.7）· **百川**（Baichuan 4）· **阶跃星辰**（Step 3）· **讯飞星火**（Spark V5）· **华为盘古**· **商汤日日新**· **零一万物**（Yi Lightning）

---

## 智能体兼容性

### 一键自动配置

在 Web UI 的「智能体接入」页面，点击任意智能体的「一键接入」按钮，网关自动将正确配置写入对应的配置文件（自动备份原文件）：

| 智能体 | 配置文件路径 | 通信协议 |
|--------|------------|----------|
| **Claude Code** | `~/.claude/settings.json` | Anthropic Messages |
| **Codex CLI** | `~/.codex/config.toml` + `auth.json` | OpenAI Responses |
| **OpenClaw** | `~/.openclaw/openclaw.json` | OpenAI + ACP |
| **Hermes Agent** | `~/.hermes/config.yaml` + `.env` | OpenAI |
| **OpenCode** | `~/.config/opencode/opencode.json` | OpenAI |
| **Gemini CLI** | `~/.gemini/settings.yaml` | Gemini |
| **Aider** | `~/.aider.conf.yml` | OpenAI / Anthropic |
| **Cursor** | `%APPDATA%/Cursor/User/settings.json` | OpenAI |

### 框架 SDK 集成

```python
# === LangChain / LangGraph ===
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(base_url="http://localhost:8701/v1", model="router:auto")

# === CrewAI ===
from crewai import Agent
agent = Agent(llm={"model": "router:auto", "base_url": "http://localhost:8701/v1"})

# === AutoGen / Microsoft Agent Framework ===
config_list = [{"api_type": "openai", "base_url": "http://localhost:8701/v1", "model": "router:auto"}]
```

---

## API 参考

### 核心端点

| 端点 | 方法 | 协议 | 说明 |
|------|------|------|------|
| `/v1/chat/completions` | POST | OpenAI 兼容 | 供 Codex、LangChain 等使用 |
| `/v1/messages` | POST | Anthropic 兼容 | 供 Claude Code 使用 |
| `/api/route` | POST | REST | 直接调用自动路由 |
| `/api/health` | GET | REST | 健康检查 |
| `/api/stats` | GET | REST | 使用统计 |
| `/api/providers` | GET/POST | REST | 提供商管理 |
| `/api/config` | GET/POST | REST | 读写路由配置 |
| `/api/agents/connect/{id}` | POST | REST | 一键配置智能体 |

### 自动路由示例

```bash
curl -X POST http://localhost:8701/api/route \
  -H "Content-Type: application/json" \
  -d '{"task": "设计一个分布式缓存系统架构", "budget": "normal"}'
```

返回：
```json
{
  "model": "claude-opus-4-7",
  "task_type": "架构设计",
  "output": "## 分布式缓存架构\n\n1. 系统概览...",
  "usage": {"input_tokens": 169, "output_tokens": 2095},
  "elapsed_ms": 34408
}
```

---

## 默认路由策略

| 任务类型 | 复杂度 | → 模型 | 推理深度 |
|---------|--------|--------|---------|
| 架构设计 | 高难度 | Opus 4.7 | max |
| 安全审计 | 复杂 | Opus 4.7 | max |
| 代码审查 | 中等+ | Opus 4.7 | high |
| 复杂编码 | 复杂 | Opus 4.7 | high |
| 标准编码 | 中等 | Sonnet 4.6 | high |
| 测试编写 | 中等 | Sonnet 4.6 | medium |
| 文档编写 | 中等 | Sonnet 4.6 | medium |
| 技术调研 | 中等 | Sonnet 4.6 | high |
| 简单任务 | 简单 | Haiku 4.5 | low |

### 资源配额

| 级别 | 行为 | 适用场景 |
|------|------|---------|
| **充裕** | 顶级模型，最大推理深度 | 关键生产任务 |
| **标准** | 平衡质量与成本（默认） | 日常开发 |
| **节省** | 经济模型，最小推理深度 | 批量简单任务 |

---

## 架构

```
用户 / 智能体 ──→ 网关 (:8701) ──→ 任务分类器 (Haiku 4.5)
                        │                    │
                        │              规则引擎匹配
                        │                    │
                        ▼                    ▼
            ┌─────────────────────────────────────────┐
            │  Opus 4.7  ·  Sonnet 4.6  ·  Haiku 4.5 │
            │  GPT-5     ·  DeepSeek V4 ·  Gemini 2.5 │
            │  通义千问   ·  文心一言    ·  豆包       │
            └─────────────────────────────────────────┘
```

---

## 项目结构

```
intelligent-model-router/
├── gateway.py              # FastAPI 网关主程序
├── providers.py            # 50+ 模型提供商适配层
├── router.py               # 智能路由引擎（Python）
├── router.ts               # 智能路由引擎（TypeScript）
├── agent_bridge.py         # 智能体框架桥接层
├── config.yaml             # 默认配置文件
├── examples.py             # 完整使用示例
├── build_exe.bat           # Windows EXE 构建脚本
├── static/
│   ├── index.html           # Web 管理面板
│   └── assets/              # 二维码图片资源
├── README.md                # 英文文档
├── README-zh.md             # 中文文档（本文件）
├── CONTRIBUTING.md          # 贡献指南
├── CHANGELOG.md             # 版本历史
└── LICENSE                  # MIT 许可证
```

---

## 参与贡献

欢迎贡献！详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 贡献流程
1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/功能名`
3. 提交更改：`git commit -m '添加某某功能'`
4. 推送分支：`git push origin feature/功能名`
5. 发起 Pull Request

---

## 常见问题

<details>
<summary><b>问：这个会替代我的 AI 智能体框架吗？</b></summary>
不会。模型路由网关位于你的智能体框架之前，作为一个智能代理。你的智能体仍然使用 LangChain/CrewAI/AutoGen，只是通过网关获得了自动模型优化能力。
</details>

<details>
<summary><b>问：主模型失败了怎么办？</b></summary>
网关支持自动故障转移。在 config.yaml 中配置 failover.chains，主模型出错后自动重试备用模型。
</details>

<details>
<summary><b>问：不配置提供商能用吗？</b></summary>
至少需要一个 API 密钥（推荐 Anthropic）。Web 面板中可以为每个提供商单独设置密钥。
</details>

<details>
<summary><b>问：支持离线使用吗？</b></summary>
可以，配合 Ollama 本地模型。将 ollama.enabled 设为 true，指向本地 Ollama 实例即可。
</details>

---

## 开源许可

MIT License —— 详见 [LICENSE](LICENSE)。个人和商业用途均免费。

---

## 赞助支持

<p align="center">
  <table align="center">
    <tr>
      <td align="center" width="33%">
        <b>💚 微信支付</b><br>
        <img src="static/assets/wechat-pay.jpg" width="180" style="border-radius:8px"><br>
        <sub>微信扫码赞助</sub>
      </td>
      <td align="center" width="33%">
        <b>🪙 USDT (TRC20 / ERC20)</b><br>
        <img src="static/assets/usdt-pay.jpg" width="180" style="border-radius:8px"><br>
        <sub>加密货币转账</sub>
      </td>
      <td align="center" width="33%">
        <b>🔶 币安支付</b><br>
        <img src="static/assets/binance-pay.jpg" width="180" style="border-radius:8px"><br>
        <sub>币安 App 扫码</sub>
      </td>
    </tr>
  </table>
</p>

### 赞助者名单

> *您的名字将出现在这里 — 感谢您对开源的每一份支持！* 🙏

---

<p align="center">
  <sub>为 AI 开发者社区而构建 ❤️</sub>
</p>
