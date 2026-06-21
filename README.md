# Intelligent Model Router Gateway

<p align="center">
  <b>Provider-Agnostic · Agent-Agnostic · Visual Config · Multi-Language</b><br>
  <sub>智能模型路由网关 — 自动检测任务类型，选择最优 AI 模型执行</sub>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.4.0-blue" alt="version">
  <img src="https://img.shields.io/badge/python-3.10+-green" alt="python">
  <img src="https://img.shields.io/badge/license-MIT-brightgreen" alt="license">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey" alt="platform">
</p>

---

## Quick Start

### Option 1: Download EXE (Windows)

Download the latest `ModelRouter.exe` from [Releases](https://github.com/bpeng亮/intelligent-model-router/releases), then:

```bash
ModelRouter.exe --port 8701
```

Open **http://localhost:8701**

### Option 2: pip install

```bash
pip install git+https://github.com/bpeng亮/intelligent-model-router.git
model-router --port 8701
```

### Option 3: From source

```bash
git clone https://github.com/bpeng亮/intelligent-model-router.git
cd intelligent-model-router
pip install -r requirements.txt
python gateway.py --port 8701
```

Set your API keys:
```bash
# Windows PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:OPENAI_API_KEY = "sk-..."        # optional

# macOS / Linux
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."         # optional
```

---

## Architecture

```
User / Agent ──→ Gateway (:8701) ──→ Task Classifier (Haiku 4.5)
                      │                        │
                      │                Rule Engine Match
                      │                        │
                      ▼                        ▼
          ┌─────────────────────────────────────────┐
          │  Opus 4.7  ·  Sonnet 4.6  ·  Haiku 4.5 │
          │  GPT-5     ·  DeepSeek V4 ·  Gemini 2.5 │
          │  通义千问   ·  文心一言    ·  豆包       │
          └─────────────────────────────────────────┘
```

## Features

| Feature | Description |
|---------|------------|
| 🔀 **Auto Routing** | Detects task type → selects optimal model automatically |
| 🌐 **50+ Providers** | OpenAI, Anthropic, Google, DeepSeek, 通义千问, 豆包, 文心, Kimi... |
| 🤖 **Agent Compatible** | One-click connect: Claude Code, Codex, OpenClaw, Hermes, Cursor... |
| 🎨 **5 Color Themes** | Industrial, Slate Blue, Forest Green, Sepia, Light |
| 🌍 **5 Languages** | English, 中文, 日本語, 한국어, Français |
| 📊 **Visual Dashboard** | Real-time stats, pipeline visualization, model distribution |
| ⚡ **One-Click Agent Connect** | Auto-configures agent config files to use the gateway |
| 💰 **Budget Control** | High/Normal/Low resource quota settings |

## Supported Providers

### International
OpenAI · Anthropic · Google Gemini · xAI Grok · Meta Llama · Mistral · Cohere · Groq · Together AI · Fireworks · OpenRouter · Replicate · DeepInfra · Ollama

### Chinese Mainland
DeepSeek · 通义千问 (阿里) · 智谱 GLM · 豆包 (字节) · 文心一言 (百度) · 混元 (腾讯) · Kimi (月之暗面) · MiniMax · 百川 · 阶跃星辰 · 讯飞星火 · 华为盘古 · 商汤日日新 · 零一万物 · 天工

## Agent Compatibility

### One-Click Auto-Configure
| Agent | Config Path |
|-------|------------|
| Claude Code | `~/.claude/settings.json` |
| Codex CLI | `~/.codex/config.toml` |
| OpenClaw | `~/.openclaw/openclaw.json` |
| Hermes Agent | `~/.hermes/config.yaml` |
| OpenCode | `~/.config/opencode/opencode.json` |
| Gemini CLI | `~/.gemini/settings.yaml` |
| Aider | `~/.aider.conf.yml` |
| Cursor | `%APPDATA%/Cursor/User/settings.json` |

### Framework Integration
```python
# LangChain
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(base_url="http://localhost:8701/v1", model="router:auto")

# CrewAI
agent = Agent(llm={"model": "router:auto", "base_url": "http://localhost:8701/v1"})

# AutoGen
config_list = [{"api_type": "openai", "base_url": "http://localhost:8701/v1", "model": "router:auto"}]
```

### CLI Tools
```bash
# Claude Code
$env:ANTHROPIC_BASE_URL = "http://localhost:8701"
$env:ANTHROPIC_MODEL = "router:auto"

# Codex CLI
# In ~/.codex/config.toml:
[model_providers.router]
base_url = "http://localhost:8701/v1"
```

---

## Build from Source

```bash
# Clone
git clone https://github.com/bpeng亮/intelligent-model-router.git
cd intelligent-model-router

# Install
pip install -r requirements.txt

# Run
python gateway.py --port 8701
```

### Build Windows EXE

```bash
pip install pyinstaller
build_exe.bat
# Output: dist/ModelRouter.exe
```

---

## API Reference

| Endpoint | Protocol | Description |
|----------|----------|------------|
| `/v1/chat/completions` | OpenAI Compatible | For Codex, OpenClaw, LangChain, etc. |
| `/v1/messages` | Anthropic Compatible | For Claude Code, Claude Agent SDK |
| `/api/route` | REST | Direct auto-routing API |
| `/api/providers` | REST | List/configure providers |
| `/api/config` | REST | Read/write routing config |
| `/api/agents/connect/{id}` | REST | One-click agent config |
| `/api/stats` | REST | Usage statistics |

---

## Project Structure

```
model-router/
├── gateway.py          # Main gateway server + API
├── providers.py        # Multi-provider adapter layer
├── router.py           # Core routing engine
├── agent_bridge.py     # Agent framework bridge
├── router.ts           # TypeScript SDK
├── config.yaml         # Default configuration
├── requirements.txt    # Python dependencies
├── package.json        # Node.js dependencies
├── pyproject.toml      # Build configuration
├── build_exe.bat       # Windows exe build script
├── .gitignore
├── static/
│   ├── index.html      # Web UI (69KB single-file)
│   └── assets/         # QR codes, icons
├── configs/            # User config storage
└── examples.py         # Usage examples
```

---

## License

MIT License — free for personal and commercial use.

---

## Sponsor / 赞助

If this project helps you, consider supporting its continued development:

<p align="center">
  <table align="center">
    <tr>
      <td align="center"><b>WeChat Pay</b><br><img src="static/assets/wechat-pay.jpg" width="180"><br><sub>微信扫码</sub></td>
      <td align="center"><b>USDT (TRC20/ERC20)</b><br><img src="static/assets/usdt-pay.jpg" width="180"><br><sub>Crypto transfer</sub></td>
      <td align="center"><b>Binance Pay</b><br><img src="static/assets/binance-pay.jpg" width="180"><br><sub>币安 App 扫码</sub></td>
    </tr>
  </table>
</p>

Thank you for every contribution! All sponsors will be listed here.

### Sponsors

> *Your name here — thank you for supporting open-source!* 🙏

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
