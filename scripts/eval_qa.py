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


def score_case(answer: str, expected_keywords: list[str]) -> float:
    if not expected_keywords:
        return 1.0
    hits = sum(1 for keyword in expected_keywords if keyword in answer)
    return hits / len(expected_keywords)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate QA outputs against keyword-based cases.")
    parser.add_argument("predictions", help="Path to prediction JSON file")
    parser.add_argument(
        "--cases",
        default="tests/evals/qa_cases.json",
        help="Path to QA eval cases",
    )
    args = parser.parse_args()

    predictions = load_json_file(args.predictions)
    cases = load_json_file(args.cases)
    by_id = {item["id"]: item["answer"] for item in predictions}

    results = []
    for case in cases:
        answer = by_id.get(case["id"], "")
        score = score_case(answer, case.get("expected_keywords", []))
        results.append({"id": case["id"], "score": score})

    avg = sum(item["score"] for item in results) / max(1, len(results))
    print(json.dumps({"average_score": avg, "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
