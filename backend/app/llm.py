"""模型调用层：统一多个 OpenAI-Compatible 供应商 + 内置 mock。

需求 MODEL-001..006：供应商配置、模型切换、流式、连接测试、降级策略。
所有列出的供应商（DeepSeek/OpenAI/GLM/Qwen/Kimi/OpenRouter/SiliconFlow/Ollama）
都兼容 OpenAI /chat/completions 接口，因此共用一个客户端。
"""
import asyncio
import json
from typing import AsyncIterator, Optional

import httpx


class LLMError(Exception):
    """携带用户友好提示的模型错误（需求 CHAT-005 错误恢复）。"""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint or message


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
    base_url: str, api_key: str, model: str, messages: list[dict]
) -> AsyncIterator[str]:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "messages": messages, "stream": True}
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
    provider: Optional[dict], model: str, messages: list[dict]
) -> AsyncIterator[str]:
    """按供应商分发。provider 为 None 或 mock 时走演示模型。"""
    if provider is None or provider["id"] == "mock" or not provider.get("base_url"):
        async for ch in _stream_mock(messages):
            yield ch
        return
    async for ch in _stream_openai_compatible(
        provider["base_url"], provider.get("api_key", ""), model, messages
    ):
        yield ch


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
