"""Recalculate deterministic LIFE2 scores without making model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import life2_evaluation  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source = args.source if args.source.is_absolute() else REPO_ROOT / args.source
    target = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    artifact = json.loads(source.read_text(encoding="utf-8"))
    for run in artifact["runs"]:
        for row in run["results"]:
            row["score"] = life2_evaluation.score_output(
                life2_evaluation.PersonaCase(**row["case"]), row["output"],
            )
        run["summary"] = life2_evaluation.summarize(
            row["score"] for row in run["results"]
        )
    artifact["evaluation_protocol"] = life2_evaluation.PROTOCOL_VERSION
    artifact["rescored_from"] = source.name
    target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "path": str(target),
        "protocol": artifact["evaluation_protocol"],
        "summaries": [run["summary"] for run in artifact["runs"]],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
