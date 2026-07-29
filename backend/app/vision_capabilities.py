"""CIE.3 evidence-backed vision capability probes and message shaping."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import httpx

from . import db

PROBE_PROTOCOL_VERSION = "vision-probe-v1"
_RED_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


def status(provider: dict | None, model: str) -> dict:
    if not provider:
        return _public(None, model, "unknown", "provider_unavailable", None)
    location_revision = max(1, int(provider.get("location_revision") or 1))
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT * FROM model_capability_evidence"
            " WHERE provider_id=? AND model=? AND capability='vision'"
            " AND provider_location_revision=?",
            (provider["id"], model, location_revision),
        ).fetchone()
        return _public(provider, model, row["status"] if row else "unknown",
                       row["error_code"] if row else None, row["checked_at"] if row else None)
    finally:
        conn.close()


async def probe(provider: dict | None, model: str) -> dict:
    if not provider or provider.get("id") == "mock" or not provider.get("base_url"):
        return _persist(provider, model, "unsupported", "provider_has_no_vision_endpoint", "")
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.get("api_key"):
        headers["Authorization"] = f"Bearer {provider['api_key']}"
    payload = {
        "model": model,
        "stream": False,
        "max_tokens": 8,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Return only the uppercase dominant color visible in the image."},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(_RED_PNG).decode("ascii"),
                }},
            ],
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, headers=headers, json=payload)
        response_digest = hashlib.sha256(response.content).hexdigest()
        if response.status_code >= 400:
            status_value = "unsupported" if response.status_code in {400, 404, 415, 422} else "unknown"
            return _persist(
                provider, model, status_value, f"vision_probe_http_{response.status_code}",
                response_digest,
            )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            return _persist(provider, model, "unknown", "vision_probe_response_invalid", response_digest)
        supported = isinstance(content, str) and content.strip().upper() == "RED"
        return _persist(
            provider, model, "supported" if supported else "unsupported",
            None if supported else "vision_probe_answer_mismatch", response_digest,
        )
    except httpx.TimeoutException:
        return _persist(provider, model, "unknown", "vision_probe_timeout", "")
    except httpx.HTTPError:
        return _persist(provider, model, "unknown", "vision_probe_transport_error", "")


def apply_images(messages: list[dict], data_urls: list[str]) -> list[dict]:
    if not data_urls:
        return messages
    shaped = [dict(item) for item in messages]
    for index in range(len(shaped) - 1, -1, -1):
        if shaped[index].get("role") != "user":
            continue
        text = shaped[index].get("content")
        parts: list[dict[str, Any]] = [{"type": "text", "text": str(text or "请描述这些图片。")}]
        parts.extend({"type": "image_url", "image_url": {"url": url}} for url in data_urls)
        shaped[index]["content"] = parts
        return shaped
    raise ValueError("vision images require a user message")


def _persist(provider: dict | None, model: str, status_value: str,
             error_code: str | None, response_digest: str) -> dict:
    provider_id = provider.get("id") if provider else "mock"
    location = provider.get("execution_location", "local") if provider else "local"
    location_revision = max(1, int((provider or {}).get("location_revision") or 1))
    evidence = hashlib.sha256(json.dumps({
        "protocol": PROBE_PROTOCOL_VERSION,
        "provider_id": provider_id,
        "model": model,
        "status": status_value,
        "error_code": error_code,
        "response_digest": response_digest,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    checked_at = db.now()
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO model_capability_evidence("
            "provider_id,model,capability,status,provider_location,provider_location_revision,"
            "probe_protocol_version,evidence_sha256,error_code,checked_at)"
            " VALUES(?,?,'vision',?,?,?,?,?,?,?)"
            " ON CONFLICT(provider_id,model,capability,provider_location_revision) DO UPDATE SET"
            " status=excluded.status,provider_location=excluded.provider_location,"
            " probe_protocol_version=excluded.probe_protocol_version,"
            " evidence_sha256=excluded.evidence_sha256,error_code=excluded.error_code,"
            " checked_at=excluded.checked_at",
            (provider_id, model, status_value, location, location_revision,
             PROBE_PROTOCOL_VERSION, evidence, error_code, checked_at),
        )
        conn.commit()
    finally:
        conn.close()
    return _public(provider, model, status_value, error_code, checked_at) | {
        "evidence_sha256": evidence,
    }


def _public(provider: dict | None, model: str, status_value: str,
            error_code: str | None, checked_at: float | None) -> dict:
    return {
        "protocol_version": PROBE_PROTOCOL_VERSION,
        "provider_id": provider.get("id") if provider else "mock",
        "model": model,
        "status": status_value,
        "provider_location": provider.get("execution_location", "local") if provider else "local",
        "provider_location_revision": max(1, int((provider or {}).get("location_revision") or 1)),
        "checked_at": checked_at,
        "error_code": error_code,
    }
