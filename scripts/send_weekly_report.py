from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib import error, request

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_FILE = BASE_DIR / "data" / "logs.json"


@dataclass(frozen=True)
class WeeklyRange:
    start: str
    end: str


def current_week_range() -> WeeklyRange:
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=4)
    return WeeklyRange(start=start.isoformat(), end=end.isoformat())


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def load_logs() -> list[dict[str, Any]]:
    if not DATA_FILE.exists():
        return []
    raw = DATA_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return json.loads(raw)


def filter_logs_by_range(logs: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    start_dt = parse_date(start)
    end_dt = parse_date(end)
    if end_dt < start_dt:
        raise ValueError("end cannot be earlier than start")

    result: list[dict[str, Any]] = []
    for log in logs:
        try:
            log_dt = parse_date(str(log.get("date", "")))
        except ValueError:
            continue
        if start_dt <= log_dt <= end_dt:
            result.append(log)
    return result


def _normalize_task_item(item: Any) -> str:
    if isinstance(item, dict):
        title = str(item.get("title", "")).strip()
        status = str(item.get("status", "")).strip()
        if title and status:
            return f"- {title} ({status})"
        if title:
            return f"- {title}"
    text = str(item).strip()
    return f"- {text}" if text else ""


def build_weekly_input(logs: list[dict[str, Any]], start: str, end: str) -> str:
    if not logs:
        return f"本周时间：{start} 到 {end}\n\n本周没有日报数据。"

    lines = [f"本周时间：{start} 到 {end}", ""]
    for log in logs:
        log_date = str(log.get("date", "")).strip() or "未知日期"
        content = str(log.get("content", "")).strip()
        structured = log.get("structured") if isinstance(log.get("structured"), dict) else {}
        lines.append(f"{log_date}:")
        if content:
            lines.append(content)
        else:
            tasks = structured.get("tasks", []) if isinstance(structured, dict) else []
            blockers = structured.get("blockers", []) if isinstance(structured, dict) else []
            plans = structured.get("plans", []) if isinstance(structured, dict) else []

            if tasks:
                lines.append("已完成/推进事项：")
                for item in tasks:
                    normalized = _normalize_task_item(item)
                    if normalized:
                        lines.append(normalized)
            if blockers:
                lines.append("风险与阻塞：")
                for item in blockers:
                    text = str(item).strip()
                    if text:
                        lines.append(f"- {text}")
            if plans:
                lines.append("后续计划：")
                for item in plans:
                    text = str(item).strip()
                    if text:
                        lines.append(f"- {text}")
        lines.append("")
    return "\n".join(lines).strip()


def local_weekly_summary(start: str, end: str, logs: list[dict[str, Any]]) -> str:
    if not logs:
        return f"本周时间：{start} 到 {end}\n\n本周暂无日报记录。"

    done_items: list[str] = []
    blocker_items: list[str] = []
    next_items: list[str] = []

    for log in logs:
        structured = log.get("structured", {}) if isinstance(log.get("structured"), dict) else {}
        for task in structured.get("tasks", []):
            if isinstance(task, dict):
                title = str(task.get("title", "")).strip()
                if title:
                    done_items.append(f"- {title}")
            else:
                title = str(task).strip()
                if title:
                    done_items.append(f"- {title}")
        for blocker in structured.get("blockers", []):
            text = str(blocker).strip()
            if text:
                blocker_items.append(f"- {text}")
        for plan in structured.get("plans", []):
            text = str(plan).strip()
            if text:
                next_items.append(f"- {text}")

    sections = [
        f"本周时间：{start} 到 {end}",
        "",
        "一、本周完成",
        *(done_items or ["- 暂无"]),
        "",
        "二、风险与阻塞",
        *(blocker_items or ["- 暂无"]),
        "",
        "三、下周计划",
        *(next_items or ["- 暂无"]),
    ]
    return "\n".join(sections)


def call_qwen_summary(prompt_text: str) -> str:
    api_key = os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ALIYUN_API_KEY or DASHSCOPE_API_KEY")

    model = os.getenv("ALIYUN_MODEL", "qwen-plus")
    base_url = os.getenv("ALIYUN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是一个专业的周报助手。请把输入内容总结成中文周报，"
                    "固定分为三个部分：本周完成、风险与阻塞、下周计划。"
                    "要求表达简洁、专业、适合发给导师。"
                ),
            },
            {"role": "user", "content": prompt_text},
        ],
        "temperature": 0.2,
    }

    req = request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Qwen HTTPError {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Qwen URLError: {exc}") from exc

    data = json.loads(raw)
    try:
        return str(data["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Qwen response: {raw}") from exc


def send_gmail_smtp(recipient: str, subject: str, body: str) -> None:
    gmail_user = os.getenv("GMAIL_USER")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
    if not gmail_user or not gmail_app_password:
        raise RuntimeError("Missing GMAIL_USER or GMAIL_APP_PASSWORD")

    msg = EmailMessage()
    msg["From"] = gmail_user
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=60) as server:
        server.login(gmail_user, gmail_app_password)
        server.send_message(msg)


def build_email_body(start: str, end: str, logs: list[dict[str, Any]]) -> tuple[str, str]:
    prompt_text = build_weekly_input(logs, start, end)
    try:
        summary = call_qwen_summary(prompt_text)
        source = "qwen"
    except Exception as exc:
        summary = local_weekly_summary(start, end, logs)
        source = f"local_fallback ({exc})"

    body = f"""老师您好，

以下是我 {start} 到 {end} 的周报：

{summary}

——
来源：{source}
"""
    subject = f"周报汇总 {start} ~ {end}"
    return subject, body


def main() -> None:
    week_range = current_week_range()
    logs = load_logs()
    weekly_logs = filter_logs_by_range(logs, week_range.start, week_range.end)

    recipient = os.getenv("GMAIL_TO")
    if not recipient:
        raise RuntimeError("Missing GMAIL_TO")

    subject, body = build_email_body(week_range.start, week_range.end, weekly_logs)

    dry_run = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes", "y"}
    if dry_run:
        print("=== DRY RUN ===")
        print(f"To: {recipient}")
        print(f"Subject: {subject}")
        print(body)
        return

    send_gmail_smtp(recipient, subject, body)
    print(f"Sent weekly report to {recipient}")


if __name__ == "__main__":
    main()
