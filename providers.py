"""
多提供商适配器层 (Multi-Provider Adapter Layer)
================================================

支持所有主流 LLM 提供商的统一接口，实现真正的 provider-agnostic。

支持的提供商:
  - OpenAI (GPT-5, GPT-4o, GPT-4o-mini, o4, o3 等)
  - Anthropic (Claude Opus 4.7, Sonnet 4.6, Haiku 4.5 等)
  - Google (Gemini 2.5 Pro, Flash 等)
  - DeepSeek (V4, V3, R1 等)
  - xAI (Grok 4 等)
  - Meta (Llama 4 等, via OpenRouter/Ollama)
  - OpenRouter (400+ 模型的统一入口)
  - Ollama (本地模型)
  - 任何 OpenAI 兼容 API (vLLM, LiteLLM, SGLang, LM Studio 等)

核心设计:
  - 统一 `chat()` 接口，参数自动翻译
  - 协议自动转换 (OpenAI ↔ Anthropic ↔ Gemini)
  - 自动故障转移和重试
  - 内置 Token 计数和成本追踪
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional, Union

import httpx

logger = logging.getLogger(__name__)


# ============================================================
# 通用数据类型
# ============================================================

class ProviderID(str, Enum):
    """提供商标识"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    DEEPSEEK = "deepseek"
    XAI = "xai"
    META = "meta"
    OPENROUTER = "openrouter"
    OLLAMA = "ollama"
    CUSTOM = "custom"


@dataclass
class ModelSpec:
    """统一模型规格"""
    provider: ProviderID
    model_id: str
    display_name: str
    context_window: int
    max_output_tokens: int
    supports_vision: bool = False
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_thinking: bool = False
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    capabilities: set[str] = field(default_factory=set)  # coding, reasoning, vision, etc.


@dataclass
class UnifiedMessage:
    """统一消息格式"""
    role: str  # system, user, assistant, tool
    content: Union[str, list[dict]]
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict]] = None


@dataclass
class UnifiedResponse:
    """统一响应格式"""
    content: str
    model: str
    provider: ProviderID
    usage: dict[str, int] = field(default_factory=dict)
    finish_reason: str = "stop"
    tool_calls: list[dict] = field(default_factory=list)
    elapsed_ms: float = 0
    cost_usd: float = 0.0
    raw_response: Any = None


@dataclass
class UnifiedRequest:
    """统一请求格式"""
    messages: list[UnifiedMessage]
    model: str
    max_tokens: int = 4096
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[str] = None
    stream: bool = False
    thinking: Optional[dict] = None  # {"type": "adaptive"} | {"type": "enabled", "budget_tokens": N}
    effort: Optional[str] = None
    stop: Optional[list[str]] = None
    extra: dict[str, Any] = field(default_factory=dict)


# ============================================================
# 基础适配器抽象
# ============================================================

class BaseProviderAdapter(ABC):
    """所有提供商的基类适配器"""

    provider_id: ProviderID

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))
        return self._client

    @abstractmethod
    async def chat(self, request: UnifiedRequest) -> UnifiedResponse:
        """发送请求并返回统一响应"""
        ...

    @abstractmethod
    async def chat_stream(self, request: UnifiedRequest) -> AsyncIterator[str]:
        """流式请求，逐块返回文本"""
        ...

    @abstractmethod
    def _build_request_payload(self, request: UnifiedRequest) -> dict:
        """将统一请求转换为此提供商的 API 格式"""
        ...

    @abstractmethod
    def _parse_response(self, raw: dict, request: UnifiedRequest) -> UnifiedResponse:
        """将提供商响应转换为统一格式"""
        ...

    async def close(self):
        if self._client:
            await self._client.aclose()

    def _estimate_cost(self, model_spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens / 1_000_000) * model_spec.cost_per_1m_input + \
               (output_tokens / 1_000_000) * model_spec.cost_per_1m_output


# ============================================================
# OpenAI 适配器
# ============================================================

class OpenAIAdapter(BaseProviderAdapter):
    provider_id = ProviderID.OPENAI

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.base_url = base_url or "https://api.openai.com/v1"

    async def chat(self, request: UnifiedRequest) -> UnifiedResponse:
        payload = self._build_request_payload(request)
        start = time.time()

        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = (time.time() - start) * 1000

        return self._parse_response(data, request, elapsed)

    async def chat_stream(self, request: UnifiedRequest) -> AsyncIterator[str]:
        payload = self._build_request_payload(request)
        payload["stream"] = True

        async with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except json.JSONDecodeError:
                        continue

    def _build_request_payload(self, request: UnifiedRequest) -> dict:
        messages = []
        for msg in request.messages:
            formatted: dict = {"role": msg.role, "content": msg.content}
            if msg.name:
                formatted["name"] = msg.name
            if msg.tool_calls:
                formatted["tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                formatted["tool_call_id"] = msg.tool_call_id
            messages.append(formatted)

        payload: dict = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.tools:
            payload["tools"] = [{"type": "function", "function": t} for t in request.tools]
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.stop:
            payload["stop"] = request.stop
        if request.extra:
            payload.update(request.extra)
        return payload

    def _parse_response(self, data: dict, request: UnifiedRequest, elapsed_ms: float = 0) -> UnifiedResponse:
        choice = data["choices"][0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return UnifiedResponse(
            content=message.get("content", ""),
            model=data.get("model", request.model),
            provider=ProviderID.OPENAI,
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=message.get("tool_calls", []),
            elapsed_ms=elapsed_ms,
            raw_response=data,
        )


# ============================================================
# Anthropic 适配器
# ============================================================

class AnthropicAdapter(BaseProviderAdapter):
    provider_id = ProviderID.ANTHROPIC

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.base_url = base_url or "https://api.anthropic.com/v1"
        self._anthropic_version = "2023-06-01"

    async def chat(self, request: UnifiedRequest) -> UnifiedResponse:
        payload = self._build_request_payload(request)
        start = time.time()

        # 分离 system 消息
        system_prompt = None
        if payload.get("system"):
            system_prompt = payload.pop("system")

        if system_prompt:
            payload["system"] = system_prompt

        response = await self.client.post(
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self._anthropic_version,
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = (time.time() - start) * 1000

        return self._parse_response(data, request, elapsed)

    async def chat_stream(self, request: UnifiedRequest) -> AsyncIterator[str]:
        payload = self._build_request_payload(request)
        system_prompt = None
        if payload.get("system"):
            system_prompt = payload.pop("system")
        if system_prompt:
            payload["system"] = system_prompt
        payload["stream"] = True

        async with self.client.stream(
            "POST",
            f"{self.base_url}/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": self._anthropic_version,
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        event = json.loads(data_str)
                        if event.get("type") == "content_block_delta":
                            delta = event.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield delta.get("text", "")
                    except json.JSONDecodeError:
                        continue

    def _build_request_payload(self, request: UnifiedRequest) -> dict:
        # Anthropic API 使用不同的消息格式
        system_content = None
        messages = []
        for msg in request.messages:
            if msg.role == "system":
                system_content = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
            else:
                formatted: dict = {"role": msg.role, "content": msg.content}
                messages.append(formatted)

        payload: dict = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if system_content:
            payload["system"] = system_content

        # Thinking 配置
        if request.thinking:
            payload["thinking"] = request.thinking

        # Effort 配置 (放在 output_config 中)
        if request.effort:
            payload["output_config"] = {"effort": request.effort}

        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.tools:
            payload["tools"] = request.tools
        if request.stop:
            payload["stop_sequences"] = request.stop
        return payload

    def _parse_response(self, data: dict, request: UnifiedRequest, elapsed_ms: float = 0) -> UnifiedResponse:
        content_blocks = data.get("content", [])
        text_content = ""
        tool_calls = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input": block.get("input", {}),
                })

        usage = data.get("usage", {})

        return UnifiedResponse(
            content=text_content,
            model=data.get("model", request.model),
            provider=ProviderID.ANTHROPIC,
            usage={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            finish_reason=data.get("stop_reason", "end_turn"),
            tool_calls=tool_calls,
            elapsed_ms=elapsed_ms,
            raw_response=data,
        )


# ============================================================
# Google Gemini 适配器
# ============================================================

class GeminiAdapter(BaseProviderAdapter):
    provider_id = ProviderID.GOOGLE

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        super().__init__(api_key, base_url)
        self.base_url = base_url or "https://generativelanguage.googleapis.com/v1beta"

    async def chat(self, request: UnifiedRequest) -> UnifiedResponse:
        payload = self._build_request_payload(request)
        start = time.time()

        model_path = f"models/{request.model}:generateContent"
        if request.thinking:
            model_path = f"models/{request.model}:generateContent"  # Gemini thinking via API config

        response = await self.client.post(
            f"{self.base_url}/{model_path}",
            params={"key": self.api_key},
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = (time.time() - start) * 1000

        return self._parse_response(data, request, elapsed)

    async def chat_stream(self, request: UnifiedRequest) -> AsyncIterator[str]:
        payload = self._build_request_payload(request)
        model_path = f"models/{request.model}:streamGenerateContent"

        async with self.client.stream(
            "POST",
            f"{self.base_url}/{model_path}",
            params={"key": self.api_key, "alt": "sse"},
            headers={"Content-Type": "application/json"},
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    try:
                        chunk = json.loads(line[6:])
                        candidates = chunk.get("candidates", [{}])
                        content = candidates[0].get("content", {})
                        parts = content.get("parts", [{}])
                        text = parts[0].get("text", "") if parts else ""
                        if text:
                            yield text
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    def _build_request_payload(self, request: UnifiedRequest) -> dict:
        contents = []
        system_instruction = None

        for msg in request.messages:
            if msg.role == "system":
                system_instruction = {"parts": [{"text": msg.content}]}
            else:
                role = "model" if msg.role == "assistant" else "user"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg.content}] if isinstance(msg.content, str) else msg.content,
                })

        payload: dict = {
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": request.max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if request.temperature is not None:
            payload["generationConfig"]["temperature"] = request.temperature
        if request.top_p is not None:
            payload["generationConfig"]["topP"] = request.top_p
        if request.tools:
            payload["tools"] = [{"function_declarations": request.tools}]
        return payload

    def _parse_response(self, data: dict, request: UnifiedRequest, elapsed_ms: float = 0) -> UnifiedResponse:
        candidates = data.get("candidates", [{}])
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        text = "".join(p.get("text", "") for p in parts)

        usage_data = data.get("usageMetadata", {})

        return UnifiedResponse(
            content=text,
            model=request.model,
            provider=ProviderID.GOOGLE,
            usage={
                "input_tokens": usage_data.get("promptTokenCount", 0),
                "output_tokens": usage_data.get("candidatesTokenCount", 0),
                "total_tokens": usage_data.get("totalTokenCount", 0),
            },
            finish_reason=candidates[0].get("finishReason", "STOP"),
            elapsed_ms=elapsed_ms,
            raw_response=data,
        )


# ============================================================
# OpenAI 兼容适配器 (通用 — 支持 DeepSeek/Ollama/OpenRouter/vLLM 等)
# ============================================================

class OpenAICompatibleAdapter(BaseProviderAdapter):
    """
    通用 OpenAI 兼容 API 适配器

    支持所有遵循 OpenAI API 格式的提供商:
      - DeepSeek (api.deepseek.com)
      - OpenRouter (openrouter.ai/api/v1)
      - Ollama (localhost:11434/v1)
      - vLLM (自定义地址)
      - Together AI, Fireworks, Groq, 等等
    """

    provider_id = ProviderID.CUSTOM

    def __init__(
        self,
        provider_id: ProviderID,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        custom_headers: Optional[dict] = None,
    ):
        super().__init__(api_key, base_url)
        self.provider_id = provider_id
        self.custom_headers = custom_headers or {}

    async def chat(self, request: UnifiedRequest) -> UnifiedResponse:
        payload = self._build_request_payload(request)
        start = time.time()

        headers = {
            "Authorization": f"Bearer {self.api_key or 'ollama'}",
            "Content-Type": "application/json",
            **self.custom_headers,
        }

        response = await self.client.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        elapsed = (time.time() - start) * 1000

        return self._parse_response(data, request, elapsed)

    async def chat_stream(self, request: UnifiedRequest) -> AsyncIterator[str]:
        payload = self._build_request_payload(request)
        payload["stream"] = True

        headers = {
            "Authorization": f"Bearer {self.api_key or 'ollama'}",
            "Content-Type": "application/json",
            **self.custom_headers,
        }

        async with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta and delta["content"]:
                            yield delta["content"]
                    except json.JSONDecodeError:
                        continue

    def _build_request_payload(self, request: UnifiedRequest) -> dict:
        # 与 OpenAI 格式相同
        messages = []
        for msg in request.messages:
            formatted = {"role": msg.role, "content": msg.content}
            if msg.name:
                formatted["name"] = msg.name
            messages.append(formatted)

        payload = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [{"type": "function", "function": t} for t in request.tools]
        return payload

    def _parse_response(self, data: dict, request: UnifiedRequest, elapsed_ms: float = 0) -> UnifiedResponse:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        return UnifiedResponse(
            content=message.get("content", ""),
            model=data.get("model", request.model),
            provider=self.provider_id,
            usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            finish_reason=choice.get("finish_reason", "stop"),
            elapsed_ms=elapsed_ms,
            raw_response=data,
        )


# ============================================================
# 提供商注册表 & 工厂
# ============================================================

class ProviderRegistry:
    """提供商注册表 —— 管理所有可用的提供商适配器"""

    def __init__(self):
        self._adapters: dict[ProviderID, BaseProviderAdapter] = {}
        self._model_registry: dict[str, ModelSpec] = {}

    def register_adapter(self, provider_id: ProviderID, adapter: BaseProviderAdapter):
        self._adapters[provider_id] = adapter

    def register_model(self, spec: ModelSpec):
        self._model_registry[spec.model_id] = spec

    def register_models(self, specs: list[ModelSpec]):
        for spec in specs:
            self.register_model(spec)

    def get_adapter(self, provider_id: ProviderID) -> BaseProviderAdapter:
        if provider_id not in self._adapters:
            raise ValueError(f"未注册的提供商: {provider_id}")
        return self._adapters[provider_id]

    def get_model_spec(self, model_id: str) -> Optional[ModelSpec]:
        return self._model_registry.get(model_id)

    def resolve_provider(self, model_id: str) -> ProviderID:
        """根据模型 ID 解析提供商"""
        spec = self._model_registry.get(model_id)
        if spec:
            return spec.provider

        # 回退: 通过模型名前缀推断
        model_lower = model_id.lower()
        if any(p in model_lower for p in ["gpt", "o1", "o3", "o4", "text-"]):
            return ProviderID.OPENAI
        if any(p in model_lower for p in ["claude"]):
            return ProviderID.ANTHROPIC
        if any(p in model_lower for p in ["gemini"]):
            return ProviderID.GOOGLE
        if any(p in model_lower for p in ["deepseek"]):
            return ProviderID.DEEPSEEK
        if any(p in model_lower for p in ["grok"]):
            return ProviderID.XAI
        if any(p in model_lower for p in ["llama", "llava"]):
            return ProviderID.META

        return ProviderID.CUSTOM

    def list_providers(self) -> list[ProviderID]:
        return list(self._adapters.keys())

    def list_models(self) -> list[ModelSpec]:
        return list(self._model_registry.values())

    def find_models_by_capability(self, capability: str) -> list[ModelSpec]:
        return [m for m in self._model_registry.values() if capability in m.capabilities]

    def find_models_by_provider(self, provider: ProviderID) -> list[ModelSpec]:
        return [m for m in self._model_registry.values() if m.provider == provider]


class ProviderFactory:
    """提供商工厂 —— 便捷创建已配置的适配器"""

    @staticmethod
    def create_registry(
        openai_key: Optional[str] = None,
        anthropic_key: Optional[str] = None,
        google_key: Optional[str] = None,
        deepseek_key: Optional[str] = None,
        openrouter_key: Optional[str] = None,
        ollama_url: str = "http://localhost:11434/v1",
    ) -> ProviderRegistry:
        registry = ProviderRegistry()

        if openai_key:
            registry.register_adapter(ProviderID.OPENAI, OpenAIAdapter(api_key=openai_key))
        if anthropic_key:
            registry.register_adapter(ProviderID.ANTHROPIC, AnthropicAdapter(api_key=anthropic_key))
        if google_key:
            registry.register_adapter(ProviderID.GOOGLE, GeminiAdapter(api_key=google_key))
        if deepseek_key:
            registry.register_adapter(
                ProviderID.DEEPSEEK,
                OpenAICompatibleAdapter(
                    provider_id=ProviderID.DEEPSEEK,
                    api_key=deepseek_key,
                    base_url="https://api.deepseek.com/v1",
                ),
            )
        if openrouter_key:
            registry.register_adapter(
                ProviderID.OPENROUTER,
                OpenAICompatibleAdapter(
                    provider_id=ProviderID.OPENROUTER,
                    api_key=openrouter_key,
                    base_url="https://openrouter.ai/api/v1",
                    custom_headers={"HTTP-Referer": "https://model-router.local"},
                ),
            )

        # Ollama 通常不需要 API key（本地运行）
        registry.register_adapter(
            ProviderID.OLLAMA,
            OpenAICompatibleAdapter(
                provider_id=ProviderID.OLLAMA,
                api_key="ollama",
                base_url=ollama_url,
            ),
        )

        # 注册内置模型
        registry.register_models(ProviderFactory._default_models())

        return registry

    @staticmethod
    def _default_models() -> list[ModelSpec]:
        """默认模型注册表 — 覆盖主流提供商的旗舰模型"""
        return [
            # OpenAI
            ModelSpec(ProviderID.OPENAI, "gpt-5", "GPT-5", 256000, 128000,
                      supports_vision=True, supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=2.50, cost_per_1m_output=5.00,
                      capabilities={"coding", "reasoning", "vision", "multilingual"}),
            ModelSpec(ProviderID.OPENAI, "gpt-4o", "GPT-4o", 128000, 16384,
                      supports_vision=True, supports_tools=True,
                      cost_per_1m_input=2.50, cost_per_1m_output=10.00,
                      capabilities={"coding", "vision", "multilingual"}),
            ModelSpec(ProviderID.OPENAI, "gpt-4o-mini", "GPT-4o Mini", 128000, 16384,
                      supports_vision=True, supports_tools=True,
                      cost_per_1m_input=0.15, cost_per_1m_output=0.60,
                      capabilities={"coding", "multilingual"}),
            ModelSpec(ProviderID.OPENAI, "o4", "o4", 200000, 100000,
                      supports_vision=True, supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=5.00, cost_per_1m_output=20.00,
                      capabilities={"coding", "reasoning", "math", "science"}),
            # Anthropic
            ModelSpec(ProviderID.ANTHROPIC, "claude-opus-4-7", "Claude Opus 4.7", 1_000_000, 128000,
                      supports_vision=True, supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=15.00, cost_per_1m_output=75.00,
                      capabilities={"coding", "reasoning", "vision", "analysis", "architecture"}),
            ModelSpec(ProviderID.ANTHROPIC, "claude-sonnet-4-6", "Claude Sonnet 4.6", 1_000_000, 64000,
                      supports_vision=True, supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=3.00, cost_per_1m_output=15.00,
                      capabilities={"coding", "reasoning", "vision"}),
            ModelSpec(ProviderID.ANTHROPIC, "claude-haiku-4-5", "Claude Haiku 4.5", 200000, 32000,
                      supports_vision=True, supports_tools=True,
                      cost_per_1m_input=0.80, cost_per_1m_output=4.00,
                      capabilities={"coding", "fast"}),
            # Google
            ModelSpec(ProviderID.GOOGLE, "gemini-2.5-pro", "Gemini 2.5 Pro", 2_000_000, 65536,
                      supports_vision=True, supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=1.25, cost_per_1m_output=10.00,
                      capabilities={"coding", "reasoning", "vision", "multimodal"}),
            ModelSpec(ProviderID.GOOGLE, "gemini-2.5-flash", "Gemini 2.5 Flash", 1_000_000, 65536,
                      supports_vision=True, supports_tools=True,
                      cost_per_1m_input=0.15, cost_per_1m_output=0.60,
                      capabilities={"coding", "vision", "fast"}),
            # DeepSeek
            ModelSpec(ProviderID.DEEPSEEK, "deepseek-v4", "DeepSeek V4", 256000, 32000,
                      supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=0.27, cost_per_1m_output=0.55,
                      capabilities={"coding", "reasoning", "math"}),
            ModelSpec(ProviderID.DEEPSEEK, "deepseek-r1", "DeepSeek R1", 128000, 32000,
                      supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=0.55, cost_per_1m_output=2.19,
                      capabilities={"reasoning", "math", "science"}),
            # xAI
            ModelSpec(ProviderID.XAI, "grok-4", "Grok 4", 1_000_000, 64000,
                      supports_vision=True, supports_tools=True, supports_thinking=True,
                      cost_per_1m_input=5.00, cost_per_1m_output=20.00,
                      capabilities={"coding", "reasoning", "vision"}),
            # Meta (via OpenRouter/Ollama)
            ModelSpec(ProviderID.META, "llama-4", "Llama 4", 256000, 32000,
                      supports_vision=True, supports_tools=True,
                      cost_per_1m_input=0.50, cost_per_1m_output=1.00,
                      capabilities={"coding", "multilingual"}),
        ]


# ============================================================
# 使用示例
# ============================================================

async def _example():
    """演示多提供商使用"""
    registry = ProviderFactory.create_registry(
        openai_key="sk-...",
        anthropic_key="sk-ant-...",
        google_key="...",
        deepseek_key="sk-...",
    )

    # 构造统一请求
    request = UnifiedRequest(
        messages=[
            UnifiedMessage(role="user", content="用 Python 写一个快速排序"),
        ],
        model="claude-opus-4-7",  # 可以换成任何模型
        max_tokens=4096,
        thinking={"type": "adaptive"},
        effort="high",
    )

    # 自动解析提供商并发起请求
    provider = registry.resolve_provider(request.model)
    adapter = registry.get_adapter(provider)
    response = await adapter.chat(request)

    print(f"提供商: {response.provider}")
    print(f"模型: {response.model}")
    print(f"Token 用量: {response.usage}")
    print(f"输出: {response.content[:200]}...")
    print(f"耗时: {response.elapsed_ms:.0f}ms")


if __name__ == "__main__":
    asyncio.run(_example())
