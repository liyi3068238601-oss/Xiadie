"""Generate the deterministic, synthetic KIG.0 retrieval baseline corpus."""
from __future__ import annotations

import json
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
OUTPUT = BACKEND_DIR / "tests" / "fixtures" / "kig0_evaluation_v1.json"


def build_fixture() -> dict:
    documents: list[dict] = []
    memories: list[dict] = []
    cases: list[dict] = []
    for number in range(1, 21):
        suffix = f"{number:02d}"
        token = f"kigs{suffix}anchor"
        document_id = f"single-doc-{suffix}"
        documents.append({
            "id": document_id,
            "title": f"单文档基线 {suffix}",
            "text": f"# 单文档 {suffix}\n{token} 的合成结论是 single-value-{suffix}。",
        })
        cases.append({
            "id": f"single-{suffix}", "category": "single_document",
            "query": token, "expected_documents": [document_id],
            "expected_memory_marker": None,
        })

    for number in range(1, 21):
        suffix = f"{number:02d}"
        token = f"kigm{suffix}shared"
        document_ids = [f"multi-doc-{suffix}-a", f"multi-doc-{suffix}-b"]
        for side, document_id in zip(("a", "b"), document_ids):
            documents.append({
                "id": document_id,
                "title": f"多文档基线 {suffix}-{side}",
                "text": f"# 多文档 {suffix}-{side}\n{token} 的 {side} 侧合成事实是 multi-{suffix}-{side}。",
            })
        cases.append({
            "id": f"multi-{suffix}", "category": "multi_document",
            "query": token, "expected_documents": document_ids,
            "expected_memory_marker": None,
        })

    for number in range(1, 21):
        suffix = f"{number:02d}"
        token = f"kigx{suffix}shared"
        document_id = f"cross-doc-{suffix}"
        memory_marker = f"cross-memory-{suffix}"
        documents.append({
            "id": document_id,
            "title": f"跨域知识基线 {suffix}",
            "text": f"# 跨域知识 {suffix}\n{token} 的资料侧结论是 cross-knowledge-{suffix}。",
        })
        memories.append({
            "id": f"cross-memory-fragment-{suffix}",
            "content": f"{token} 的用户记忆侧标记是 {memory_marker}。",
            "marker": memory_marker,
        })
        cases.append({
            "id": f"cross-{suffix}", "category": "knowledge_memory",
            "query": token, "expected_documents": [document_id],
            "expected_memory_marker": memory_marker,
        })

    return {
        "protocol_version": "kig-construction-baseline-eval-v1",
        "synthetic_only": True,
        "contains_user_data": False,
        "documents": documents,
        "memories": memories,
        "cases": cases,
    }


def main() -> int:
    OUTPUT.write_text(
        json.dumps(build_fixture(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), "cases": 60, "documents": 80}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
