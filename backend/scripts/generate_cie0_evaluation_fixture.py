"""Generate the deterministic, synthetic CIE.0 interaction fixture."""
from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "cie0_interaction_v1.json"
CONTINUOUS_ROUNDS = (5, 20, 100, 500)


def build_fixture() -> dict:
    continuous = []
    for rounds in CONTINUOUS_ROUNDS:
        continuous.append({
            "id": f"continuous-{rounds}",
            "rounds": rounds,
            "session_id": f"synthetic-session-{rounds}",
            "messages": [
                {
                    "message_id": f"c{rounds}-m{index:03d}",
                    "sequence": index,
                    "content": f"合成连续消息 {rounds}/{index}",
                }
                for index in range(1, rounds + 1)
            ],
        })

    interruption = [
        {
            "id": f"interrupt-{index:02d}",
            "active_request_id": f"request-{index:02d}",
            "new_message_id": f"interrupt-message-{index:02d}",
            "phase": "provider_stream",
            "expected_current_support": False,
        }
        for index in range(1, 21)
    ]
    attachments = [
        {
            "id": f"attachment-text-{index:02d}",
            "kind": "text",
            "mime_type": "text/plain",
            "remote_authorized": False,
            "expected_current_support": True,
        }
        for index in range(1, 11)
    ] + [
        {
            "id": f"attachment-image-{index:02d}",
            "kind": "image",
            "mime_type": "image/png",
            "remote_authorized": index % 2 == 0,
            "expected_current_support": False,
        }
        for index in range(1, 11)
    ]
    rhythm_samples = [
        "普通句子。第二句保持原文。",
        "代码块：```python\nprint('synthetic')\n```",
        "链接 https://example.invalid/a?b=1 不得拆坏。",
        "版本 v1.2.3 与数字 12.50 不得拆坏。",
        "> 合成引用\n\n引用后的正文。",
    ]
    rhythm = [
        {
            "id": f"rhythm-{index:02d}",
            "content": rhythm_samples[(index - 1) % len(rhythm_samples)],
            "expected_current_mode": "verbatim_sse",
        }
        for index in range(1, 21)
    ]
    contributions = [
        {
            "id": f"contribution-{index:02d}",
            "source": f"synthetic-plugin-{index:02d}",
            "revision": f"r{index}",
            "ttl_seconds": 60,
            "privacy": "private",
            "candidate_payload": f"UNTRUSTED_SYNTHETIC_MARKER_{index:02d}",
            "expected_current_support": False,
        }
        for index in range(1, 21)
    ]
    return {
        "protocol_version": "cie-construction-baseline-eval-v1",
        "synthetic_only": True,
        "contains_user_data": False,
        "continuous": continuous,
        "interruption": interruption,
        "attachments": attachments,
        "rhythm": rhythm,
        "contributions": contributions,
    }


def main() -> int:
    fixture = build_fixture()
    OUTPUT.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(OUTPUT),
        "continuous_messages": sum(item["rounds"] for item in fixture["continuous"]),
        "other_cases": sum(len(fixture[key]) for key in ("interruption", "attachments", "rhythm", "contributions")),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
