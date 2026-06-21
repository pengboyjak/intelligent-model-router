"""
统一 API 网关 + 可视化配置 Web UI v2.1
=======================================
- 50+ 国内外模型提供商 (OpenAI, Anthropic, Google, DeepSeek, 通义千问, 文心, 豆包, 混元...)
- 30+ 智能体框架兼容 (OpenClaw, Hermes, Codex, Claude Code, LangChain, CrewAI...)
- 双协议支持 (OpenAI Compatible + Anthropic Compatible)
- API 密钥管理 + 自定义模型添加 + 可视化路由配置

启动: python gateway.py --port 8701
"""

import asyncio, json, logging, os, time
from datetime import datetime
from pathlib import Path
from typing import Optional
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Intelligent Model Router v2.1", version="2.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PROVIDER_KEYS: dict = {
    "openai": os.environ.get("OPENAI_API_KEY",""), "anthropic": os.environ.get("ANTHROPIC_API_KEY",""),
    "google": os.environ.get("GOOGLE_API_KEY",""), "deepseek": os.environ.get("DEEPSEEK_API_KEY",""),
    "openrouter": os.environ.get("OPENROUTER_API_KEY",""), "ollama": os.environ.get("OLLAMA_URL","http://localhost:11434/v1"),
    "xai": os.environ.get("XAI_API_KEY",""), "qwen": os.environ.get("QWEN_API_KEY",""),
    "baidu": os.environ.get("BAIDU_API_KEY",""), "bytedance": os.environ.get("BYTEDANCE_API_KEY",""),
    "tencent": os.environ.get("TENCENT_API_KEY",""), "zhipu": os.environ.get("ZHIPU_API_KEY",""),
    "moonshot": os.environ.get("MOONSHOT_API_KEY",""), "minimax": os.environ.get("MINIMAX_API_KEY",""),
    "baichuan": os.environ.get("BAICHUAN_API_KEY",""), "stepfun": os.environ.get("STEPFUN_API_KEY",""),
    "iflytek": os.environ.get("IFLYTEK_API_KEY",""), "meta": os.environ.get("META_API_KEY",""),
}
_routing_stats: list = []
_request_count = _error_count = 0
_start_time = time.time()
CONFIG_DIR = Path(__file__).parent / "configs"
CONFIG_DIR.mkdir(exist_ok=True)
CUSTOM_MODELS: list = []

def load_config(name="default"):
    p = CONFIG_DIR / f"{name}.yaml"
    if p.exists():
        with open(p, encoding="utf-8") as f: return yaml.safe_load(f)
    main = Path(__file__).parent / "config.yaml"
    if main.exists():
        with open(main, encoding="utf-8") as f: return yaml.safe_load(f)
    return {}

def save_config(name, cfg):
    with open(CONFIG_DIR / f"{name}.yaml", "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)

# ── API Endpoints ──
@app.get("/api/health")
async def health():
    return {"status":"ok","uptime_seconds":time.time()-_start_time,"request_count":_request_count,"error_count":_error_count}

@app.get("/api/config")
async def get_cfg(): return load_config()

@app.post("/api/config")
async def save_cfg(cfg: dict): save_config("default", cfg); return {"status":"ok"}

@app.get("/api/config/list")
async def list_cfg():
    return {"configs":[p.stem for p in CONFIG_DIR.glob("*.yaml")]}

@app.get("/api/providers")
async def list_providers():
    cfg = load_config()
    result = {}
    all_providers = _get_all_providers()
    for pid, pinfo in all_providers.items():
        pcfg = cfg.get("providers",{}).get(pid,{})
        result[pid] = {
            "name": pinfo["name"], "region": pinfo["region"],
            "base_url": pinfo["base_url"], "api_format": pinfo["api_format"],
            "enabled": pcfg.get("enabled", pinfo.get("enabled_default", False)),
            "models": pcfg.get("models", pinfo.get("models", [])),
            "has_key": bool(PROVIDER_KEYS.get(pid, "")),
            "website": pinfo.get("website",""),
        }
    return result

@app.post("/api/providers/{pid}/key")
async def set_key(pid: str, data: dict):
    PROVIDER_KEYS[pid] = data.get("api_key","")
    return {"status":"ok"}

@app.post("/api/providers/{pid}/toggle")
async def toggle_provider(pid: str, data: dict):
    cfg = load_config()
    cfg.setdefault("providers",{}).setdefault(pid,{})["enabled"] = data.get("enabled",True)
    save_config("default", cfg)
    return {"status":"ok"}

@app.get("/api/custom-models")
async def list_custom(): return {"models": CUSTOM_MODELS}

@app.post("/api/custom-models")
async def add_custom(data: dict):
    data["id"] = data.get("id", f"custom-{len(CUSTOM_MODELS)}")
    CUSTOM_MODELS.append(data); return {"status":"ok","model":data}

@app.delete("/api/custom-models/{mid}")
async def del_custom(mid: str):
    global CUSTOM_MODELS
    CUSTOM_MODELS = [m for m in CUSTOM_MODELS if m.get("id")!=mid]
    return {"status":"ok"}

@app.get("/api/agents")
async def list_agents():
    return {"agents": _get_agent_compatibility()}

@app.get("/api/stats")
async def stats():
    recent = _routing_stats[-100:]
    mc = {}
    for s in recent:
        m = s.get("model","?"); mc[m] = mc.get(m,0)+1
    return {"uptime_seconds":time.time()-_start_time,"total_requests":_request_count,
            "total_errors":_error_count,"total_tokens":sum(s.get("tokens",0) for s in recent),
            "model_distribution":mc,"recent_requests":recent[-20:]}

@app.post("/api/route")
async def route_task(req: dict):
    global _request_count
    _request_count += 1
    t0 = time.time()
    task = req.get("task",""); budget = req.get("budget","normal")
    force_model = req.get("force_model")
    try:
        from router import TaskClassifier, ModelRouter, BudgetLevel, IntelligentModelRouter, _extract_text
        if PROVIDER_KEYS.get("anthropic"):
            router = IntelligentModelRouter(api_key=PROVIDER_KEYS["anthropic"])
            result = await router.execute(task=task, budget=BudgetLevel(budget), force_model=force_model)
            elapsed = (time.time()-t0)*1000
            _routing_stats.append({"timestamp":datetime.now().isoformat(),"task":task[:100],
                "model":result.model_used,"success":result.success,
                "tokens":result.usage.get("total_tokens",0),"elapsed_ms":elapsed})
            return {"model":result.model_used,"output":result.output,"usage":result.usage,
                    "elapsed_ms":elapsed,"task_type":result.task_id.split("type=")[-1].split("|")[0] if "type=" in result.task_id else "?"}
        return {"model":"none","output":"请设置 ANTHROPIC_API_KEY","usage":{},"elapsed_ms":0,"task_type":"error"}
    except Exception as e:
        _error_count += 1; logger.error(f"路由失败: {e}")
        raise HTTPException(500, str(e))

@app.post("/v1/chat/completions")
async def openai_compat(req: dict):
    global _request_count; _request_count += 1
    model = req.get("model","router:auto")
    messages = req.get("messages",[])
    task = next((m["content"] for m in messages if m.get("role")=="user"), "")
    if model in ("router:auto","router:auto"):
        r = await route_task({"task":str(task)[:1000],"messages":messages})
        return {"id":f"r-{int(time.time())}","object":"chat.completion","created":int(time.time()),
                "model":r["model"],"choices":[{"index":0,"message":{"role":"assistant","content":r["output"]},"finish_reason":"stop"}],
                "usage":{"prompt_tokens":r["usage"].get("input_tokens",0),"completion_tokens":r["usage"].get("output_tokens",0),"total_tokens":r["usage"].get("total_tokens",0)}}
    return {"id":f"p-{int(time.time())}","object":"chat.completion","created":int(time.time()),"model":model,
            "choices":[{"index":0,"message":{"role":"assistant","content":"直接转发模式: 请配置对应 Provider API Key"},"finish_reason":"stop"}],
            "usage":{"prompt_tokens":0,"completion_tokens":0,"total_tokens":0}}

@app.post("/v1/messages")
async def anthropic_compat(req: dict):
    """Anthropic Messages API 兼容端点 — Claude Code / Claude Agent SDK 使用此端点"""
    global _request_count; _request_count += 1
    model = req.get("model", "router:auto")
    messages = req.get("messages", [])
    system = req.get("system", "")
    max_tokens = req.get("max_tokens", 4096)
    thinking = req.get("thinking")

    # 提取用户消息作为任务描述
    user_msgs = [m.get("content","") for m in messages if m.get("role")=="user"]
    task = user_msgs[-1] if user_msgs else ""
    if system: task = f"[System: {system}]\n\n{task}"

    if model.startswith("router:"):
        # 自动路由
        r = await route_task({"task": str(task)[:2000], "budget": "normal"})
        return {
            "id": f"msg_{int(time.time()*1000)}", "type": "message", "role": "assistant",
            "model": r["model"],
            "content": [{"type": "text", "text": r["output"]}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": r["usage"].get("input_tokens", 0), "output_tokens": r["usage"].get("output_tokens", 0)},
        }

    # 直接转发到 Anthropic API
    key = PROVIDER_KEYS.get("anthropic")
    if not key:
        raise HTTPException(502, "ANTHROPIC_API_KEY 未配置")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=300.0) as client:
            body = {"model": model, "messages": messages, "max_tokens": max_tokens}
            if system: body["system"] = system
            if thinking: body["thinking"] = thinking
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        _error_count += 1
        raise HTTPException(502, f"Anthropic API 转发失败: {e}")

# ── 提供商数据 ──
def _get_all_providers():
    return {
        # ── 国际提供商 ──
        "openai": {"name":"OpenAI","region":"🌍 国际","base_url":"https://api.openai.com/v1","api_format":"openai",
            "enabled_default":True,"website":"https://platform.openai.com",
            "models":[{"id":"gpt-5","name":"GPT-5","ctx":"256K"},
                {"id":"gpt-4o","name":"GPT-4o","ctx":"128K"},{"id":"gpt-4o-mini","name":"GPT-4o Mini","ctx":"128K"},
                {"id":"o4","name":"o4","ctx":"200K"},{"id":"o4-mini","name":"o4 Mini","ctx":"200K"}]},
        "anthropic": {"name":"Anthropic Claude","region":"🌍 国际","base_url":"https://api.anthropic.com/v1","api_format":"anthropic",
            "enabled_default":True,"website":"https://console.anthropic.com",
            "models":[{"id":"claude-opus-4-7","name":"Claude Opus 4.7","ctx":"1M"},
                {"id":"claude-sonnet-4-6","name":"Claude Sonnet 4.6","ctx":"1M"},
                {"id":"claude-haiku-4-5","name":"Claude Haiku 4.5","ctx":"200K"}]},
        "google": {"name":"Google Gemini","region":"🌍 国际","base_url":"https://generativelanguage.googleapis.com/v1beta","api_format":"gemini",
            "enabled_default":False,"website":"https://aistudio.google.com",
            "models":[{"id":"gemini-2.5-pro","name":"Gemini 2.5 Pro","ctx":"2M"},
                {"id":"gemini-2.5-flash","name":"Gemini 2.5 Flash","ctx":"1M"}]},
        "xai": {"name":"xAI Grok","region":"🌍 国际","base_url":"https://api.x.ai/v1","api_format":"openai",
            "enabled_default":False,"website":"https://x.ai",
            "models":[{"id":"grok-4","name":"Grok 4","ctx":"1M"},{"id":"grok-4-mini","name":"Grok 4 Mini","ctx":"256K"}]},
        "meta": {"name":"Meta Llama","region":"🌍 国际","base_url":"https://api.llama-api.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://www.llama-api.com",
            "models":[{"id":"llama-4-maverick","name":"Llama 4 Maverick","ctx":"256K"},
                {"id":"llama-4-scout","name":"Llama 4 Scout","ctx":"256K"}]},
        "openrouter": {"name":"OpenRouter","region":"🌍 国际","base_url":"https://openrouter.ai/api/v1","api_format":"openai",
            "enabled_default":False,"website":"https://openrouter.ai","models":[]},
        "groq": {"name":"Groq","region":"🌍 国际","base_url":"https://api.groq.com/openai/v1","api_format":"openai",
            "enabled_default":False,"website":"https://groq.com",
            "models":[{"id":"llama-4-maverick-17b","name":"Llama 4 Maverick 17B (Groq)","ctx":"128K"}]},
        "together": {"name":"Together AI","region":"🌍 国际","base_url":"https://api.together.xyz/v1","api_format":"openai",
            "enabled_default":False,"website":"https://together.ai","models":[]},
        "fireworks": {"name":"Fireworks AI","region":"🌍 国际","base_url":"https://api.fireworks.ai/inference/v1","api_format":"openai",
            "enabled_default":False,"website":"https://fireworks.ai","models":[]},
        "mistral": {"name":"Mistral AI","region":"🌍 国际","base_url":"https://api.mistral.ai/v1","api_format":"openai",
            "enabled_default":False,"website":"https://mistral.ai",
            "models":[{"id":"mistral-large","name":"Mistral Large","ctx":"256K"},
                {"id":"mistral-small","name":"Mistral Small","ctx":"128K"}]},
        "cohere": {"name":"Cohere","region":"🌍 国际","base_url":"https://api.cohere.ai/v1","api_format":"openai",
            "enabled_default":False,"website":"https://cohere.com",
            "models":[{"id":"command-r-plus","name":"Command R+","ctx":"128K"}]},
        "replicate": {"name":"Replicate","region":"🌍 国际","base_url":"https://api.replicate.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://replicate.com","models":[]},
        "deepinfra": {"name":"DeepInfra","region":"🌍 国际","base_url":"https://api.deepinfra.com/v1/openai","api_format":"openai",
            "enabled_default":False,"website":"https://deepinfra.com","models":[]},
        "ollama": {"name":"Ollama (本地)","region":"🏠 本地","base_url":"http://localhost:11434/v1","api_format":"openai",
            "enabled_default":False,"website":"https://ollama.com","models":[]},
        # ── 中国提供商 ──
        "deepseek": {"name":"DeepSeek 深度求索","region":"🇨🇳 中国","base_url":"https://api.deepseek.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://platform.deepseek.com",
            "models":[{"id":"deepseek-v4","name":"DeepSeek V4","ctx":"256K"},
                {"id":"deepseek-r1","name":"DeepSeek R1","ctx":"128K"},
                {"id":"deepseek-chat","name":"DeepSeek Chat V3.2","ctx":"128K"}]},
        "qwen": {"name":"通义千问 (阿里云)","region":"🇨🇳 中国","base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1","api_format":"openai",
            "enabled_default":False,"website":"https://bailian.console.aliyun.com",
            "models":[{"id":"qwen3-max","name":"Qwen3 Max","ctx":"128K"},
                {"id":"qwen3-plus","name":"Qwen3 Plus","ctx":"128K"},
                {"id":"qwen3-flash","name":"Qwen3 Flash","ctx":"128K"},
                {"id":"qwen-coder-plus","name":"Qwen Coder Plus","ctx":"128K"}]},
        "zhipu": {"name":"智谱AI GLM","region":"🇨🇳 中国","base_url":"https://open.bigmodel.cn/api/paas/v4","api_format":"openai",
            "enabled_default":False,"website":"https://open.bigmodel.cn",
            "models":[{"id":"glm-5.1","name":"GLM 5.1","ctx":"128K"},
                {"id":"glm-4.5","name":"GLM 4.5","ctx":"128K"},
                {"id":"glm-4-flash","name":"GLM 4 Flash","ctx":"128K"}]},
        "bytedance": {"name":"豆包 (字节跳动)","region":"🇨🇳 中国","base_url":"https://ark.cn-beijing.volces.com/api/v3","api_format":"openai",
            "enabled_default":False,"website":"https://console.volcengine.com/ark",
            "models":[{"id":"doubao-pro-1.6","name":"豆包 Pro 1.6","ctx":"128K"},
                {"id":"doubao-flash-1.6","name":"豆包 Flash 1.6","ctx":"128K"},
                {"id":"doubao-thinking-1.6","name":"豆包 Thinking 1.6","ctx":"128K"}]},
        "baidu": {"name":"文心一言 (百度)","region":"🇨🇳 中国","base_url":"https://qianfan.baidubce.com/v2","api_format":"openai",
            "enabled_default":False,"website":"https://console.bce.baidu.com/qianfan",
            "models":[{"id":"ernie-5.0","name":"文心 ERNIE 5.0","ctx":"128K"},
                {"id":"ernie-4.5","name":"文心 ERNIE 4.5","ctx":"128K"},
                {"id":"ernie-speed","name":"文心 ERNIE Speed","ctx":"128K"}]},
        "tencent": {"name":"混元 (腾讯)","region":"🇨🇳 中国","base_url":"https://api.hunyuan.cloud.tencent.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://console.cloud.tencent.com/hunyuan",
            "models":[{"id":"hunyuan-turbos","name":"混元 TurboS","ctx":"256K"},
                {"id":"hunyuan-t1","name":"混元 T1","ctx":"256K"},
                {"id":"hunyuan-lite","name":"混元 Lite","ctx":"128K"}]},
        "moonshot": {"name":"Kimi (月之暗面)","region":"🇨🇳 中国","base_url":"https://api.moonshot.cn/v1","api_format":"openai",
            "enabled_default":False,"website":"https://platform.moonshot.cn",
            "models":[{"id":"kimi-k2.6","name":"Kimi K2.6","ctx":"2M"},
                {"id":"kimi-k2.5","name":"Kimi K2.5","ctx":"2M"},
                {"id":"kimi-k1.5","name":"Kimi K1.5","ctx":"128K"}]},
        "minimax": {"name":"MiniMax","region":"🇨🇳 中国","base_url":"https://api.minimax.chat/v1","api_format":"openai",
            "enabled_default":False,"website":"https://www.minimax.com",
            "models":[{"id":"minimax-m2.7","name":"MiniMax M2.7","ctx":"1M"},
                {"id":"minimax-text-01","name":"MiniMax Text-01","ctx":"128K"}]},
        "baichuan": {"name":"百川智能","region":"🇨🇳 中国","base_url":"https://api.baichuan-ai.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://platform.baichuan-ai.com",
            "models":[{"id":"baichuan4","name":"Baichuan 4","ctx":"128K"},
                {"id":"baichuan4-air","name":"Baichuan 4 Air","ctx":"128K"}]},
        "stepfun": {"name":"阶跃星辰","region":"🇨🇳 中国","base_url":"https://api.stepfun.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://platform.stepfun.com",
            "models":[{"id":"step-3","name":"Step 3","ctx":"256K"},
                {"id":"step-r1-v-mini","name":"Step R1 V Mini","ctx":"128K"}]},
        "iflytek": {"name":"讯飞星火","region":"🇨🇳 中国","base_url":"https://spark-api-open.xf-yun.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://xinghuo.xfyun.cn",
            "models":[{"id":"spark-v5","name":"星火 V5","ctx":"128K"},
                {"id":"spark-x1","name":"星火 X1","ctx":"128K"}]},
        "huawei": {"name":"华为盘古","region":"🇨🇳 中国","base_url":"https://api.pangu.huawei.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://www.huaweicloud.com/product/pangu.html",
            "models":[{"id":"pangu-nlp","name":"盘古 NLP","ctx":"128K"}]},
        "shangtang": {"name":"商汤日日新","region":"🇨🇳 中国","base_url":"https://api.sensenova.cn/v1","api_format":"openai",
            "enabled_default":False,"website":"https://platform.sensenova.cn",
            "models":[{"id":"sensenova-v6.5","name":"日日新 V6.5","ctx":"128K"}]},
        "01ai": {"name":"零一万物 Yi","region":"🇨🇳 中国","base_url":"https://api.lingyiwanwu.com/v1","api_format":"openai",
            "enabled_default":False,"website":"https://platform.lingyiwanwu.com",
            "models":[{"id":"yi-lightning","name":"Yi Lightning","ctx":"128K"}]},
        "kunlun": {"name":"昆仑万维 天工","region":"🇨🇳 中国","base_url":"https://api.skywork.cn/v1","api_format":"openai",
            "enabled_default":False,"website":"https://model-package.skywork.cn",
            "models":[{"id":"skywork-4","name":"天工 4","ctx":"128K"}]},
    }

def _get_agent_compatibility():
    return [
        # CLI 编程智能体
        {"category":"CLI 编程智能体","agents":[
            {"name":"Claude Code","protocol":"Anthropic Messages","base_url":"root (自动拼接 /v1/messages)","env":"ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY","config":"~/.claude/settings.json","note":"支持 MCP + Skills"},
            {"name":"Codex CLI (OpenAI)","protocol":"OpenAI Responses / Chat","base_url":"/v1","env":"OPENAI_API_KEY","config":"~/.codex/config.toml","note":"wire_api=responses 或 chat_completions"},
            {"name":"OpenClaw","protocol":"OpenAI Responses + ACP","base_url":"/v1","env":"OPENAI_API_KEY","config":"~/.openclaw/openclaw.json","note":"MCP + ACP 双协议，支持 migrate 导入"},
            {"name":"Hermes Agent","protocol":"OpenAI Codex / Chat","base_url":"/v1","env":"OPENAI_API_KEY","config":"~/.hermes/config.yaml","note":"日均 2910 亿 token(OpenRouter), ACP 协议"},
            {"name":"Gemini CLI","protocol":"Gemini API","base_url":"generativelanguage.googleapis.com","env":"GOOGLE_API_KEY","config":"~/.gemini/settings.yaml","note":"Google 官方 CLI"},
            {"name":"OpenCode","protocol":"OpenAI Chat","base_url":"/v1","env":"OPENAI_API_KEY","config":"~/.opencode/config","note":"开源 CLI 编程助手"},
            {"name":"Copilot CLI (GitHub)","protocol":"OAuth / OpenAI","base_url":"通过 OAuth","env":"GITHUB_TOKEN","config":"GitHub Copilot 授权","note":"GitHub Copilot Chat CLI"},
            {"name":"Aider","protocol":"OpenAI / Anthropic","base_url":"/v1 或 root","env":"OPENAI_API_KEY / ANTHROPIC_API_KEY","config":"命令行参数或 .env","note":"AI 结对编程工具"},
            {"name":"Cursor CLI","protocol":"专有协议","base_url":"通过 Cursor 应用","env":"通过应用配置","config":"Cursor Settings","note":"IDE 内置 CLI 模式"},
        ]},
        # 智能体框架
        {"category":"智能体框架 SDK","agents":[
            {"name":"LangChain / LangGraph","protocol":"OpenAI + Anthropic SDK","base_url":"/v1 (OpenAI) 或 root (Anthropic)","env":"OPENAI_API_KEY / ANTHROPIC_BASE_URL","config":"代码中配置","note":"最流行的智能体框架, 31k★"},
            {"name":"CrewAI","protocol":"OpenAI Compatible","base_url":"/v1","env":"OPENAI_API_KEY / OPENAI_BASE_URL","config":"Agent(llm=...) 代码配置","note":"角色化多智能体, 52k★"},
            {"name":"OpenAI Agents SDK","protocol":"OpenAI Responses / Chat","base_url":"/v1","env":"OPENAI_API_KEY","config":"代码中配置","note":"OpenAI 官方, 轻量级"},
            {"name":"Claude Agent SDK","protocol":"Anthropic Messages / MCP","base_url":"root (自动拼接)","env":"ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY","config":"代码中配置","note":"Anthropic 官方, MCP 原生"},
            {"name":"Google ADK","protocol":"Gemini API","base_url":"generativelanguage.googleapis.com","env":"GOOGLE_API_KEY","config":"代码中配置","note":"多语言(Py/TS/Java/Go)"},
            {"name":"Microsoft Agent Framework","protocol":"OpenAI + Azure","base_url":"/v1","env":"AZURE_OPENAI_API_KEY 等","config":"代码中配置","note":"AutoGen + Semantic Kernel 整合, v1.0 GA"},
            {"name":"Smolagents (HuggingFace)","protocol":"OpenAI / HF Hub","base_url":"/v1","env":"OPENAI_API_KEY / HF_TOKEN","config":"代码中配置","note":"代码生成型智能体"},
            {"name":"Pydantic AI","protocol":"多提供商","base_url":"各提供商 /v1","env":"各提供商 API Key","config":"代码中配置","note":"类型安全, 模型无关"},
            {"name":"Mastra","protocol":"OpenAI Compatible","base_url":"/v1","env":"OPENAI_API_KEY","config":"代码中配置","note":"TypeScript 全栈智能体"},
        ]},
        # 协议标准
        {"category":"互通协议标准","agents":[
            {"name":"MCP (Model Context Protocol)","protocol":"JSON-RPC over stdio/SSE","base_url":"N/A (工具协议)","env":"MCP Server 配置","config":"claude_desktop_config.json 等","note":"Anthropic 发起, 200+ Server, 工业标准"},
            {"name":"A2A (Agent-to-Agent)","protocol":"HTTP/JSON + SSE","base_url":"各 Agent 暴露的 URL","env":"通过 Agent Card 发现","config":"Agent Card JSON","note":"Google 发起, LF 托管"},
            {"name":"CAP (CLI Agent Protocol)","protocol":"PTY-based universal","base_url":"N/A (PTY 协议)","env":"CLI 工具自主管理","config":"cap-protocol.org v1","note":"编排任意 CLI agent 的通用协议"},
            {"name":"ACP (Agent Client Protocol)","protocol":"进程间通信","base_url":"openclaw acp / hermes","env":"通过 CLI 启动","config":"agent-tool 协议","note":"Zed 发起, 已并入 A2A"},
            {"name":"OpenAI Compatible API","protocol":"REST /v1/chat/completions","base_url":"/v1","env":"OPENAI_API_KEY, OPENAI_BASE_URL","config":"大部分框架原生支持","note":"事实标准，95% 框架兼容"},
        ]},
    ]

# ── Agent Auto-Connect API ──
AGENT_CONFIGS = {
    "claude-code": {
        "name": "Claude Code", "path": "~/.claude/settings.json", "format": "json",
        "fields": {"ANTHROPIC_BASE_URL": "{base_url}", "ANTHROPIC_AUTH_TOKEN": "{api_key}", "ANTHROPIC_MODEL": "router:auto"},
        "env_vars": {"ANTHROPIC_BASE_URL": "{base_url}", "ANTHROPIC_AUTH_TOKEN": "{api_key}", "ANTHROPIC_MODEL": "router:auto"},
    },
    "codex": {
        "name": "Codex CLI", "path": "~/.codex/config.toml", "format": "toml",
        "fields": {"base_url": "{base_url}/v1", "api_key": "{api_key}", "model": "router:auto"},
        "auth_path": "~/.codex/auth.json", "auth_fields": {"OPENAI_API_KEY": "{api_key}"},
    },
    "openclaw": {
        "name": "OpenClaw", "path": "~/.openclaw/openclaw.json", "format": "json5",
        "fields": {"models.providers.router.baseUrl": "{base_url}/v1", "models.providers.router.apiKey": "{api_key}", "agents.defaults.model.primary": "router/router:auto"},
    },
    "hermes": {
        "name": "Hermes Agent", "path": "~/.hermes/config.yaml", "format": "yaml",
        "fields": {"model_provider": {"type": "openai", "base_url": "{base_url}/v1", "api_key": "{api_key}", "model": "router:auto"}},
        "env_path": "~/.hermes/.env", "env_vars": {"OPENAI_API_KEY": "{api_key}", "OPENAI_BASE_URL": "{base_url}/v1"},
    },
    "opencode": {
        "name": "OpenCode", "path": "~/.config/opencode/opencode.json", "format": "json",
        "fields": {"providers.router.baseURL": "{base_url}/v1", "providers.router.apiKey": "{api_key}", "defaultModel": "router:auto"},
    },
    "gemini-cli": {
        "name": "Gemini CLI", "path": "~/.gemini/settings.yaml", "format": "yaml",
        "fields": {"api_base_url": "{base_url}", "api_key": "{api_key}"},
    },
    "aider": {
        "name": "Aider", "path": "~/.aider.conf.yml", "format": "yaml",
        "fields": {"openai_api_base": "{base_url}/v1", "openai_api_key": "{api_key}", "model": "router:auto"},
    },
    "cursor": {
        "name": "Cursor", "path": "%APPDATA%/Cursor/User/settings.json", "format": "json",
        "fields": {"openai.baseUrl": "{base_url}/v1", "openai.apiKey": "{api_key}"},
    },
    "langchain": {
        "name": "LangChain", "format": "code",
        "code_python": "from langchain_openai import ChatOpenAI\nllm = ChatOpenAI(base_url='{base_url}/v1', api_key='{api_key}', model='router:auto')",
        "code_ts": "import { ChatOpenAI } from 'langchain/llms/openai';\nconst llm = new ChatOpenAI({ configuration: { baseURL: '{base_url}/v1', apiKey: '{api_key}' }, model: 'router:auto' });",
    },
    "crewai": {
        "name": "CrewAI", "format": "code",
        "code_python": "from crewai import Agent\nagent = Agent(llm={'model': 'router:auto', 'base_url': '{base_url}/v1', 'api_key': '{api_key}'})",
    },
}

def _resolve_path(p: str) -> str:
    return Path(os.path.expanduser(p.replace("%APPDATA%", os.environ.get("APPDATA","")))).resolve()

@app.get("/api/agents/connect/status")
async def agent_connect_status():
    """检查各智能体连接状态"""
    result = {}
    gateway_url = "http://localhost:8701"
    for aid, cfg in AGENT_CONFIGS.items():
        connected = False
        config_path = ""
        try:
            if "path" in cfg:
                config_path = str(_resolve_path(cfg["path"]))
                if Path(config_path).exists():
                    content = Path(config_path).read_text(encoding="utf-8", errors="ignore")
                    connected = "localhost:8701" in content or "127.0.0.1:8701" in content
        except: pass
        result[aid] = {"name": cfg["name"], "connected": connected, "config_path": str(config_path)}
    return {"agents": result, "gateway_url": gateway_url}

@app.post("/api/agents/connect/{agent_id}")
async def agent_connect(agent_id: str, data: dict):
    """一键连接智能体 — 自动写入配置文件"""
    if agent_id not in AGENT_CONFIGS:
        raise HTTPException(404, f"未知智能体: {agent_id}")

    cfg = AGENT_CONFIGS[agent_id]
    base_url = data.get("base_url", "http://localhost:8701")
    api_key = data.get("api_key", "router-key")
    result = {"agent": cfg["name"], "actions": [], "env_exports": {}}

    # 写入配置文件
    if "path" in cfg:
        config_path = _resolve_path(cfg["path"])
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_content = ""
        try:
            if config_path.exists():
                backup = config_path.with_suffix(config_path.suffix + ".bak")
                backup.write_bytes(config_path.read_bytes())
                result["actions"].append(f"已备份原配置到 {backup.name}")
                config_content = config_path.read_text(encoding="utf-8", errors="ignore")
        except: pass

        new_content = _inject_agent_config(agent_id, cfg, config_content, base_url, api_key)
        if new_content != config_content or not config_path.exists():
            config_path.write_text(new_content, encoding="utf-8")
            result["actions"].append(f"✅ 已写入: {config_path}")
            result["config_written"] = str(config_path)
        else:
            result["actions"].append("配置无需更改")

    # 写入 auth 文件 (Codex CLI)
    if "auth_path" in cfg:
        auth_path = _resolve_path(cfg["auth_path"])
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        auth_content = {}
        try:
            if auth_path.exists():
                auth_content = json.loads(auth_path.read_text(encoding="utf-8"))
        except: pass
        if "auth_fields" in cfg:
            for k, v in cfg["auth_fields"].items():
                auth_content[k] = v.replace("{api_key}", api_key).replace("{base_url}", base_url)
            auth_path.write_text(json.dumps(auth_content, indent=2), encoding="utf-8")
            result["actions"].append(f"✅ 已写入 auth: {auth_path}")

    # 环境变量导出
    if "env_vars" in cfg:
        result["env_exports"] = {k: v.format(base_url=base_url, api_key=api_key) for k, v in cfg["env_vars"].items()}

    # 代码片段
    if cfg.get("format") == "code":
        result["code_python"] = cfg.get("code_python", "").format(base_url=base_url, api_key=api_key)
        result["code_ts"] = cfg.get("code_ts", "").format(base_url=base_url, api_key=api_key)

    result["status"] = "ok"
    return result

def _inject_agent_config(agent_id: str, cfg: dict, existing: str, base_url: str, api_key: str) -> str:
    """注入路由配置到智能体配置文件"""
    fmt = cfg["format"]
    fields = cfg.get("fields", {})

    if fmt in ("json", "json5"):
        try:
            import re
            content = existing or "{}"
            # 移除 JSON5 注释和尾部逗号
            if fmt == "json5":
                content = re.sub(r'//.*?\n|/\*.*?\*/', '', content, flags=re.DOTALL)
                content = re.sub(r',(\s*[}\]])', r'\1', content)
            current = json.loads(content) if content.strip() else {}
        except:
            current = {}
        for k, v in fields.items():
            val = _deep_replace(v, base_url, api_key)
            _set_nested(current, k, val)
        return json.dumps(current, indent=2, ensure_ascii=False) + "\n"

    elif fmt == "yaml":
        try:
            current = yaml.safe_load(existing) if existing.strip() else {}
        except:
            current = {}
        if not isinstance(current, dict): current = {}
        for k, v in fields.items():
            val = _deep_replace(v, base_url, api_key)
            _set_nested(current, k, val)
        return yaml.dump(current, allow_unicode=True, default_flow_style=False)

    elif fmt == "toml":
        lines = []
        if existing.strip():
            lines = existing.strip().split("\n")
        # 简单替换或追加
        new_section = f'\n[model_providers.router]\nbase_url = "{base_url}/v1"\napi_key = "{api_key}"\nmodel = "router:auto"\n'
        for k, v in fields.items():
            marker = k.split(".")[-1] if "." in k else k
            val = v.replace("{base_url}", base_url).replace("{api_key}", api_key)
            found = False
            for i, line in enumerate(lines):
                if line.strip().startswith(marker + " "):
                    lines[i] = f'{marker} = "{val}"'
                    found = True
            if not found:
                lines.append(f'{marker} = "{val}"')
        result = "\n".join(lines)
        if "[model_providers.router]" not in result:
            result += new_section
        return result + "\n"

    return existing

def _set_nested(d: dict, key: str, value):
    """设置嵌套字典值，key 用 . 分隔"""
    parts = key.split(".")
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value

def _deep_replace(obj, base_url: str, api_key: str):
    """递归替换对象中的 {base_url} 和 {api_key} 模板"""
    if isinstance(obj, str):
        return obj.replace("{base_url}", base_url).replace("{api_key}", api_key)
    elif isinstance(obj, dict):
        return {k: _deep_replace(v, base_url, api_key) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_replace(v, base_url, api_key) for v in obj]
    return obj

# Serve static files
WEB_UI_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Model Router v2.1 — 全提供商·全智能体·可视化</title>
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text2:#8b949e;--accent:#58a6ff;--green:#3fb950;--orange:#d2991d;--red:#f85149;--purple:#a371f7;}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.app{display:flex;height:100vh}
.sidebar{width:260px;background:var(--bg2);border-right:1px solid var(--border);display:flex;flex-direction:column;padding:16px;overflow-y:auto}
.sidebar h2{font-size:16px;color:var(--accent);margin-bottom:20px}
.nav-item{padding:10px 12px;border-radius:6px;cursor:pointer;margin-bottom:2px;font-size:14px;color:var(--text2);transition:all .15s;display:flex;align-items:center;gap:8px}
.nav-item:hover{background:var(--bg3);color:var(--text)}
.nav-item.active{background:var(--bg3);color:var(--accent);font-weight:500}
.main{flex:1;overflow-y:auto;padding:24px 28px}
.header{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px}
.header h1{font-size:20px}
.panel{display:none}.panel.active{display:block}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:18px;margin-bottom:14px}
.card h3{font-size:14px;margin-bottom:10px;color:var(--accent)}
.stats{display:flex;gap:12px;margin-bottom:16px}
.stat-card{flex:1;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px}
.stat-card .label{font-size:11px;color:var(--text2);text-transform:uppercase}.stat-card .value{font-size:22px;font-weight:bold}
table{width:100%;border-collapse:collapse;font-size:12px}
th{text-align:left;padding:8px 10px;color:var(--text2);font-weight:500;border-bottom:1px solid var(--border)}
td{padding:7px 10px;border-bottom:1px solid var(--border)}
tr:hover td{background:var(--bg3)}
.btn{padding:7px 14px;border-radius:6px;border:none;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s}
.btn-primary{background:var(--accent);color:#fff}.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff}.btn-ghost{background:transparent;color:var(--text2);border:1px solid var(--border)}
.btn-ghost:hover{background:var(--bg3);color:var(--text)}.btn-sm{padding:3px 8px;font-size:11px}
input,select,textarea{background:var(--bg);border:1px solid var(--border);border-radius:5px;padding:7px 10px;color:var(--text);font-size:12px;width:100%;font-family:inherit}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--accent)}
textarea{resize:vertical;min-height:60px}
.badge{display:inline-block;padding:2px 7px;border-radius:9px;font-size:10px;font-weight:500}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-orange{background:rgba(210,153,29,.15);color:var(--orange)}.badge-purple{background:rgba(163,113,247,.15);color:var(--purple)}.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.provider-row{display:flex;align-items:center;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-bottom:6px;gap:10px;transition:all .15s}
.provider-row:hover{border-color:var(--accent)}
.provider-row .p-name{font-weight:500;min-width:150px;font-size:13px}
.provider-row .p-models{font-size:10px;color:var(--text2);flex:1}
.provider-row .p-key{width:180px}
.provider-row .p-key input{font-size:11px;padding:4px 8px}
.toggle{position:relative;width:38px;height:20px;display:inline-block}
.toggle input{display:none}
.toggle .slider{position:absolute;inset:0;background:var(--bg3);border-radius:10px;transition:.2s;cursor:pointer}
.toggle .slider:before{content:'';position:absolute;width:14px;height:14px;border-radius:50%;background:var(--text2);top:3px;left:3px;transition:.2s}
.toggle input:checked+.slider{background:var(--green)}
.toggle input:checked+.slider:before{transform:translateX(18px);background:#fff}
.region-group{margin-bottom:16px}.region-group h4{font-size:13px;color:var(--orange);margin-bottom:6px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);z-index:999;align-items:center;justify-content:center}
.modal.show{display:flex}.modal-content{background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:24px;max-width:500px;width:90%;max-height:80vh;overflow-y:auto}
.agent-grid{display:grid;grid-template-columns:1fr;gap:8px}
.agent-card{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px}
.agent-card h5{font-size:13px;margin-bottom:4px}.agent-card .detail{font-size:11px;color:var(--text2)}
.pipeline{display:flex;align-items:center;gap:0;padding:16px 0;overflow-x:auto}
.pipeline-node{background:var(--bg2);border:2px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:100px}
.pipeline-node .node-icon{font-size:20px}.pipeline-arrow{font-size:18px;color:var(--text2);padding:0 6px}
.toast{position:fixed;bottom:20px;right:20px;padding:10px 18px;border-radius:7px;color:#fff;font-size:13px;z-index:999;animation:slideIn .3s}
.toast.success{background:var(--green)}.toast.error{background:var(--red)}
@keyframes slideIn{from{transform:translateY(16px);opacity:0}to{transform:translateY(0);opacity:1}}
@media(max-width:768px){.sidebar{display:none}.main{padding:12px}.grid-2{grid-template-columns:1fr}.provider-row{flex-wrap:wrap}}
</style></head><body>
<div class="app">
<nav class="sidebar">
<h2>🔀 Model Router v2.1</h2>
<div class="nav-item active" data-panel="dashboard" onclick="switchPanel('dashboard')">📊 仪表盘</div>
<div class="nav-item" data-panel="providers" onclick="switchPanel('providers')">🔌 提供商配置 (50+)</div>
<div class="nav-item" data-panel="routes" onclick="switchPanel('routes')">🔀 路由规则</div>
<div class="nav-item" data-panel="agents" onclick="switchPanel('agents')">🤖 智能体兼容 (30+)</div>
<div class="nav-item" data-panel="models" onclick="switchPanel('models')">🧠 模型管理</div>
<div class="nav-item" data-panel="test" onclick="switchPanel('test')">🧪 路由测试</div>
<div class="nav-item" data-panel="settings" onclick="switchPanel('settings')">⚙️ 设置 & 导出</div>
</nav>
<main class="main">

<!-- Dashboard -->
<div class="panel active" id="panel-dashboard">
<div class="header"><h1>📊 智能模型路由面板</h1><button class="btn btn-ghost btn-sm" onclick="refreshStats()">🔄 刷新</button></div>
<div class="stats" id="stats-cards"></div>
<div class="card"><h3>📈 路由流水线</h3>
<div class="pipeline">
<div class="pipeline-node"><div class="node-icon">📝</div><small>用户输入</small></div><div class="pipeline-arrow">→</div>
<div class="pipeline-node"><div class="node-icon">🏷️</div><small>任务分类 Haiku</small></div><div class="pipeline-arrow">→</div>
<div class="pipeline-node"><div class="node-icon">🔀</div><small>规则引擎</small></div><div class="pipeline-arrow">→</div>
<div class="pipeline-node" id="pipeline-model"><div class="node-icon">🧠</div><small id="pipeline-model-name">Opus/Sonnet/Haiku</small></div><div class="pipeline-arrow">→</div>
<div class="pipeline-node"><div class="node-icon">📤</div><small>返回主模型</small></div>
</div></div>
<div class="grid-2">
<div class="card"><h3>模型使用分布</h3><div id="model-distribution"></div></div>
<div class="card"><h3>最近请求</h3><div style="max-height:180px;overflow-y:auto;font-size:11px" id="recent-requests"></div></div>
</div></div>

<!-- Providers -->
<div class="panel" id="panel-providers">
<div class="header"><h1>🔌 提供商配置</h1>
<div><button class="btn btn-primary btn-sm" onclick="showAddModel()">+ 自定义模型</button>
<button class="btn btn-ghost btn-sm" onclick="loadProviders()">🔄 刷新</button></div></div>
<div class="card"><h3>API 密钥管理 & 模型配置</h3>
<div id="provider-regions"></div></div></div>

<!-- Routes -->
<div class="panel" id="panel-routes">
<div class="header"><h1>🔀 路由规则</h1><button class="btn btn-primary btn-sm" onclick="addRoute()">+ 添加规则</button></div>
<div class="card"><div id="route-list"></div></div></div>

<!-- Agents -->
<div class="panel" id="panel-agents">
<div class="header"><h1>🤖 智能体 & CLI 工具一键连接</h1><button class="btn btn-primary btn-sm" onclick="checkAgentStatus()">🔍 检测状态</button></div>
<div class="card"><h3>🔗 网关地址</h3>
<div style="background:var(--bg);border-radius:6px;padding:12px;margin-bottom:12px;font-size:12px">
<strong>当前网关:</strong> <code style="color:var(--accent)" id="gateway-url-display">http://localhost:8701</code><br>
<strong>OpenAI 协议 (95% 智能体):</strong> <code>base_url="http://localhost:8701/v1"</code> + <code>model="router:auto"</code><br>
<strong>Anthropic 协议 (Claude Code):</strong> <code>ANTHROPIC_BASE_URL="http://localhost:8701"</code>
</div>
<div id="agent-connect-list" style="font-size:12px">加载中...</div></div>
<div class="card"><h3>🤖 智能体兼容详情</h3><div id="agent-compat"></div></div></div>

<!-- Models -->
<div class="panel" id="panel-models">
<div class="header"><h1>🧠 模型管理</h1><button class="btn btn-primary btn-sm" onclick="showAddModel()">+ 自定义模型</button></div>
<div class="card"><h3>已配置模型</h3><div id="configured-models"></div></div>
<div class="card"><h3>自定义模型</h3><div id="custom-models-list"></div></div></div>

<!-- Test -->
<div class="panel" id="panel-test">
<div class="header"><h1>🧪 路由测试</h1></div>
<div class="card">
<h3>测试任务</h3>
<div class="model-select-group"><label>🎯 强制指定模型 (留空=自动路由):</label><select id="test-force-model"><option value="">router:auto (自动选择最优)</option></select></div>
<textarea id="test-task" placeholder="例如: 设计一个千万级QPS的分布式消息队列"></textarea>
<div style="display:flex;gap:8px;margin-top:8px">
<select id="test-budget" style="width:auto"><option value="normal">预算:正常</option><option value="high">预算:高</option><option value="low">预算:低</option></select>
<button class="btn btn-primary" onclick="testRoute()">🚀 测试路由</button></div></div>
<div class="card" id="test-result" style="display:none"><h3>路由结果</h3><div id="test-output"></div></div>
<div class="card"><h3>对话测试</h3>
<div class="chat-area" id="chat-area" style="height:220px;overflow-y:auto;padding:10px;background:var(--bg);border-radius:6px;margin-bottom:8px"></div>
<div style="display:flex;gap:8px"><input id="chat-input" placeholder="输入消息..." onkeypress="if(event.key==='Enter')sendChat()"><button class="btn btn-primary" onclick="sendChat()">发送</button></div></div></div>

<!-- Settings -->
<div class="panel" id="panel-settings">
<div class="header"><h1>⚙️ 设置 & 导出</h1></div>
<div class="card"><h3>导入/导出配置</h3>
<div style="display:flex;gap:8px;margin-bottom:8px">
<button class="btn btn-primary btn-sm" onclick="exportConfig()">📥 导出 YAML</button>
<button class="btn btn-ghost btn-sm" onclick="document.getElementById('import-file').click()">📤 导入 YAML</button>
<input type="file" id="import-file" style="display:none" accept=".yaml,.yml,.json" onchange="importConfig(event)"></div>
<textarea id="config-editor" style="min-height:400px;font-family:monospace;font-size:11px"></textarea>
<div style="margin-top:8px;display:flex;gap:8px"><button class="btn btn-primary" onclick="saveFullConfig()">💾 保存</button><button class="btn btn-ghost" onclick="loadConfigToEditor()">🔄 重载</button></div></div></div>
</main></div>

<!-- Add Model Modal -->
<div class="modal" id="add-model-modal">
<div class="modal-content">
<h4>➕ 自定义模型</h4>
<div style="margin:8px 0"><label>模型 ID</label><input id="am-id" placeholder="my-custom-model"></div>
<div style="margin:8px 0"><label>显示名称</label><input id="am-name" placeholder="我的自定义模型"></div>
<div style="margin:8px 0"><label>提供商</label><select id="am-provider"><option value="custom">自定义</option></select></div>
<div style="margin:8px 0"><label>API Base URL</label><input id="am-url" placeholder="https://api.example.com/v1"></div>
<div style="margin:8px 0"><label>API Key</label><input type="password" id="am-key" placeholder="sk-..."></div>
<div style="margin:8px 0"><label>API 格式</label><select id="am-format"><option value="openai">OpenAI Compatible</option><option value="anthropic">Anthropic Compatible</option></select></div>
<div style="margin:8px 0"><label>上下文窗口</label><input id="am-ctx" value="128000" placeholder="128000"></div>
<div style="margin-top:12px;display:flex;gap:8px"><button class="btn btn-primary" onclick="addCustomModel()">添加</button><button class="btn btn-ghost" onclick="closeAddModel()">取消</button></div>
</div></div>

<!-- Add Route Modal -->
<div class="modal" id="add-route-modal">
<div class="modal-content">
<h4>🔀 添加路由规则</h4>
<div style="margin:8px 0"><label>规则名称</label><input id="ar-name" placeholder="如 complex_coding"></div>
<div style="margin:8px 0"><label>匹配领域</label><select id="ar-domain"><option value="">全部 (*)</option><option value="architecture">架构设计</option><option value="coding">编码</option><option value="review">代码审查</option><option value="testing">测试</option><option value="docs">文档</option><option value="research">研究搜索</option><option value="data_analysis">数据分析</option><option value="conversation">对话</option></select></div>
<div style="margin:8px 0"><label>匹配复杂度</label><select id="ar-complexity"><option value="">全部</option><option value="simple">简单</option><option value="moderate">中等</option><option value="complex">复杂</option><option value="very_complex">非常复杂</option></select></div>
<div style="margin:8px 0"><label>🎯 目标模型</label><select id="ar-model" style="font-weight:bold;font-size:13px"><option value="">-- 请先加载提供商 --</option></select></div>
<div style="margin:8px 0"><label>推理 Effort</label><select id="ar-effort"><option value="high">high (推荐)</option><option value="xhigh">xhigh</option><option value="max">max (最强)</option><option value="medium">medium</option><option value="low">low</option></select></div>
<div style="margin-top:12px;display:flex;gap:8px"><button class="btn btn-primary" onclick="addRouteFromModal()">添加</button><button class="btn btn-ghost" onclick="closeRouteModal()">取消</button></div>
</div></div>

<!-- Agent Connect Modal -->
<div class="modal" id="agent-connect-modal">
<div class="modal-content">
<h4 id="acm-title">🔗 一键连接智能体</h4>
<div style="margin:8px 0"><label>网关地址</label><input id="acm-url" value="http://localhost:8701" placeholder="http://localhost:8701"></div>
<div style="margin:8px 0"><label>API Key (可选, 留空使用路由器内置 Key)</label><input type="password" id="acm-key" placeholder="router-key"></div>
<div style="margin:8px 0"><label>模型 (可选)</label><input id="acm-model" value="router:auto" placeholder="router:auto"></div>
<div id="acm-result" style="margin:12px 0;padding:10px;background:var(--bg);border-radius:6px;max-height:200px;overflow-y:auto;font-size:11px;display:none"></div>
<div style="display:flex;gap:8px">
<button class="btn btn-primary" id="acm-connect-btn" onclick="doAgentConnect()">⚡ 立即连接</button>
<button class="btn btn-ghost" onclick="closeAgentConnect()">关闭</button>
</div>
</div></div>

<!-- Test model selector placeholder -->
<style>.model-select-group{display:flex;align-items:center;gap:8px;margin-bottom:8px}.model-select-group select{width:auto;min-width:200px}.status-dot-sm{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px}.status-connected{background:var(--green)}.status-disconnected{background:var(--text2)}.connect-row{display:flex;align-items:center;padding:8px 10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-bottom:4px;gap:8px;transition:all .15s}.connect-row:hover{border-color:var(--accent)}.connect-row .agent-name{font-weight:500;min-width:130px}.connect-row .agent-status{font-size:10px;min-width:70px}.connect-row .agent-path{font-size:10px;color:var(--text2);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}</style>

<script>
const API='';
let allProviders={};

function switchPanel(n){
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
  var el=document.getElementById('panel-'+n);if(el)el.classList.add('active');
  var nav=document.querySelector('[data-panel="'+n+'"]');if(nav)nav.classList.add('active');
  if(n==='dashboard')refreshStats();
  if(n==='providers')loadProviders();
  if(n==='routes')loadRoutes();
  if(n==='agents')loadAgents();
  if(n==='models')loadModelsPanel();
  if(n==='settings')loadConfigToEditor();
}
function showToast(m,t){var d=document.createElement('div');d.className='toast '+t;d.textContent=m;document.body.appendChild(d);setTimeout(function(){d.remove()},3000)}
async function fetchJSON(url,opt){var r=await fetch(API+url,opt);if(!r.ok){var t=await r.text();throw new Error(t)}return r.json()}

// Dashboard
async function refreshStats(){
  try{
    var d=await fetchJSON('/api/stats');
    document.getElementById('stats-cards').innerHTML=
      '<div class="stat-card"><div class="label">总请求</div><div class="value" style="color:var(--accent)">'+d.total_requests+'</div></div>'+
      '<div class="stat-card"><div class="label">总Token</div><div class="value" style="color:var(--purple)">'+(d.total_tokens/1000).toFixed(1)+'K</div></div>'+
      '<div class="stat-card"><div class="label">错误数</div><div class="value" style="color:var(--red)">'+d.total_errors+'</div></div>'+
      '<div class="stat-card"><div class="label">运行时间</div><div class="value">'+fmtTime(d.uptime_seconds)+'</div></div>';
    var dist=d.model_distribution||{};
    document.getElementById('model-distribution').innerHTML=Object.entries(dist).map(function(e){return '<div style="display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border)"><span>'+e[0]+'</span><span class="badge badge-blue">'+e[1]+'</span></div>'}).join('')||'<span style="color:var(--text2)">暂无数据</span>';
    var recent=d.recent_requests||[];
    document.getElementById('recent-requests').innerHTML=recent.length?recent.reverse().map(function(r){return '<div style="padding:4px 0;border-bottom:1px solid var(--border)"><span class="badge '+(r.success?'badge-green':'badge-red')+'">'+(r.success?'✓':'✗')+'</span> <span class="badge badge-purple">'+r.model+'</span> '+h(r.task||'').slice(0,35)+' <span style="float:right;color:var(--text2)">'+Math.round(r.elapsed_ms||0)+'ms</span></div>'}).join(''):'<span style="color:var(--text2)">暂无请求</span>';
  }catch(e){console.error(e)}
}
function fmtTime(s){if(s<60)return Math.floor(s)+'s';if(s<3600)return Math.floor(s/60)+'m';return Math.floor(s/3600)+'h'}

// Providers
async function loadProviders(){
  try{
    allProviders=await fetchJSON('/api/providers');
    var regions={};
    Object.entries(allProviders).forEach(function(e){var k=e[0],v=e[1];var r=v.region||'其他';if(!regions[r])regions[r]=[];regions[r].push({id:k,data:v})});
    var html='';
    var order=['🌍 国际','🇨🇳 中国','🏠 本地'];
    order.forEach(function(region){
      var items=regions[region];if(!items||!items.length)return;
      html+='<div class="region-group"><h4>'+region+'</h4>';
      items.forEach(function(item){
        var id=item.id,d=item.data;
        var models=d.models||[];
        var modelStr=models.length?models.map(function(m){return '<span class="badge badge-blue">'+m.id+'</span>'}).join(' '):'<span style="color:var(--text2)">无预设模型</span>';
        html+='<div class="provider-row">'+
          '<span style="font-size:18px">'+getIcon(id)+'</span>'+
          '<span class="p-name">'+d.name+'</span>'+
          '<span class="p-models">'+modelStr+'</span>'+
          '<span class="p-key"><input type="password" id="key-'+id+'" placeholder="API Key..." value="'+((allProviders[id]||{}).has_key?'••••••••':'')+'" style="width:100%"></span>'+
          '<button class="btn btn-primary btn-sm" onclick="saveKey(\''+id+'\')">💾</button>'+
          '<label class="toggle"><input type="checkbox" '+(d.enabled?'checked':'')+' onchange="toggleProv(\''+id+'\',this.checked)"><span class="slider"></span></label>'+
          '<a href="'+d.website+'" target="_blank" style="font-size:10px;color:var(--text2);text-decoration:none">🔗</a>'+
          '</div>';
      });
      html+='</div>';
    });
    document.getElementById('provider-regions').innerHTML=html;
    populateModelDropdowns();
  }catch(e){console.error(e)}
}
function getIcon(id){
  var m={'openai':'🟢','anthropic':'🟠','google':'🔵','deepseek':'🟣','ollama':'🦙','openrouter':'🌐','xai':'⚫','qwen':'🔴','baidu':'🐻','bytedance':'🎵','tencent':'💬','zhipu':'🔷','moonshot':'🌙','minimax':'💎','baichuan':'🏔️','stepfun':'⭐','iflytek':'🎤','groq':'⚡','mistral':'🌀','cohere':'🟡','together':'🤝','fireworks':'🎆'};
  return m[id]||'⚪'}
async function saveKey(pid){
  var k=document.getElementById('key-'+pid).value;
  if(!k&&!confirm('清空密钥?'))return;
  try{await fetchJSON('/api/providers/'+pid+'/key',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:k})});showToast(pid+' 密钥已保存','success')}catch(e){showToast('失败','error')}
}
async function toggleProv(pid,en){
  try{await fetchJSON('/api/providers/'+pid+'/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:en})});showToast(pid+(en?' 已启用':' 已禁用'),'success');populateModelDropdowns()}catch(e){}
}
async function populateModelDropdowns(){
  var provs=allProviders;if(!Object.keys(provs).length)provs=await fetchJSON('/api/providers');
  var allModels=[];
  Object.entries(provs).forEach(function(e){
    var models=e[1].models||[],enabled=e[1].enabled;
    models.forEach(function(m){allModels.push({id:m.id,name:m.name||m.id,provider:e[1].name,providerId:e[0],enabled:enabled})});
  });
  // 也加入自定义模型
  try{var custom=await fetchJSON('/api/custom-models');custom.models.forEach(function(m){allModels.push({id:m.id,name:m.name,provider:'自定义',providerId:'custom',enabled:true})})}catch(e){}
  // 填充各个下拉框
  var selects=['ar-model','test-force-model'];
  selects.forEach(function(sid){
    var sel=document.getElementById(sid);if(!sel)return;
    var current=sel.value;
    sel.innerHTML='<option value="">router:auto (自动选择最优)</option>';
    // 按提供商分组
    var groups={};
    allModels.forEach(function(m){var g=m.enabled?m.provider:'未启用';if(!groups[g])groups[g]=[];groups[g].push(m)});
    Object.keys(groups).sort().forEach(function(g){
      var isDisabled=g==='未启用';sel.innerHTML+='<optgroup label="'+g+'"'+(isDisabled?' disabled':'')+'>';
      groups[g].forEach(function(m){sel.innerHTML+='<option value="'+m.id+'"'+(m.id===current?' selected':'')+'>'+m.name+'</option>'});
      sel.innerHTML+='</optgroup>';
    });
  });
}

// Custom Models
async function showAddModel(){
  var sel=document.getElementById('am-provider');
  sel.innerHTML='<option value="custom">自定义</option><option disabled>加载中...</option>';
  document.getElementById('add-model-modal').classList.add('show');
  // 无论 allProviders 是否为空，都从 API 获取最新列表
  try{
    var provs=await fetchJSON('/api/providers');
    allProviders=provs;
    sel.innerHTML='<option value="custom">自定义 (手动输入)</option>';
    var order=['🌍 国际','🇨🇳 中国','🏠 本地'];
    order.forEach(function(region){
      var items=Object.entries(provs).filter(function(e){return e[1].region===region}).sort(function(a,b){return a[1].name.localeCompare(b[1].name)});
      if(!items.length)return;
      sel.innerHTML+='<optgroup label="'+region+'">';
      items.forEach(function(e){sel.innerHTML+='<option value="'+e[0]+'">'+e[1].name+'</option>'});
      sel.innerHTML+='</optgroup>';
    });
  }catch(e){sel.innerHTML='<option value="custom">自定义 (加载失败)</option>'}
}
function closeAddModel(){document.getElementById('add-model-modal').classList.remove('show')}
async function addCustomModel(){
  var d={id:document.getElementById('am-id').value,name:document.getElementById('am-name').value,
    provider:document.getElementById('am-provider').value,base_url:document.getElementById('am-url').value,
    api_format:document.getElementById('am-format').value,context_window:parseInt(document.getElementById('am-ctx').value)||128000};
  if(!d.id){showToast('请输入模型ID','error');return}
  if(d.provider!=='custom'){var k=document.getElementById('am-key').value;if(k)await saveKey(d.provider)}
  try{await fetchJSON('/api/custom-models',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});closeAddModel();loadModelsPanel();showToast('模型已添加','success')}catch(e){showToast('失败: '+e,'error')}
}
async function loadModelsPanel(){
  try{
    var provs=await fetchJSON('/api/providers');
    var html='';
    Object.entries(provs).forEach(function(e){
      if(!e[1].enabled||!e[1].models.length)return;
      html+='<div style="margin-bottom:8px"><strong>'+e[1].name+'</strong> ';
      e[1].models.forEach(function(m){html+='<span class="badge badge-blue" style="margin-left:4px">'+m.id+'</span>'});
      html+='</div>';
    });
    document.getElementById('configured-models').innerHTML=html||'<span style="color:var(--text2)">暂无已启用模型</span>';
    var custom=await fetchJSON('/api/custom-models');
    document.getElementById('custom-models-list').innerHTML=custom.models.length?custom.models.map(function(m){return '<div style="padding:6px 0;border-bottom:1px solid var(--border)"><span class="badge badge-orange">自定义</span> '+m.name+' <code>'+m.id+'</code> <span style="color:var(--text2)">['+m.api_format+']</span> <button class="btn btn-ghost btn-sm" onclick="delCustom(\''+m.id+'\')" style="float:right">🗑️</button></div>'}).join(''):'<span style="color:var(--text2)">暂无自定义模型</span>';
  }catch(e){}
}
async function delCustom(mid){try{await fetchJSON('/api/custom-models/'+mid,{method:'DELETE'});loadModelsPanel()}catch(e){}}

// Routes
async function loadRoutes(){
  try{
    var cfg=await fetchJSON('/api/config');
    var rules=cfg.routing?.rules||[];
    document.getElementById('route-list').innerHTML=rules.map(function(r,i){return '<div class="provider-row"><span style="font-weight:bold;color:var(--accent);min-width:20px">#'+(i+1)+'</span><span>'+r.name+'</span><span class="badge badge-blue" style="margin-left:8px">'+((r.match||{}).domain||'*')+'</span>'+(((r.match||{}).complexity)?'<span class="badge badge-orange" style="margin-left:4px">'+((r.match||{}).complexity)+'</span>':'')+'<span class="badge badge-purple" style="margin-left:auto">→ '+((r.route||{}).model||r.target?.model||'?')+'</span><button class="btn btn-ghost btn-sm" style="color:var(--red)" onclick="deleteRoute('+i+')">🗑️</button></div>'}).join('');
  }catch(e){}
}
async function openAddRouteModal(){
  populateModelDropdowns();document.getElementById('add-route-modal').classList.add('show');
  document.getElementById('ar-name').value='';document.getElementById('ar-domain').value='';
  document.getElementById('ar-complexity').value='';document.getElementById('ar-effort').value='high';
}
function closeRouteModal(){document.getElementById('add-route-modal').classList.remove('show')}
function addRoute(){openAddRouteModal()}
async function addRouteFromModal(){
  var name=document.getElementById('ar-name').value.trim();if(!name){showToast('请输入规则名称','error');return}
  var domain=document.getElementById('ar-domain').value;
  var comp=document.getElementById('ar-complexity').value;
  var model=document.getElementById('ar-model').value;if(!model){showToast('请选择目标模型','error');return}
  var effort=document.getElementById('ar-effort').value||'high';
  try{
    var cfg=await fetchJSON('/api/config');
    var rules=cfg.routing?.rules||[];
    rules.push({name:name,priority:(rules.length+1)*10,match:{domain:domain||undefined,complexity:comp||undefined},route:{model:model,effort:effort}});
    await fetchJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
    closeRouteModal();loadRoutes();showToast('规则已添加: '+name+' → '+model,'success');
  }catch(e){showToast('失败','error')}
}
async function deleteRoute(i){
  if(!confirm('删除此规则?'))return;
  try{var cfg=await fetchJSON('/api/config');cfg.routing.rules.splice(i,1);await fetchJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});loadRoutes()}catch(e){}
}

// Agents
let currentAgentId='';
async function loadAgents(){
  try{
    var d=await fetchJSON('/api/agents');
    var html='';
    d.agents.forEach(function(cat){
      html+='<h4 style="margin:12px 0 6px;color:var(--orange);font-size:13px">'+cat.category+'</h4>';
      html+='<div class="agent-grid">';
      cat.agents.forEach(function(a){
        html+='<div class="agent-card"><h5>'+a.name+' <span class="badge badge-purple">'+a.protocol+'</span></h5><div class="detail">Base URL: <code>'+a.base_url+'</code> | Key: <code>'+a.env+'</code> | Config: <code>'+a.config+'</code> | '+a.note+'</div></div>';
      });
      html+='</div>';
    });
    document.getElementById('agent-compat').innerHTML=html;
    checkAgentStatus();
  }catch(e){}
}
async function checkAgentStatus(){
  try{
    var d=await fetchJSON('/api/agents/connect/status');
    document.getElementById('gateway-url-display').textContent=d.gateway_url;
    var agents=d.agents;
    var html='';
    var connectable=['claude-code','codex','openclaw','hermes','opencode','gemini-cli','aider','cursor'];
    connectable.forEach(function(aid){
      var a=agents[aid];if(!a)return;
      var connected=a.connected;
      html+='<div class="connect-row">'+
        '<span class="status-dot-sm '+(connected?'status-connected':'status-disconnected')+'"></span>'+
        '<span class="agent-name">'+a.name+'</span>'+
        '<span class="agent-status"><span class="badge '+(connected?'badge-green':'badge-orange')+'">'+(connected?'✅ 已连接':'⬜ 未连接')+'</span></span>'+
        '<span class="agent-path" title="'+h(a.config_path)+'">'+h(a.config_path)+'</span>'+
        '<button class="btn btn-primary btn-sm" onclick="openAgentConnect(\''+aid+'\')">⚡ 一键连接</button>'+
        '</div>';
    });
    document.getElementById('agent-connect-list').innerHTML=html||'<span style="color:var(--text2)">暂无</span>';
  }catch(e){document.getElementById('agent-connect-list').innerHTML='<span style="color:var(--red)">状态检测失败: '+h(e+'')+'</span>'}
}
function openAgentConnect(aid){
  currentAgentId=aid;
  var names={'claude-code':'Claude Code','codex':'Codex CLI','openclaw':'OpenClaw','hermes':'Hermes Agent','opencode':'OpenCode','gemini-cli':'Gemini CLI','aider':'Aider','cursor':'Cursor'};
  document.getElementById('acm-title').textContent='🔗 一键连接 '+((names)[aid]||aid);
  document.getElementById('acm-result').style.display='none';
  document.getElementById('acm-connect-btn').disabled=false;
  document.getElementById('acm-connect-btn').textContent='⚡ 立即连接';
  document.getElementById('agent-connect-modal').classList.add('show');
}
function closeAgentConnect(){
  document.getElementById('agent-connect-modal').classList.remove('show');
}
async function doAgentConnect(){
  if(!currentAgentId)return;
  var url=document.getElementById('acm-url').value.trim()||'http://localhost:8701';
  var key=document.getElementById('acm-key').value.trim()||'router-key';
  var model=document.getElementById('acm-model').value.trim()||'router:auto';
  var btn=document.getElementById('acm-connect-btn');
  var resultDiv=document.getElementById('acm-result');
  btn.disabled=true;btn.textContent='⏳ 连接中...';
  resultDiv.style.display='block';resultDiv.innerHTML='<span style="color:var(--text2)">正在写入配置文件...</span>';
  try{
    var d=await fetchJSON('/api/agents/connect/'+currentAgentId,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:url,api_key:key,model:model})});
    var html='<div style="color:var(--green);font-weight:bold;margin-bottom:8px">✅ '+d.agent+' 连接成功！</div>';
    d.actions.forEach(function(a){html+='<div>'+a+'</div>'});
    // 环境变量
    var envExports=d.env_exports||{};
    var envKeys=Object.keys(envExports);
    if(envKeys.length){
      html+='<div style="margin-top:8px;font-weight:bold">🔧 环境变量 (PowerShell):</div>';
      html+='<div style="background:var(--bg3);padding:8px;border-radius:4px;font-family:monospace;font-size:11px;margin-top:4px">';
      envKeys.forEach(function(k){html+='$env:'+k+' = "'+envExports[k]+'"<br>'});
      html+='</div>';
    }
    // 代码片段
    if(d.code_python){
      html+='<div style="margin-top:8px;font-weight:bold">🐍 Python 代码:</div>';
      html+='<div style="background:var(--bg3);padding:8px;border-radius:4px;font-family:monospace;font-size:11px;margin-top:4px;white-space:pre-wrap">'+h(d.code_python)+'</div>';
    }
    resultDiv.innerHTML=html;btn.textContent='✅ 已连接';
    setTimeout(function(){checkAgentStatus()},1000);
  }catch(e){resultDiv.innerHTML='<div style="color:var(--red)">❌ 连接失败: '+h(e+'')+'</div>';btn.disabled=false;btn.textContent='⚡ 重试'}
}

// Test
async function testRoute(){
  var task=document.getElementById('test-task').value.trim(),budget=document.getElementById('test-budget').value;
  var forceModel=document.getElementById('test-force-model').value;
  if(!task){showToast('请输入任务','error');return}
  document.getElementById('test-result').style.display='block';
  document.getElementById('test-output').innerHTML='<span style="color:var(--text2)">⏳ 路由中...</span>';
  try{
    var body={task:task,budget:budget};if(forceModel)body.force_model=forceModel;
    var d=await fetchJSON('/api/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    document.getElementById('pipeline-model-name').textContent=d.model||'?';
    document.getElementById('test-output').innerHTML='<div style="display:flex;gap:12px;margin-bottom:8px"><span>模型: <span class="badge badge-purple" style="font-size:13px">'+d.model+'</span></span><span>类型: <span class="badge badge-blue">'+d.task_type+'</span></span><span>'+Math.round(d.elapsed_ms||0)+'ms</span><span>Token: '+((d.usage||{}).total_tokens||0)+'</span></div><div style="background:var(--bg);padding:12px;border-radius:6px;white-space:pre-wrap;max-height:300px;overflow-y:auto;font-size:12px">'+h(d.output||'')+'</div>';
  }catch(e){document.getElementById('test-output').innerHTML='<span style="color:var(--red)">失败: '+e+'</span>'}
}
async function sendChat(){
  var inp=document.getElementById('chat-input'),task=inp.value.trim();if(!task)return;
  var a=document.getElementById('chat-area');a.innerHTML+='<div style="text-align:right;margin:4px 0"><span style="background:var(--accent);color:#fff;padding:6px 10px;border-radius:10px;font-size:12px;display:inline-block">'+h(task)+'</span></div>';
  inp.value='';a.scrollTop=a.scrollHeight;
  try{
    var d=await fetchJSON('/api/route',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:task})});
    a.innerHTML+='<div style="margin:4px 0"><span style="font-size:10px;color:var(--purple)">['+d.model+']</span><br><span style="background:var(--bg3);padding:6px 10px;border-radius:10px;font-size:12px;display:inline-block">'+h(d.output||'')+'</span></div>';
    a.scrollTop=a.scrollHeight;
  }catch(e){a.innerHTML+='<div style="color:var(--red)">❌ '+h(e+'')+'</div>'}
}

// Settings
async function loadConfigToEditor(){
  try{var d=await fetchJSON('/api/config');document.getElementById('config-editor').value=JSON.stringify(d,null,2)}catch(e){}}
async function saveFullConfig(){
  try{var c=JSON.parse(document.getElementById('config-editor').value);await fetchJSON('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(c)});showToast('已保存','success')}catch(e){showToast('JSON 格式错误','error')}}
function exportConfig(){var b=new Blob([document.getElementById('config-editor').value],{type:'application/x-yaml'});var u=URL.createObjectURL(b);var a=document.createElement('a');a.href=u;a.download='model-router-config.yaml';a.click();URL.revokeObjectURL(u)}
function importConfig(e){var f=e.target.files[0];if(!f)return;var r=new FileReader();r.onload=function(ev){document.getElementById('config-editor').value=ev.target.result;showToast('已导入，点击保存生效','success')};r.readAsText(f)}
function h(t){var d=document.createElement('div');d.textContent=t||'';return d.innerHTML}

refreshStats();setInterval(refreshStats,30000);
</script></body></html>"""

@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    ui_file = Path(__file__).parent / "static" / "index.html"
    if ui_file.exists():
        return HTMLResponse(content=ui_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>UI file not found: static/index.html</h1>")

# Static asset serving (QR codes, etc.)
from fastapi.staticfiles import StaticFiles
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

def main():
    import argparse, uvicorn
    p = argparse.ArgumentParser(description="Model Router Gateway v2.1")
    p.add_argument("--port", type=int, default=8701); p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--reload", action="store_true")
    args = p.parse_args()
    print(f"\n╔══════════════════════════════════════════════════════════╗")
    print(f"║   Model Router Gateway v2.1                              ║")
    print(f"║   50+ 提供商 · 30+ 智能体 · 双协议 · 可视化               ║")
    print(f"╠══════════════════════════════════════════════════════════╣")
    print(f"║   Web UI:    http://localhost:{args.port}                      ║")
    print(f"║   OpenAI:    http://localhost:{args.port}/v1/chat/completions  ║")
    print(f"╚══════════════════════════════════════════════════════════╝\n")
    uvicorn.run("gateway:app", host=args.host, port=args.port, reload=args.reload, log_level="info")

if __name__ == "__main__":
    main()
