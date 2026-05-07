import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


DEFAULT_CASES_FILE = ROOT_DIR / "data" / "eval_cases.json"
LABELS = ["tasks", "blockers", "plans"]


def normalize_text(value: str) -> str:
    text = str(value).lower()
    text = re.sub(r"[\s，,。；;：:\-_/\\'\"“”‘’（）()\[\]【】]+", "", text)
    for token in ["今天", "当前", "目前", "已经", "已", "了", "的", "主要", "继续", "准备"]:
        text = text.replace(token, "")
    return text


def similarity_score(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return 1.0
    left_chars = set(left_norm)
    right_chars = set(right_norm)
    return len(left_chars & right_chars) / len(left_chars | right_chars)


def item_hit(expected_item: str, actual_items: list[str]) -> bool:
    return any(similarity_score(expected_item, actual) >= 0.72 for actual in actual_items)


def score_list(expected: list[str], actual: list[str]) -> dict[str, Any]:
    hits = [item for item in expected if item_hit(item, actual)]
    precision = len(hits) / len(actual) if actual else (1.0 if not expected else 0.0)
    recall = len(hits) / len(expected) if expected else 1.0
    return {
        "expected_count": len(expected),
        "actual_count": len(actual),
        "hit_count": len(hits),
        "precision": precision,
        "recall": recall,
        "missed": [item for item in expected if item not in hits],
    }


def extract_titles(structured: dict[str, Any]) -> dict[str, list[str]]:
    return {
        "tasks": [str(item.get("title", "")).strip() for item in structured.get("tasks", []) if item.get("title")],
        "blockers": [str(item).strip() for item in structured.get("blockers", []) if str(item).strip()],
        "plans": [str(item).strip() for item in structured.get("plans", []) if str(item).strip()],
    }


async def evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    from app import to_structured_log

    structured, source = await to_structured_log(case["input"])
    actual = extract_titles(structured)
    expected = case["expected"]
    scores = {
        key: score_list(expected.get(key, []), actual.get(key, []))
        for key in LABELS
    }
    recall_values = [scores[key]["recall"] for key in scores]
    precision_values = [scores[key]["precision"] for key in scores]
    return {
        "id": case.get("id", "unknown"),
        "source": source,
        "input": case["input"],
        "actual": actual,
        "expected": expected,
        "scores": scores,
        "avg_recall": sum(recall_values) / len(recall_values),
        "avg_precision": sum(precision_values) / len(precision_values),
    }


def aggregate_label_metrics(results: list[dict[str, Any]]) -> dict[str, dict[str, float | int]]:
    metrics: dict[str, dict[str, float | int]] = {}
    for label in LABELS:
        expected_count = sum(result["scores"][label]["expected_count"] for result in results)
        actual_count = sum(result["scores"][label]["actual_count"] for result in results)
        hit_count = sum(result["scores"][label]["hit_count"] for result in results)
        metrics[label] = {
            "expected_count": expected_count,
            "actual_count": actual_count,
            "hit_count": hit_count,
            "recall": hit_count / expected_count if expected_count else 1.0,
            "precision": hit_count / actual_count if actual_count else (1.0 if expected_count == 0 else 0.0),
        }
    return metrics


def evaluate_quality_gate(report: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    failures = []
    if args.min_recall is not None and report["avg_recall"] < args.min_recall:
        failures.append(f"avg_recall {report['avg_recall']:.2%} < {args.min_recall:.2%}")
    if args.min_precision is not None and report["avg_precision"] < args.min_precision:
        failures.append(f"avg_precision {report['avg_precision']:.2%} < {args.min_precision:.2%}")
    if args.max_local_rate is not None and report["local_fallback_rate"] > args.max_local_rate:
        failures.append(
            f"local_fallback_rate {report['local_fallback_rate']:.2%} > {args.max_local_rate:.2%}"
        )
    return {
        "enabled": any(value is not None for value in [args.min_recall, args.min_precision, args.max_local_rate]),
        "passed": not failures,
        "failures": failures,
    }


async def run_eval(cases_file: Path) -> dict[str, Any]:
    cases = json.loads(cases_file.read_text(encoding="utf-8"))
    results = []
    for case in cases:
        results.append(await evaluate_case(case))

    total = len(results)
    source_counts: dict[str, int] = {}
    for result in results:
        source_counts[result["source"]] = source_counts.get(result["source"], 0) + 1

    structured_count = sum(count for source, count in source_counts.items() if source != "local")
    local_count = source_counts.get("local", 0)
    label_metrics = aggregate_label_metrics(results)

    return {
        "total_cases": total,
        "source_counts": source_counts,
        "structured_rate": structured_count / total if total else 0,
        "local_fallback_rate": local_count / total if total else 0,
        "avg_recall": sum(item["avg_recall"] for item in results) / total if total else 0,
        "avg_precision": sum(item["avg_precision"] for item in results) / total if total else 0,
        "label_metrics": label_metrics,
        "results": results,
    }


def write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def print_report(report: dict[str, Any], show_details: bool) -> None:
    print("=== Weekly Agent Structuring Eval ===")
    print(f"Total cases: {report['total_cases']}")
    print(f"Source counts: {report['source_counts']}")
    print(f"Structured rate: {report['structured_rate']:.2%}")
    print(f"Local fallback rate: {report['local_fallback_rate']:.2%}")
    print(f"Average recall: {report['avg_recall']:.2%}")
    print(f"Average precision: {report['avg_precision']:.2%}")

    print("\n=== Per-label Metrics ===")
    for label, metrics in report["label_metrics"].items():
        print(
            f"{label:<8} recall={metrics['recall']:.2%} precision={metrics['precision']:.2%} "
            f"hits={metrics['hit_count']}/{metrics['expected_count']} actual={metrics['actual_count']}"
        )

    gate = report.get("quality_gate")
    if gate and gate["enabled"]:
        print("\n=== Quality Gate ===")
        print("PASS" if gate["passed"] else "FAIL")
        for failure in gate["failures"]:
            print(f"- {failure}")

    if not show_details:
        return

    print("\n=== Details ===")
    for result in report["results"]:
        print(
            f"\n[{result['id']}] source={result['source']} "
            f"recall={result['avg_recall']:.2%} precision={result['avg_precision']:.2%}"
        )
        print(f"Input: {result['input']}")
        print(f"Actual tasks: {result['actual']['tasks']}")
        print(f"Actual blockers: {result['actual']['blockers']}")
        print(f"Actual plans: {result['actual']['plans']}")
        for key, score in result["scores"].items():
            if score["missed"]:
                print(f"Missed {key}: {score['missed']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate weekly agent structuring quality.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_FILE, help="Path to eval cases JSON file.")
    parser.add_argument("--details", action="store_true", help="Print per-case details.")
    parser.add_argument("--json", action="store_true", help="Print full JSON report.")
    parser.add_argument("--output", type=Path, help="Path to save JSON report.")
    parser.add_argument("--min-recall", type=float, help="Minimum allowed average recall, e.g. 0.85.")
    parser.add_argument("--min-precision", type=float, help="Minimum allowed average precision, e.g. 0.85.")
    parser.add_argument("--max-local-rate", type=float, help="Maximum allowed local fallback rate, e.g. 0.2.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_eval(args.cases))
    report["quality_gate"] = evaluate_quality_gate(report, args)

    if args.output:
        write_report(report, args.output)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report, show_details=args.details)
        if args.output:
            print(f"\nSaved JSON report to: {args.output}")

    if report["quality_gate"]["enabled"] and not report["quality_gate"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
