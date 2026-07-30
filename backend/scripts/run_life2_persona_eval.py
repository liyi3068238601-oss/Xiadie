"""Run the LIFE2 Persona fixed set against the configured remote model.

This explicit developer command writes response bodies only to the requested
evaluation artifact. It never writes them to product diagnostics or SQLite.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import db, life2_evaluation, persona, persona_output_guard, persona_v2  # noqa: E402


def _configured_model() -> tuple[dict, str, str]:
    cfg = json.loads(db.get_setting("current_model", "{}") or "{}")
    provider_id = str(cfg.get("provider_id") or "")
    model = str(cfg.get("model") or "")
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    finally:
        conn.close()
    if not row or not model:
        raise RuntimeError("configured_model_unavailable")
    provider = dict(row)
    if provider_id == "mock" or not provider.get("base_url"):
        raise RuntimeError("remote_model_required")
    fingerprint_payload = {
        "provider_id": provider_id,
        "base_url": str(provider.get("base_url") or "").rstrip("/"),
        "model": model,
        "execution_location": str(provider.get("execution_location") or "unknown"),
    }
    encoded = json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"))
    return provider, model, hashlib.sha256(encoded.encode()).hexdigest()


async def _complete(
    client: httpx.AsyncClient, provider: dict, model: str, system_prompt: str,
    case: life2_evaluation.PersonaCase, temperature: float,
) -> dict[str, object]:
    started = time.perf_counter()
    response = await client.post(
        str(provider["base_url"]).rstrip("/") + "/chat/completions",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.get('api_key', '')}",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": case.user_text},
            ],
            "stream": False,
            "temperature": temperature,
            "max_tokens": 500,
        },
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    payload = response.json()
    raw_output = str(payload["choices"][0]["message"]["content"] or "")
    allow_narration = persona_output_guard.explicit_narration_requested(case.user_text)
    output = persona_output_guard.sanitize_natural_dialogue(
        raw_output, allow_narration=allow_narration,
        suppress_ungrounded_ambience=case.category == "casual_grounding",
    )
    usage = payload.get("usage") if isinstance(payload, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    return {
        "case": case.public(),
        "output": output,
        "raw_output": raw_output,
        "output_guard_applied": (
            output != raw_output
        ),
        "score": life2_evaluation.score_output(case, output),
        "latency_ms": latency_ms,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
    }


async def _run(args: argparse.Namespace) -> dict[str, object]:
    db.init_db()
    provider, model, fingerprint = _configured_model()
    cases = life2_evaluation.build_cases()
    semaphore = asyncio.Semaphore(max(1, min(args.concurrency, 12)))
    timeout = httpx.Timeout(args.timeout)
    prompts = {"legacy": persona.PERSONA_PROMPT}
    if args.profile == "candidate":
        prompts = {
            mode: persona_v2.compile_candidate(mode=mode)[0]
            for mode in persona_v2.MODES
        }

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def one(case: life2_evaluation.PersonaCase) -> dict[str, object]:
            async with semaphore:
                last_error: Exception | None = None
                for attempt in range(1, 4):
                    try:
                        prompt = prompts[case.mode] if args.profile == "candidate" else prompts["legacy"]
                        return await _complete(client, provider, model, prompt, case, args.temperature)
                    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                        last_error = exc
                        if attempt < 3:
                            await asyncio.sleep(float(attempt))
                return {
                    "case": case.public(), "output": "",
                    "score": life2_evaluation.score_output(case, ""),
                    "latency_ms": 0, "prompt_tokens": None, "completion_tokens": None,
                    "error": type(last_error).__name__ if last_error else "unknown",
                }

        runs: list[dict[str, object]] = []
        for run_number in range(1, args.runs + 1):
            started = time.time()
            rows = await asyncio.gather(*(one(case) for case in cases))
            scores = [row["score"] for row in rows]
            runs.append({
                "run": run_number,
                "started_at": started,
                "finished_at": time.time(),
                "summary": life2_evaluation.summarize(scores),
                "latency_ms_total": sum(int(row["latency_ms"]) for row in rows),
                "prompt_tokens_total": sum(int(row["prompt_tokens"] or 0) for row in rows),
                "completion_tokens_total": sum(int(row["completion_tokens"] or 0) for row in rows),
                "results": rows,
            })
    return {
        "artifact_version": "life2-persona-eval-artifact-v1",
        "evaluation_protocol": life2_evaluation.PROTOCOL_VERSION,
        "label": args.label,
        "input_profile": args.profile,
        "prompt_sha256": {
            key: hashlib.sha256(value.encode()).hexdigest() for key, value in prompts.items()
        },
        "sampling_profile": {"temperature": args.temperature, "max_tokens": 500},
        "provider_id": provider["id"],
        "model": model,
        "model_fingerprint": fingerprint,
        "fixture_sha256": life2_evaluation.fixture_sha256(cases),
        "case_count": len(cases),
        "runs": runs,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="legacy-persona")
    parser.add_argument("--profile", choices=("legacy", "candidate"), default="legacy")
    parser.add_argument("--runs", type=int, default=life2_evaluation.RUNS_REQUIRED)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    artifact = asyncio.run(_run(args))
    output_path = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "path": str(output_path),
        "model": artifact["model"],
        "fingerprint": artifact["model_fingerprint"],
        "summaries": [run["summary"] for run in artifact["runs"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
