"""在隔离临时库中运行 K.3 合成评测；不会读取开发库或用户对话。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    backend_root = Path(__file__).resolve().parents[1]
    project_root = backend_root.parent
    parser.add_argument(
        "--fixture", type=Path,
        default=backend_root / "tests" / "fixtures" / "knowledge_recall_evaluation_v3.json",
    )
    parser.add_argument(
        "--uncalibrated", action="store_true",
        help="关闭 K.3 dense 下限，生成同一 fixture 的阈值前基线",
    )
    parser.add_argument(
        "--json-output", type=Path,
        default=project_root / "docs" / "reports" / "knowledge-recall-eval-v3-calibrated.json",
    )
    parser.add_argument(
        "--markdown-output", type=Path,
        default=project_root / "docs" / "reports" / "knowledge-recall-eval-v3-calibrated.md",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="xiadie-recall-eval-") as data_dir:
        os.environ["XIADIE_DATA_DIR"] = data_dir
        sys.path.insert(0, str(backend_root))
        from app import (  # noqa: PLC0415
            db, knowledge, knowledge_embeddings, knowledge_policy, knowledge_recall,
            knowledge_recall_evaluation, knowledge_search, knowledge_worker,
        )
        if args.uncalibrated:
            knowledge_recall.knowledge_recall_thresholds.SEMANTIC_CANDIDATE_MIN_SCORE = -1.0

        fixture = knowledge_recall_evaluation.load_fixture(args.fixture)
        db.init_db()
        document_map: dict[str, str] = {}
        for item in fixture["documents"]:
            imported = knowledge.import_file(
                item["title"] + ".md", "text/markdown", item["text"].encode("utf-8"),
            )
            document_id = imported["document"]["id"]
            document_map[item["id"]] = document_id
            asyncio.run(knowledge_worker.process_due(limit=3))
            knowledge_policy.update_document_policy(document_id, item["policy"])

        embedding_available = knowledge_embeddings.availability()["available"]
        if embedding_available:
            for document_id in document_map.values():
                run = knowledge_embeddings.enqueue(document_id)
                if run:
                    knowledge_embeddings.process_due(limit=1)

        reverse_documents = {value: key for key, value in document_map.items()}
        outcomes: list[dict] = []
        for case in fixture["cases"]:
            captured: dict = {}

            def search_once(query: str, **kwargs) -> dict:
                found = knowledge_search.hybrid_search(query, **kwargs)
                captured["found"] = found
                return found

            decision = knowledge_recall.evaluate(
                case["message"],
                {"id": "evaluation", "execution_location": case["provider_location"],
                 "location_revision": 1},
                search_fn=search_once,
            )
            found = captured.get("found", {"results": [], "diagnostics": {}})
            retrieved = {
                reverse_documents[item["document_id"]]
                for item in found.get("results", [])
                if item["document_id"] in reverse_documents
            }
            expected_groups = case["expected_document_groups"]
            retrieval_hit = bool(
                expected_groups and all(retrieved & set(group) for group in expected_groups)
            )
            expected_documents = {
                document for group in expected_groups for document in group
            }
            first_relevant_rank = next((
                rank for rank, item in enumerate(found.get("results", []), start=1)
                if reverse_documents.get(item["document_id"]) in expected_documents
            ), None)
            outcomes.append({
                "case_id": case["id"],
                "category": case["category"],
                "expected_action": case["expected_action"],
                "actual_action": decision["action"],
                "expected_reason": case["expected_reason"],
                "actual_reason": decision["reason_code"],
                "recall_mode": decision["recall_mode"],
                "expected_document_groups": expected_groups,
                "retrieval_hit": bool(retrieval_hit),
                "first_relevant_rank": first_relevant_rank,
                "retrieved_document_count": len(retrieved),
                "candidate_count": decision["candidate_count"],
                "natural_selected_count": decision["natural_selected_count"],
                "natural_tokens": decision["natural_tokens"],
                "retrieval_mode": decision["retrieval_mode"],
                "confidence_band": decision["confidence_band"],
                "latency_ms": decision["latency_ms"],
                "features": decision["features"],
                "candidate_features": [
                    {
                        "document": reverse_documents.get(item["document_id"], "unknown"),
                        "match_type": item.get("match_type"),
                        "fts_position": item.get("fts_position"),
                        "dense_position": item.get("dense_position"),
                        "vector_score": item.get("vector_score"),
                        "fusion_score": item.get("fusion_score"),
                        "duplicate_count": item.get("duplicate_count", 1),
                    }
                    for item in found.get("results", [])
                ],
            })

        report = knowledge_recall_evaluation.build_report(
            fixture=fixture,
            outcomes=outcomes,
            environment={
                "python": platform.python_version(),
                "platform": platform.system().lower(),
                "embedding_available": embedding_available,
                "embedding_version": knowledge_embeddings.EMBEDDING_VERSION if embedding_available else None,
                "search_index_version": knowledge_search.INDEX_VERSION,
                "search_protocol_version": knowledge_search.SEARCH_PROTOCOL_VERSION,
            },
        )
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        args.markdown_output.write_text(
            knowledge_recall_evaluation.render_markdown(report), encoding="utf-8",
        )
        print(json.dumps({
            "json": str(args.json_output), "markdown": str(args.markdown_output),
            "metrics": report["metrics"], "threshold": report["threshold_decision"],
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
