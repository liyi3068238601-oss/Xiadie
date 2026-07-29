"""Build the reviewed LIFE2 WorldBook r1 package from its canonical draft."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "LIFE_V2_WORLDBOOK_CONTENT_DRAFT.md"
TARGET = ROOT / "backend" / "app" / "knowledge" / "xiadie_worldbook_r1.json"
PROTOCOL_VERSION = "worldbook-r1"
SOURCE_GATE_VERSION = "worldbook-source-gate-v1"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _items(value: str) -> list[str]:
    if value.strip() in {"", "无", "none", "None"}:
        return []
    return [item.strip() for item in re.split(r"[,，、]", value) if item.strip()]


def parse_entries(text: str) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for block in re.findall(r"```text\s*\n(##\s+[a-z0-9_]+\n.*?)(?=\n```)", text, re.S):
        lines = block.strip().splitlines()
        entry_id = lines[0].removeprefix("## ").strip()
        metadata: dict[str, str] = {}
        body_start = None
        for index, line in enumerate(lines[1:], start=1):
            if not line.startswith("- "):
                body_start = index
                break
            key, separator, value = line[2:].partition(":")
            if not separator:
                raise ValueError(f"invalid metadata line for {entry_id}: {line}")
            metadata[key.strip()] = value.strip()
        if body_start is None:
            raise ValueError(f"missing body for {entry_id}")
        body = "\n".join(lines[body_start:]).strip()
        body_hash = _sha256(body)
        entry = {
            "entry_id": entry_id,
            "category": metadata["category"],
            "triggers": _items(metadata["triggers"]),
            "aliases": _items(metadata["aliases"]),
            "priority": int(metadata["priority"]),
            "always_on": metadata["always_on"].casefold() == "true",
            "related_entry_ids": _items(metadata["related_entry_ids"]),
            "source_status": metadata["source_status"],
            "source_refs": _items(metadata["source_refs"]),
            "revision": f"r1-{body_hash[:16]}",
            "body_sha256": body_hash,
            "body": body,
        }
        entries.append(entry)
    return entries


def build() -> dict[str, object]:
    source_text = SOURCE.read_text(encoding="utf-8")
    entries = parse_entries(source_text)
    if len(entries) != 30:
        raise ValueError(f"expected 30 entries, got {len(entries)}")
    ids = [str(item["entry_id"]) for item in entries]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate entry_id")
    known = set(ids)
    for item in entries:
        missing = set(item["related_entry_ids"]) - known
        if missing:
            raise ValueError(f"unknown related ids for {item['entry_id']}: {sorted(missing)}")
        if item["always_on"]:
            raise ValueError(f"r1 always_on must remain empty: {item['entry_id']}")
    canonical_entries = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload = {
        "protocol_version": PROTOCOL_VERSION,
        "source_gate_version": SOURCE_GATE_VERSION,
        "generated_from": "docs/LIFE_V2_WORLDBOOK_CONTENT_DRAFT.md",
        "source_sha256": _sha256(source_text),
        "entry_count": len(entries),
        "entries_sha256": _sha256(canonical_entries),
        "entries": entries,
    }
    return payload


if __name__ == "__main__":
    payload = build()
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {TARGET} ({payload['entry_count']} entries, {payload['entries_sha256']})")
