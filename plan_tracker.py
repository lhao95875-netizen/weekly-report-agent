"""计划闭环对比模块。

拉上周日报结构化出的 plans，与本周的 tasks / blockers 做相似度匹配，
回答周报里最值钱的问题：上周说要做的事，这周到底做了没有。

输出三类信息：
- completed_plans：上周计划中本周已完成/推进的项（附匹配到的本周条目）
- missed_plans：上周计划中本周没有对应工作痕迹的项
- new_work：本周工作中不属于上周计划的新增项

匹配采用字符级 Jaccard 相似度（与 eval harness 同思路），
阈值默认 0.42：计划与实际任务的措辞差异较大，阈值比结构化评估更宽松。
"""

import re
from datetime import datetime, timedelta
from typing import Any

DEFAULT_MATCH_THRESHOLD = 0.42

STOP_TOKENS = [
    "今天", "当前", "目前", "已经", "已", "了", "的", "主要", "继续",
    "准备", "最近", "本周", "下周", "明天", "计划", "开始", "进行",
]


def normalize_text(value: str) -> str:
    text = str(value).lower().strip()
    text = re.sub(r"[\s，,。；;：:\-_/\\'\"“”‘’（）()\[\]【】]+", "", text)
    for token in STOP_TOKENS:
        text = text.replace(token, "")
    return text


def similarity_score(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm and not right_norm:
        return 1.0
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.9
    left_chars = set(left_norm)
    right_chars = set(right_norm)
    union = left_chars | right_chars
    return len(left_chars & right_chars) / len(union) if union else 0.0


def previous_week_range(start: str, end: str) -> tuple[str, str]:
    prev_start = (
        datetime.strptime(start, "%Y-%m-%d") - timedelta(days=7)
    ).date().isoformat()
    prev_end = (
        datetime.strptime(end, "%Y-%m-%d") - timedelta(days=7)
    ).date().isoformat()
    return prev_start, prev_end


def extract_plans(logs: list[dict[str, Any]]) -> list[str]:
    plans: list[str] = []
    for log in logs:
        structured = log.get("structured") or {}
        for plan in structured.get("plans") or []:
            text = str(plan).strip()
            if text:
                plans.append(text)
    return plans


def extract_work_items(logs: list[dict[str, Any]]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for log in logs:
        structured = log.get("structured") or {}
        for task in structured.get("tasks") or []:
            title = str(task.get("title", "")).strip()
            if title:
                items.append({"title": title, "source": "task"})
        for blocker in structured.get("blockers") or []:
            text = str(blocker).strip()
            if text:
                items.append({"title": text, "source": "blocker"})
    return items


def build_plan_followup(
    previous_logs: list[dict[str, Any]],
    current_logs: list[dict[str, Any]],
    threshold: float = DEFAULT_MATCH_THRESHOLD,
) -> dict[str, Any]:
    last_plans = extract_plans(previous_logs)
    current_items = extract_work_items(current_logs)

    if not last_plans:
        return {
            "has_plans": False,
            "completed_plans": [],
            "missed_plans": [],
            "new_work": [item["title"] for item in current_items],
            "threshold": threshold,
        }

    matched_item_indices: set[int] = set()
    completed_plans: list[dict[str, Any]] = []
    missed_plans: list[dict[str, Any]] = []

    for plan in last_plans:
        best_index: int | None = None
        best_score = 0.0
        for idx, item in enumerate(current_items):
            if idx in matched_item_indices:
                continue
            score = similarity_score(plan, item["title"])
            if score > best_score:
                best_score = score
                best_index = idx
        if best_index is not None and best_score >= threshold:
            matched_item_indices.add(best_index)
            completed_plans.append(
                {
                    "plan": plan,
                    "matched_item": current_items[best_index]["title"],
                    "matched_source": current_items[best_index]["source"],
                    "score": round(best_score, 3),
                }
            )
        else:
            missed_plans.append({"plan": plan, "best_score": round(best_score, 3)})

    new_work = [
        item["title"]
        for idx, item in enumerate(current_items)
        if idx not in matched_item_indices and item["source"] == "task"
    ]

    return {
        "has_plans": True,
        "completed_plans": completed_plans,
        "missed_plans": missed_plans,
        "new_work": new_work,
        "threshold": threshold,
    }


def render_plan_followup_text(followup: dict[str, Any]) -> str:
    if not followup.get("has_plans"):
        return ""

    completed = followup.get("completed_plans") or []
    missed = followup.get("missed_plans") or []
    new_work = followup.get("new_work") or []

    lines: list[str] = ["上周计划回顾："]
    if completed:
        lines.append("  已完成或推进：")
        for item in completed:
            lines.append(f"  - {item['plan']}")
    else:
        lines.append("  已完成或推进：暂无")
    if missed:
        lines.append("  未完成：")
        for item in missed:
            lines.append(f"  - {item['plan']}")
    else:
        lines.append("  未完成：暂无")
    if new_work:
        lines.append("  本周新增工作：")
        for title in new_work:
            lines.append(f"  - {title}")
    return "\n".join(lines)
