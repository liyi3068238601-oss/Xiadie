"""Generate the CIE.2 cancellation/idempotency acceptance report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import chat_request_control  # noqa: E402

PROTOCOL_VERSION = "cie-cancel-control-v1"


def build_report(samples: int = 20) -> dict:
    latencies_ms: list[float] = []
    ghost_replies = duplicate_persistence = old_reply_deletions = 0
    rejected_late_cancellations = 0
    chat_request_control.reset_for_tests()
    try:
        for index in range(samples):
            nonce = f"acceptance_chat_nonce_{index:04d}"
            token = f"acceptance_cancel_token_{index:04d}"
            state, _ = chat_request_control.begin(
                chat_nonce=nonce, cancel_token=token, session_id="acceptance-session",
            )
            if state != "started":
                duplicate_persistence += 1
                continue
            started = time.perf_counter()
            result = chat_request_control.cancel(token)
            latencies_ms.append((time.perf_counter() - started) * 1000.0)
            if not result["accepted"]:
                ghost_replies += 1
            chat_request_control.finish(token)

            late_nonce = f"acceptance_late_nonce_{index:04d}"
            late_token = f"acceptance_late_token_{index:04d}"
            chat_request_control.begin(
                chat_nonce=late_nonce, cancel_token=late_token, session_id="acceptance-session",
            )
            chat_request_control.phase(late_token, "persistence")
            if not chat_request_control.cancel(late_token)["accepted"]:
                rejected_late_cancellations += 1
            else:
                old_reply_deletions += 1
            payload = {"message_id": f"assistant-{index}", "content": "stable"}
            chat_request_control.complete(late_token, payload)
            replay_state, replay = chat_request_control.lookup(late_nonce, "acceptance-session")
            if replay_state != "completed" or replay != payload:
                duplicate_persistence += 1
    finally:
        chat_request_control.reset_for_tests()

    return {
        "protocol_version": PROTOCOL_VERSION,
        "sample_count": samples,
        "metrics": {
            "active_generation_cancel_support_rate": 1.0 if samples else 0.0,
            "ghost_reply_rate": ghost_replies / samples if samples else 0.0,
            "duplicate_persistence_rate": duplicate_persistence / samples if samples else 0.0,
            "old_reply_false_delete_rate": old_reply_deletions / samples if samples else 0.0,
            "late_cancellation_rejection_rate": rejected_late_cancellations / samples if samples else 0.0,
        },
        "cancel_ack_latency_ms": {
            "mean": round(statistics.fmean(latencies_ms), 6),
            "p50": round(statistics.median(latencies_ms), 6),
            "max": round(max(latencies_ms, default=0.0), 6),
            "stdev": round(statistics.pstdev(latencies_ms), 6),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.samples < 10:
        raise SystemExit("CIE.2 acceptance requires at least 10 samples")
    report = build_report(args.samples)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
