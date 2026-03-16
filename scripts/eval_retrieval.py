from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json_file(path: str) -> object:
    file_path = Path(path)
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return json.loads(file_path.read_text(encoding=encoding))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Unable to decode JSON file: {path}")


def recall_at_k(returned_doc_ids: list[str], expected_doc_ids: list[str]) -> float:
    if not expected_doc_ids:
        return 1.0
    hits = len(set(returned_doc_ids) & set(expected_doc_ids))
    return hits / len(set(expected_doc_ids))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval outputs.")
    parser.add_argument("predictions", help="Path to retrieval prediction JSON file")
    parser.add_argument(
        "--cases",
        default="tests/evals/retrieval_cases.json",
        help="Path to retrieval eval cases",
    )
    args = parser.parse_args()

    predictions = load_json_file(args.predictions)
    cases = load_json_file(args.cases)
    by_id = {item["id"]: item.get("doc_ids", []) for item in predictions}

    results = []
    for case in cases:
        doc_ids = by_id.get(case["id"], [])
        score = recall_at_k(doc_ids, case.get("expected_doc_ids", []))
        results.append({"id": case["id"], "recall": score})

    avg = sum(item["recall"] for item in results) / max(1, len(results))
    print(json.dumps({"average_recall": avg, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
