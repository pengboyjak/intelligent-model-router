# Changelog

All notable changes to the Intelligent Model Router will be documented in this file.

## [2.4.0] — 2026-06-21

### Added
- **Industrial Precision UI redesign**: Complete visual overhaul with professional design system
- **5 color themes**: Industrial Gray, Slate Blue, Forest Green, Sepia Warm, Light
- **5 UI languages**: English, 中文 (Simplified Chinese), 日本語 (Japanese), 한국어 (Korean), Français (French)
- **One-click agent auto-configuration**: Automatically writes gateway config to Claude Code, Codex, OpenClaw, Hermes, OpenCode, Gemini CLI, Aider, Cursor config files
- **Sponsor page**: WeChat Pay, USDT (TRC20/ERC20), Binance Pay QR codes
- **Chinese font stack**: 站酷文艺体 + 思源黑体 + JetBrains Mono (no Google Fonts dependency)
- Comprehensive bilingual documentation (README.md + README-zh.md)
- CONTRIBUTING.md, CHANGELOG.md, LICENSE files
- Standard GitHub project structure with badges, topics, and full documentation

### Changed
- Sidebar group labels now fully i18n-aware
- All zh-CN translations revised for professional IT/DevOps terminology accuracy
- Static asset serving via FastAPI StaticFiles
- README restructured following 2026 GitHub best practices

### Fixed
- Language switcher initialization timing issue (DOMContentLoaded race condition)
- Static file serving (`/static/assets/*`) mount order fix
- i18n not applied to dynamically-created select options

## [2.3.0] — 2026-06-20

### Added
- 5 visual design styles: Glass, Minimal, Neo, Retro, Brutalist
- Design style switcher in sidebar (independent from color themes)
- i18n system with 5 languages (en, zh-CN, ja, ko, fr)
- 95 data-i18n translation points
- Language persistence via localStorage

### Changed
- Web UI separated from gateway.py into `static/index.html`
- Gateway serves HTML from file instead of inline string

## [2.2.0] — 2026-06-20

### Added
- `POST /v1/messages` Anthropic-compatible API endpoint
- Agent one-click connect backend (`/api/agents/connect/status`, `/api/agents/connect/{id}`)
- Agent config auto-writer with template variable replacement
- Connection status detection for installed agents
- Comprehensive agent compatibility matrix in Web UI

## [2.1.0] — 2026-06-20

### Added
- 50+ provider catalog with API key management UI
- 29 LLM providers: 14 international + 15 Chinese Mainland
- Custom model CRUD (`/api/custom-models`)
- Model dropdown selectors in routing rules and playground
- Provider enable/disable toggles with instant model list refresh

## [2.0.0] — 2026-06-20

### Added
- Multi-provider adapter layer (`providers.py`):
  - OpenAI, Anthropic, Google Gemini, DeepSeek, xAI adapters
  - Unified OpenAI-compatible adapter for OpenRouter, Ollama, vLLM, etc.
  - Provider registry and factory pattern
- Agent-agnostic bridge layer (`agent_bridge.py`):
  - LangChain, CrewAI, AutoGen framework adapters
  - RouterMiddleware for transparent LLM call interception
  - OpenAI-compatible proxy endpoint
- Visual Config Web UI (dashboard, provider management, routing rules)
- `POST /v1/chat/completions` OpenAI-compatible endpoint
- `POST /api/route` auto-routing API endpoint
- Resource quota (budget) control system
- Parallel execution API

### Changed
- Config format upgraded from v1 (`models`/`routing_rules`) to v2 (`providers`/`routing.rules`)
- Backward-compatible config loading (both v1 and v2 supported)

## [1.0.0] — 2026-06-19

### Added
- Core routing engine (`router.py`):
  - TaskClassifier: lightweight LLM-based task analysis
  - ModelRouter: rule-engine with config-driven model selection
  - SubagentDispatcher: minimal-context subagent execution
  - IntelligentModelRouter: unified entry point
- TypeScript SDK (`router.ts`)
- Configuration file (`config.yaml`) with routing rules
- Default routing strategy for 10 task categories
- Budget strategies (high/normal/low)
- Usage examples (`examples.py`)
- Claude Code skill definition (`SKILL.md`)
- README with architecture documentation
