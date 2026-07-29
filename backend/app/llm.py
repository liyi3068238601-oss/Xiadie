"""模型调用层：统一多个 OpenAI-Compatible 供应商 + 内置 mock。

需求 MODEL-001..006：供应商配置、模型切换、流式、连接测试、降级策略。
所有列出的供应商（DeepSeek/OpenAI/GLM/Qwen/Kimi/OpenRouter/SiliconFlow/Ollama）
都兼容 OpenAI /chat/completions 接口，因此共用一个客户端。
"""
import asyncio
import json
import time
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

import httpx

JSON_COMPLETION_MAX_TOKENS = 500
# The default remains intentionally small for background observers.  A caller
# performing an explicit reasoning-model certification may request more, but
# can never exceed this process-wide hard ceiling.
JSON_COMPLETION_HARD_MAX_TOKENS = 2_048
# JSON-mode reasoning models may spend much of the output budget on hidden
# reasoning before emitting the bounded object. This larger ceiling is opt-in;
# ordinary observers retain the 2,048 hard limit above.
JSON_REASONING_HARD_MAX_TOKENS = 4_096
JSON_COMPLETION_TIMEOUT_SECONDS = 20
JSON_COMPLETION_MAX_CHARS = 12000


class LLMError(Exception):
    """携带用户友好提示的模型错误（需求 CHAT-005 错误恢复）。"""

    def __init__(self, message: str, hint: str = "", code: str | None = None):
        super().__init__(message)
        self.hint = hint or message
        self.code = code


MOCK_REPLY = (
    "（演示模式）你好，我是遐蝶。现在使用的是内置演示模型，"
    "还没有配置真实的模型供应商。你可以在「设置 → 模型」里填入 DeepSeek、"
    "OpenAI 等任意兼容 OpenAI 接口的 API Key，我就能给出真正的回复了。\n\n"
    "在演示模式下，我依然可以帮你体验界面：新建对话、创建任务、查看记忆、"
    "感受桌宠气泡联动都可以正常使用。"
)


async def _stream_mock(messages: list[dict]) -> AsyncIterator[str]:
    last = messages[-1]["content"] if messages else ""
    text = MOCK_REPLY
    if last.strip():
        text = f"我收到了你的消息：「{last.strip()[:40]}」。\n\n" + MOCK_REPLY
    for ch in text:
        await asyncio.sleep(0.008)
        yield ch


async def _stream_openai_compatible(
    base_url: str, api_key: str, model: str, messages: list[dict], *, max_tokens: int,
    temperature: float | None = None,
) -> AsyncIterator[str]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "max_tokens": max(1, int(max_tokens)),
    }
    if temperature is not None:
        payload["temperature"] = float(temperature)
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code == 401:
                    raise LLMError("鉴权失败", "API Key 无效或已过期，请到设置中检查。")
                if resp.status_code == 429:
                    raise LLMError("被限流", "请求过于频繁或额度不足，请稍后重试。")
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "ignore")[:200]
                    raise LLMError(f"模型返回错误 {resp.status_code}", body or "请检查模型名与 Base URL。")
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {}).get("content")
                        if delta:
                            yield delta
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
    except httpx.ConnectError:
        raise LLMError("无法连接到模型服务", f"连不上 {base_url}，请检查网络或本地服务是否启动。")
    except httpx.TimeoutException:
        raise LLMError("请求超时", "模型响应超时，请稍后重试。")
    except httpx.HTTPError:
        # 流式读取中途断连（RemoteProtocolError/ReadError 等）也包装成 LLMError，
        # 保证以 SSE error 事件下发而非静默截断。放在具体异常之后作为兜底父类。
        raise LLMError("模型连接中断", "与模型服务的连接意外中断，请稍后重试。")


async def stream_chat(
    provider: Optional[dict], model: str, messages: list[dict], *, max_tokens: int = 1_024,
    temperature: float | None = None,
) -> AsyncIterator[str]:
    """按供应商分发。provider 为 None 或 mock 时走演示模型。"""
    if provider is None or provider["id"] == "mock" or not provider.get("base_url"):
        async for ch in _stream_mock(messages):
            yield ch
        return
    stream_options = {"max_tokens": max_tokens}
    if temperature is not None:
        stream_options["temperature"] = temperature
    async for ch in _stream_openai_compatible(
        provider["base_url"], provider.get("api_key", ""), model, messages,
        **stream_options,
    ):
        yield ch


async def complete_json(
    provider: Optional[dict],
    model: str,
    messages: list[dict],
    *,
    max_tokens: int = JSON_COMPLETION_MAX_TOKENS,
    timeout_seconds: float = JSON_COMPLETION_TIMEOUT_SECONDS,
    temperature: float = 0.0,
    top_p: float | None = None,
    json_mode: bool = False,
) -> dict:
    """执行受限的非流式 JSON 观察调用；不负责解析或信任模型输出。"""
    if provider is None or provider.get("id") == "mock" or not provider.get("base_url"):
        raise LLMError("观察模型不可用", "演示模型不执行旁观观察。")
    token_ceiling = JSON_REASONING_HARD_MAX_TOKENS if json_mode else JSON_COMPLETION_HARD_MAX_TOKENS
    safe_max_tokens = max(1, min(int(max_tokens), token_ceiling))
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "temperature": float(temperature),
        "max_tokens": safe_max_tokens,
    }
    if top_p is not None:
        payload["top_p"] = float(top_p)
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=max(0.1, float(timeout_seconds))) as client:
            response = await client.post(url, headers=headers, json=payload)
        if response.status_code == 401:
            raise LLMError(
                "观察模型鉴权失败", "API Key 无效或已过期。", "observer_model_auth_failed",
            )
        if response.status_code == 429:
            raise LLMError(
                "观察模型被限流", "稍后进入恢复队列重试。", "observer_model_rate_limited",
            )
        if response.status_code >= 500:
            raise LLMError(
                f"观察模型返回错误 {response.status_code}", "稍后重试。",
                "observer_model_server_error",
            )
        if response.status_code >= 400:
            raise LLMError(
                f"观察模型返回错误 {response.status_code}", "请检查模型兼容性。",
                "observer_model_http_error",
            )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                "观察模型响应格式错误", "响应缺少 JSON 文本。",
                "observer_model_response_invalid",
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError(
                "观察模型响应为空", "稍后重试。", "observer_model_response_empty",
            )
        if len(content) > JSON_COMPLETION_MAX_CHARS:
            raise LLMError(
                "观察模型响应过长", "响应超过本地安全上限。",
                "observer_model_response_too_long",
            )
        usage = body.get("usage") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        return {
            "text": content,
            "prompt_tokens": _safe_token_count(usage.get("prompt_tokens")),
            "completion_tokens": _safe_token_count(usage.get("completion_tokens")),
            "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        }
    except httpx.ConnectError as exc:
        raise LLMError(
            "无法连接观察模型", "稍后进入恢复队列重试。", "observer_model_connect_error",
        ) from exc
    except httpx.TimeoutException as exc:
        raise LLMError(
            "观察模型请求超时", "稍后进入恢复队列重试。", "observer_model_timeout"
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMError(
            "观察模型连接中断", "稍后进入恢复队列重试。", "observer_model_transport_error",
        ) from exc


def _safe_token_count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(int(value), 10_000_000))


async def test_connection(base_url: str, api_key: str, model: str) -> dict:
    """连接测试（需求 MODEL-004）。"""
    if not base_url:
        return {"ok": False, "message": "未填写 Base URL"}
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 200:
            return {"ok": True, "message": "连接成功"}
        if resp.status_code == 401:
            return {"ok": False, "message": "鉴权失败：API Key 无效"}
        if resp.status_code == 404:
            return {"ok": False, "message": "接口或模型不存在：请检查 Base URL 和模型名"}
        return {"ok": False, "message": f"返回 {resp.status_code}：{resp.text[:120]}"}
    except httpx.ConnectError:
        return {"ok": False, "message": f"无法连接到 {base_url}"}
    except httpx.TimeoutException:
        return {"ok": False, "message": "连接超时"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"测试失败：{e}"}


async def discover_models(base_url: str, api_key: str) -> dict:
    """Read an OpenAI-compatible /models endpoint without persisting credentials."""
    base_url = base_url.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "models": [], "message": "Base URL 必须是有效的 http/https 地址"}

    url = base_url + "/models"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code in (401, 403):
            return {"ok": False, "models": [], "message": "鉴权失败：请检查 API Key"}
        if resp.status_code == 404:
            return {"ok": False, "models": [], "message": "没有找到 /models 接口，请检查 Base URL"}
        if resp.status_code >= 400:
            return {"ok": False, "models": [], "message": f"模型列表接口返回 HTTP {resp.status_code}"}
        try:
            payload = resp.json()
        except ValueError:
            return {"ok": False, "models": [], "message": "模型列表接口没有返回有效 JSON"}

        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list) and isinstance(payload, dict):
            raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return {"ok": False, "models": [], "message": "无法识别模型列表返回格式"}

        model_ids: list[str] = []
        for item in raw_models:
            if isinstance(item, str):
                model_id = item
            elif isinstance(item, dict):
                model_id = item.get("id") or item.get("name") or item.get("model")
            else:
                continue
            if isinstance(model_id, str) and 0 < len(model_id.strip()) <= 200:
                model_ids.append(model_id.strip())

        models = sorted(set(model_ids), key=str.casefold)[:500]
        if not models:
            return {"ok": False, "models": [], "message": "接口可访问，但没有发现可用模型"}
        return {"ok": True, "models": models, "message": f"发现 {len(models)} 个可用模型"}
    except httpx.ConnectError:
        return {"ok": False, "models": [], "message": f"无法连接到 {base_url}"}
    except httpx.TimeoutException:
        return {"ok": False, "models": [], "message": "获取模型列表超时"}
    except httpx.HTTPError:
        return {"ok": False, "models": [], "message": "获取模型列表时连接中断"}
