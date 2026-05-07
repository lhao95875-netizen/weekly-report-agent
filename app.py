import asyncio
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "data" / "logs.json"
STATIC_DIR = BASE_DIR / "static"
LAST_LLM_ERRORS: list[dict[str, str]] = []


def sanitize_error_message(error: Exception | str) -> str:
    message = str(error)
    message = re.sub(r"([?&]key=)[^&\s']+", r"\1***", message)
    message = re.sub(r"AIza[0-9A-Za-z_-]+", "AIza***", message)
    return message


def record_llm_error(provider: str, error: Exception | str) -> None:
    LAST_LLM_ERRORS.append(
        {
            "time": datetime.now().isoformat(timespec="seconds"),
            "provider": provider,
            "error": sanitize_error_message(error),
        }
    )
    del LAST_LLM_ERRORS[:-10]

app = FastAPI(title="Weekly Report Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class LogCreate(BaseModel):
    date: str = Field(description="日报日期，格式如 2026-04-04")
    content: str = Field(description="当天自然语言工作内容")


class WeeklyReportCreate(BaseModel):
    start: str = Field(description="周开始日期")
    end: str = Field(description="周结束日期")


class StructuredTask(BaseModel):
    title: str = Field(description="任务标题，保留原始工作事项的核心表达")
    status: str = Field(default="done", description="任务状态，已完成用 done，推进中用 doing")


class StructuredLogResult(BaseModel):
    summary: str = Field(default="", description="日报摘要，优先概括当天最重要的完成事项")
    tasks: list[StructuredTask] = Field(default_factory=list, description="已经完成或正在推进的事项")
    blockers: list[str] = Field(default_factory=list, description="阻塞、风险、问题、失败或报错")
    plans: list[str] = Field(default_factory=list, description="明天、下周、后续、下一步计划")


def ensure_data_file() -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        DATA_FILE.write_text("[]", encoding="utf-8")


def load_logs() -> list[dict[str, Any]]:
    ensure_data_file()
    raw = DATA_FILE.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    return json.loads(raw)


def save_logs(logs: list[dict[str, Any]]) -> None:
    DATA_FILE.write_text(
        json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="日期格式必须是 YYYY-MM-DD") from exc


def split_log_items(raw_text: str) -> list[str]:
    normalized = raw_text.replace("\r\n", "\n").strip()
    if not normalized:
        return []

    bullet_lines = [line.strip("- •\t ") for line in normalized.splitlines() if line.strip()]
    if len(bullet_lines) > 1:
        return bullet_lines

    inline_parts = re.split(
        r"[；;。]|(?<!\d)[1-9]\d*[、.]|\s*[-•]\s*",
        normalized,
    )
    lines = [part.strip("- •\t ") for part in inline_parts if part.strip()]
    if len(lines) <= 1:
        comma_parts = re.split(r"[，,]", normalized)
        lines = [part.strip("- •\t ") for part in comma_parts if part.strip()]
    return lines or [normalized]


def classify_log_line(line: str) -> str:
    lowered = line.lower()
    blocker_keywords = ["阻塞", "卡住", "问题", "bug", "风险", "失败", "报错", "异常", "超时", "错误"]
    plan_keywords = ["明天", "下周", "计划", "待做", "todo", "下一步", "后续", "准备", "开始", "继续", "投入"]
    done_keywords = ["完成", "修复", "上线", "联调", "实现", "优化", "整理", "开发", "接入", "验证", "测试"]

    if any(keyword in lowered for keyword in blocker_keywords):
        return "blocker"
    if any(keyword in lowered for keyword in plan_keywords):
        return "plan"
    if any(keyword in lowered for keyword in done_keywords):
        return "task"
    return "task"


def local_structured_log(raw_text: str) -> dict[str, Any]:
    lines = split_log_items(raw_text)
    if not lines:
        lines = [raw_text.strip()]

    tasks: list[dict[str, str]] = []
    blockers: list[str] = []
    plans: list[str] = []

    for line in lines:
        category = classify_log_line(line)
        if category == "blocker":
            blockers.append(line)
        elif category == "plan":
            plans.append(line)
        else:
            tasks.append({"title": line, "status": "done"})

    if not tasks and lines:
        tasks = [{"title": line, "status": "done"} for line in lines if line and line not in plans and line not in blockers]
    if not tasks and (plans or blockers):
        summary = (plans or blockers)[0]
    else:
        summary = tasks[0]["title"] if tasks else lines[0]

    return {
        "summary": summary,
        "tasks": tasks,
        "blockers": blockers,
        "plans": plans,
    }


def get_aliyun_config() -> tuple[str, str, str] | None:
    api_key = os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    model = os.getenv("ALIYUN_MODEL", "qwen-plus")
    base_url = os.getenv(
        "ALIYUN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    if not api_key:
        return None
    return api_key, model, base_url.rstrip("/")


def clean_json_text(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        text = text[start : end + 1]
    return text.strip()


def normalize_structured_log(data: dict[str, Any] | StructuredLogResult) -> dict[str, Any]:
    if isinstance(data, StructuredLogResult):
        data = data.model_dump()

    tasks_raw = data.get("tasks") or []
    tasks: list[dict[str, str]] = []
    for item in tasks_raw:
        if isinstance(item, dict):
            title = str(item.get("title", "")).strip()
            status = str(item.get("status", "done")).strip() or "done"
        else:
            title = str(item).strip()
            status = "done"
        if title:
            tasks.append({"title": title, "status": status})

    blockers = [str(item).strip() for item in (data.get("blockers") or []) if str(item).strip()]
    plans = [str(item).strip() for item in (data.get("plans") or []) if str(item).strip()]
    summary = str(data.get("summary") or "").strip()
    if not summary:
        summary = tasks[0]["title"] if tasks else ""

    return {
        "summary": summary,
        "tasks": tasks,
        "blockers": blockers,
        "plans": plans,
    }


def get_langchain_llm(temperature: float = 0.2) -> ChatOpenAI | None:
    config = get_aliyun_config()
    if not config:
        return None

    api_key, model, base_url = config
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
    )


async def call_langchain(
    prompt_text: str,
    system_text: str,
    temperature: float = 0.2,
    timeout: float = 20.0,
) -> str | None:
    llm = get_langchain_llm(temperature=temperature)
    if not llm:
        return None

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_text),
            ("human", "{input}"),
        ]
    )
    chain = prompt | llm
    try:
        response = await asyncio.wait_for(
            chain.ainvoke({"input": prompt_text}),
            timeout=timeout,
        )
    except Exception as exc:
        record_llm_error("langchain", exc)
        return None
    return str(response.content).strip()


async def call_langchain_structured_log(raw_text: str, timeout: float = 20.0) -> StructuredLogResult | None:
    llm = get_langchain_llm(temperature=0.1)
    if not llm:
        return None

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是一个日报结构化助手。你必须把日报拆成 summary、tasks、blockers、plans。"
                "分类规则："
                "1. tasks 只包含已经完成或正在推进的事项。"
                "2. blockers 只包含尚未解决、正在阻塞、造成风险或影响交付的问题。"
                "3. plans 只包含明天、下周、后续或下一步计划。"
                "4. 如果句子包含“修复/解决/处理/完成/验证”加“异常/问题/bug/报错/失败/超时”，且语义表示已经解决，归入 tasks，不归入 blockers。"
                "5. 如果句子包含“未解决/还没有解决/暂时没解决/阻塞/影响/不可用/失败且未恢复”，归入 blockers。"
                "6. “没有阻塞/暂无阻塞/没有风险/暂无风险”是无阻塞信号，不要放入 blockers。"
                "7. 保留原文核心措辞，不要过度改写，不要编造原文没有的信息。",
            ),
            (
                "human",
                "请结构化下面的日报内容。如果一句话包含多个事项，请按语义拆分。\n\n日报内容：\n{raw_text}",
            ),
        ]
    )
    structured_llm = llm.with_structured_output(StructuredLogResult)
    chain = prompt | structured_llm
    try:
        return await asyncio.wait_for(chain.ainvoke({"raw_text": raw_text}), timeout=timeout)
    except Exception as exc:
        record_llm_error("aliyun_structured_output", exc)
        return None


async def call_llm_structured_log(raw_text: str) -> tuple[dict[str, Any], str] | None:
    structured = await call_langchain_structured_log(raw_text)
    if structured:
        return normalize_structured_log(structured), "aliyun_qwen_structured"

    prompt = f"""请把下面的日报内容拆分成结构化 JSON，字段必须固定为：
summary: 字符串
tasks: 数组，每项包含 title 和 status
blockers: 字符串数组
plans: 字符串数组

要求：
1. tasks 只保留已经完成或正在推进的事项，不要把阻塞和计划混进去。
2. blockers 只保留尚未解决、正在阻塞、造成风险或影响交付的问题。
3. plans 只保留接下来的计划、待办、下一步动作。
4. 如果句子包含“修复/解决/处理/完成/验证”加“异常/问题/bug/报错/失败/超时”，且语义表示已经解决，归入 tasks，不归入 blockers。
5. 如果句子包含“未解决/还没有解决/暂时没解决/阻塞/影响/不可用/失败且未恢复”，归入 blockers。
6. “没有阻塞/暂无阻塞/没有风险/暂无风险”是无阻塞信号，不要放入 blockers。
7. 即使原文是一整段话，也要按语义拆成多项，不要把整段原文塞进一个 task。
8. 如果某个字段没有内容，返回空数组或空字符串。
9. 只返回 JSON，不要返回 markdown，不要解释。

日报内容：
{raw_text}
"""
    content = await call_langchain(
        prompt,
        "你是一个日报助手，擅长把自然语言日报拆分成 tasks、blockers、plans 三类结构化信息。输出必须是合法 JSON。",
    )
    if content:
        return normalize_structured_log(json.loads(clean_json_text(content))), "aliyun_qwen_json"
    return None


async def to_structured_log(raw_text: str) -> tuple[dict[str, Any], str]:
    try:
        result = await call_llm_structured_log(raw_text)
        if result:
            return result
    except Exception as exc:
        record_llm_error("structured_parse", exc)
    return local_structured_log(raw_text), "local"


def filter_logs_by_date(logs: list[dict[str, Any]], target: str) -> list[dict[str, Any]]:
    return [log for log in logs if log["date"] == target]


def filter_logs_by_range(logs: list[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    start_date = parse_date(start)
    end_date = parse_date(end)
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end 不能早于 start")
    result = []
    for log in logs:
        current = parse_date(log["date"])
        if start_date <= current <= end_date:
            result.append(log)
    return result


def local_weekly_summary(start: str, end: str, logs: list[dict[str, Any]]) -> str:
    if not logs:
        return f"{start} 到 {end} 暂无日报记录。"

    done_items: list[str] = []
    blocker_items: list[str] = []
    next_items: list[str] = []
    for log in logs:
        structured = log.get("structured", {})
        for task in structured.get("tasks", []):
            title = task.get("title")
            if title:
                done_items.append(f"- {title}")
        for blocker in structured.get("blockers", []):
            blocker_items.append(f"- {blocker}")
        for plan in structured.get("plans", []):
            next_items.append(f"- {plan}")

    sections = [
        f"本周时间：{start} 至 {end}",
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


async def call_llm_weekly_summary(start: str, end: str, logs: list[dict[str, Any]]) -> str | None:
    prompt = json.dumps(
        {
            "start": start,
            "end": end,
            "logs": logs,
            "instruction": "请生成中文周报，分成本周完成、风险与阻塞、下周计划三部分，表达专业简洁。",
        },
        ensure_ascii=False,
    )
    content = await call_langchain(
        prompt,
        "你是一个专业周报助手，擅长总结工作周报。",
        timeout=60.0,
    )
    if content:
        return content
    return None


async def generate_weekly_summary(start: str, end: str, logs: list[dict[str, Any]]) -> str:
    try:
        content = await call_llm_weekly_summary(start, end, logs)
        if content:
            return content
    except Exception:
        pass
    return local_weekly_summary(start, end, logs)


@app.get("/debug/llm")
async def debug_llm() -> dict[str, Any]:
    config = get_aliyun_config()
    info: dict[str, Any] = {
        "provider": "aliyun_dashscope_openai_compatible",
        "has_aliyun_api_key": bool(os.getenv("ALIYUN_API_KEY") or os.getenv("DASHSCOPE_API_KEY")),
        "aliyun_model": os.getenv("ALIYUN_MODEL", "qwen-plus"),
        "aliyun_base_url": os.getenv(
            "ALIYUN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        "recent_errors": LAST_LLM_ERRORS,
    }
    if not config:
        info["llm"] = "skipped: ALIYUN_API_KEY or DASHSCOPE_API_KEY is missing"
        return info

    content = await call_langchain(
        "只回复 pong，不要解释。",
        "你是健康检查助手。",
        timeout=10.0,
    )
    info["llm"] = "ok" if content else "failed"
    info["llm_response"] = content
    info["recent_errors"] = LAST_LLM_ERRORS
    return info


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/logs")
async def create_log(payload: LogCreate) -> dict[str, Any]:
    parse_date(payload.date)
    logs = load_logs()
    structured, structured_by = await to_structured_log(payload.content)
    new_log = {
        "date": payload.date,
        "content": payload.content,
        "structured": structured,
        "structured_by": structured_by,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    logs.append(new_log)
    save_logs(logs)
    return {"message": "日报已保存", "log": new_log}


@app.get("/logs")
def get_logs(date: str) -> dict[str, Any]:
    parse_date(date)
    logs = filter_logs_by_date(load_logs(), date)
    return {"date": date, "logs": logs}


@app.get("/weekly")
def get_weekly(start: str, end: str) -> dict[str, Any]:
    logs = filter_logs_by_range(load_logs(), start, end)
    return {
        "start": start,
        "end": end,
        "count": len(logs),
        "logs": logs,
    }


@app.post("/weekly-report")
async def create_weekly_report(payload: WeeklyReportCreate) -> dict[str, Any]:
    logs = filter_logs_by_range(load_logs(), payload.start, payload.end)
    report = await generate_weekly_summary(payload.start, payload.end, logs)
    return {
        "start": payload.start,
        "end": payload.end,
        "count": len(logs),
        "report": report,
        "logs": logs,
    }
