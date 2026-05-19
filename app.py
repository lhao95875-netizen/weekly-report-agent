import asyncio
import json
import os
import re
from datetime import date, datetime, timedelta
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


class ChatRequest(BaseModel):
    message: str = Field(description="用户自然语言请求")
    action: str | None = Field(default=None, description="可选显式动作，例如 create_log、get_weekly_report、run_eval")
    date: str | None = Field(default=None, description="可选日期，格式 YYYY-MM-DD")
    start: str | None = Field(default=None, description="可选开始日期，格式 YYYY-MM-DD")
    end: str | None = Field(default=None, description="可选结束日期，格式 YYYY-MM-DD")


class ChatIntent(BaseModel):
    intent: str = Field(
        description="意图类型，只能是 create_log、get_daily_log、get_weekly_report、get_weekly_data、run_eval、debug_llm、unknown"
    )
    date: str | None = Field(default=None, description="日报日期，格式 YYYY-MM-DD")
    start: str | None = Field(default=None, description="周报开始日期，格式 YYYY-MM-DD")
    end: str | None = Field(default=None, description="周报结束日期，格式 YYYY-MM-DD")
    content: str | None = Field(default=None, description="需要保存的日报正文")
    reason: str = Field(default="", description="简要说明为什么选择该意图")
    resolved_by: str = Field(default="rules", description="意图由 rules 还是 llm 解析得到")


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


def today_str() -> str:
    return date.today().isoformat()


def current_week_range() -> tuple[str, str]:
    today = date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def last_week_range() -> tuple[str, str]:
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)
    return last_monday.isoformat(), last_sunday.isoformat()


def extract_iso_date(text: str) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else None


def extract_date_range(text: str) -> tuple[str | None, str | None]:
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    return None, None


def strip_chat_command_prefix(message: str) -> str:
    text = message.strip()
    patterns = [
        r"^帮我保存(?:今天|昨日|昨天)?日报[:：，,\s]*",
        r"^保存(?:今天|昨日|昨天)?日报[:：，,\s]*",
        r"^记录(?:今天|昨日|昨天)?日报[:：，,\s]*",
        r"^新增(?:今天|昨日|昨天)?日报[:：，,\s]*",
        r"^日报[:：，,\s]*",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "", text)
    return text.strip() or message.strip()


def is_eval_command(message: str) -> bool:
    lowered = message.lower().strip()
    eval_patterns = [
        r"^(run|执行|运行|跑)\s*eval(?:\s+harness)?$",
        r"^(run|执行|运行|跑)\s*harness$",
        r"^(执行|运行|跑).*(结构化)?评估$",
        r"^(执行|运行|跑).*质量门禁$",
        r"^eval\s+harness$",
    ]
    return any(re.search(pattern, lowered) for pattern in eval_patterns)


def infer_chat_intent_by_rules(payload: ChatRequest) -> ChatIntent:
    message = payload.message.strip()
    lowered = message.lower()
    action = (payload.action or "").strip().lower()
    start_from_text, end_from_text = extract_date_range(message)
    date_from_text = extract_iso_date(message)

    start = payload.start or start_from_text
    end = payload.end or end_from_text
    if not start or not end:
        if any(token in message for token in ["本周", "这周", "周报", "weekly"]):
            default_start, default_end = current_week_range()
            start = start or default_start
            end = end or default_end

    target_date = payload.date or date_from_text
    if not target_date and any(token in message for token in ["今天", "今日"]):
        target_date = today_str()
    if not target_date and any(token in message for token in ["昨天", "昨日"]):
        target_date = (date.today() - timedelta(days=1)).isoformat()

    if action in {"create_log", "save_log", "log"}:
        return ChatIntent(
            intent="create_log",
            date=target_date or today_str(),
            content=strip_chat_command_prefix(message),
            reason="用户显式指定保存日报动作",
            resolved_by="rules",
        )
    if action in {"get_daily_log", "query_log"}:
        return ChatIntent(
            intent="get_daily_log",
            date=target_date or today_str(),
            reason="用户显式指定查询日报动作",
            resolved_by="rules",
        )
    if action in {"get_weekly_report", "weekly_report"}:
        default_start, default_end = last_week_range()
        return ChatIntent(
            intent="get_weekly_report",
            start=start or default_start,
            end=end or default_end,
            reason="用户显式指定生成周报动作",
            resolved_by="rules",
        )
    if action in {"get_weekly_data", "weekly_data"}:
        default_start, default_end = last_week_range()
        return ChatIntent(
            intent="get_weekly_data",
            start=start or default_start,
            end=end or default_end,
            reason="用户显式指定查询周数据动作",
            resolved_by="rules",
        )
    if action == "run_eval":
        return ChatIntent(intent="run_eval", reason="用户显式指定运行结构化评估", resolved_by="rules")
    if action == "debug_llm":
        return ChatIntent(intent="debug_llm", reason="用户显式指定检查 LLM 状态", resolved_by="rules")

    if any(token in message for token in ["保存", "记录", "新增", "写入", "帮我保存"]):
        return ChatIntent(
            intent="create_log",
            date=target_date or today_str(),
            content=strip_chat_command_prefix(message),
            reason="用户请求保存日报",
            resolved_by="rules",
        )

    if any(token in message for token in ["查看日报", "查询日报", "看看日报", "今天日报", "昨天日报"]):
        return ChatIntent(
            intent="get_daily_log",
            date=target_date or today_str(),
            reason="用户请求查询日报",
            resolved_by="rules",
        )

    if is_eval_command(message):
        return ChatIntent(intent="run_eval", reason="用户请求运行结构化评估", resolved_by="rules")

    if any(token in message for token in ["调试", "健康检查", "检查模型", "检查LLM", "检查 llm"]):
        return ChatIntent(intent="debug_llm", reason="用户请求检查 LLM 状态", resolved_by="rules")

    if any(token in message for token in ["生成周报", "写周报", "周报"]):
        default_start, default_end = last_week_range()
        return ChatIntent(
            intent="get_weekly_report",
            start=start or default_start,
            end=end or default_end,
            reason="用户请求生成周报",
            resolved_by="rules",
        )

    if any(token in message for token in ["周数据", "本周数据", "这周数据", "查看本周"]):
        default_start, default_end = last_week_range()
        return ChatIntent(
            intent="get_weekly_data",
            start=start or default_start,
            end=end or default_end,
            reason="用户请求查看周数据",
            resolved_by="rules",
        )

    return ChatIntent(intent="unknown", reason="规则无法识别明确意图", resolved_by="rules")


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


async def parse_chat_intent(payload: ChatRequest) -> ChatIntent:
    llm = get_langchain_llm(temperature=0)
    if llm:
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是周报 Agent 的意图识别器。请把用户请求解析为固定 schema。"
                    "intent 只能是 create_log、get_daily_log、get_weekly_report、get_weekly_data、run_eval、debug_llm、unknown。"
                    "如果用户要保存/记录/新增日报，intent=create_log，并把日报正文放入 content。"
                    "如果用户要查询某天日报，intent=get_daily_log。"
                    "如果用户要生成周报，intent=get_weekly_report。"
                    "如果用户要查看周数据但不要求生成报告，intent=get_weekly_data。"
                    "如果用户要运行 eval/harness/质量评估，intent=run_eval。"
                    "如果用户要检查 LLM 状态，intent=debug_llm。"
                    "日期必须使用 YYYY-MM-DD；如果无法判断日期可留空。不要编造用户没有表达的日报正文。",
                ),
                (
                    "human",
                    "用户请求：{message}\n可选 date={date}\n可选 start={start}\n可选 end={end}",
                ),
            ]
        )
        structured_llm = llm.with_structured_output(ChatIntent)
        chain = prompt | structured_llm
        try:
            parsed = await asyncio.wait_for(
                chain.ainvoke(
                    {
                        "message": payload.message,
                        "date": payload.date,
                        "start": payload.start,
                        "end": payload.end,
                    }
                ),
                timeout=10.0,
            )
        except Exception as exc:
            record_llm_error("chat_intent", exc)
        else:
            if parsed.intent == "create_log":
                parsed.date = parsed.date or payload.date or extract_iso_date(payload.message) or today_str()
                parsed.content = parsed.content or strip_chat_command_prefix(payload.message)
            elif parsed.intent == "get_daily_log":
                parsed.date = parsed.date or payload.date or extract_iso_date(payload.message) or today_str()
            elif parsed.intent in {"get_weekly_report", "get_weekly_data"}:
                default_start, default_end = last_week_range()
                parsed.start = parsed.start or payload.start or default_start
                parsed.end = parsed.end or payload.end or default_end
            parsed.resolved_by = "llm"
            return parsed

    rule_intent = infer_chat_intent_by_rules(payload)
    rule_intent.resolved_by = "rules"
    return rule_intent


async def tool_create_log(date_value: str, content: str) -> dict[str, Any]:
    parse_date(date_value)
    if not content.strip():
        raise HTTPException(status_code=400, detail="保存日报需要提供日报内容")

    logs = load_logs()
    structured, structured_by = await to_structured_log(content)
    new_log = {
        "date": date_value,
        "content": content,
        "structured": structured,
        "structured_by": structured_by,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    logs.append(new_log)
    save_logs(logs)
    return {"message": "日报已保存", "log": new_log}


def tool_get_daily_log(date_value: str) -> dict[str, Any]:
    parse_date(date_value)
    logs = filter_logs_by_date(load_logs(), date_value)
    return {"date": date_value, "logs": logs}


def tool_get_weekly_data(start: str, end: str) -> dict[str, Any]:
    logs = filter_logs_by_range(load_logs(), start, end)
    return {
        "start": start,
        "end": end,
        "count": len(logs),
        "logs": logs,
    }


async def tool_create_weekly_report(start: str, end: str) -> dict[str, Any]:
    weekly_data = tool_get_weekly_data(start, end)
    report = await generate_weekly_summary(start, end, weekly_data["logs"])
    return {
        "start": start,
        "end": end,
        "count": weekly_data["count"],
        "report": report,
        "logs": weekly_data["logs"],
    }


def tool_run_eval() -> dict[str, Any]:
    return {
        "message": "请在终端运行 eval harness，以避免 Web 请求中触发长时间 LLM 批量调用。",
        "command": "python scripts/eval_structuring.py --details --min-recall 0.85 --min-precision 0.85 --max-local-rate 0.20 --output reports/eval-latest.json",
        "supported_metrics": [
            "avg_recall",
            "avg_precision",
            "structured_rate",
            "local_fallback_rate",
            "label_metrics.tasks",
            "label_metrics.blockers",
            "label_metrics.plans",
        ],
    }


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
        "note": "意图识别优先走规则兜底，LLM 只在规则无法明确判断时参与",
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
    return await tool_create_log(payload.date, payload.content)


@app.get("/logs")
def get_logs(date: str) -> dict[str, Any]:
    return tool_get_daily_log(date)


@app.get("/weekly")
def get_weekly(start: str, end: str) -> dict[str, Any]:
    return tool_get_weekly_data(start, end)


@app.post("/weekly-report")
async def create_weekly_report(payload: WeeklyReportCreate) -> dict[str, Any]:
    return await tool_create_weekly_report(payload.start, payload.end)


@app.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, Any]:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message 不能为空")

    intent = await parse_chat_intent(payload)
    result: dict[str, Any]

    if intent.intent == "create_log":
        result = await tool_create_log(intent.date or today_str(), intent.content or payload.message)
    elif intent.intent == "get_daily_log":
        result = tool_get_daily_log(intent.date or today_str())
    elif intent.intent == "get_weekly_report":
        start, end = last_week_range()
        result = await tool_create_weekly_report(start, end)
    elif intent.intent == "get_weekly_data":
        start, end = last_week_range()
        result = tool_get_weekly_data(start, end)
    elif intent.intent == "run_eval":
        result = tool_run_eval()
    elif intent.intent == "debug_llm":
        result = await debug_llm()
    else:
        result = {
            "message": "暂时无法识别你的请求。你可以说：保存今天日报、查询今天日报、生成本周周报、运行 eval。",
            "examples": [
                "帮我保存今天日报：完成 README 优化，明天接入 LangGraph",
                "查询今天日报",
                "生成本周周报",
                "运行 eval harness",
            ],
        }

    return {
        "intent": intent.model_dump(),
        "result": result,
    }
