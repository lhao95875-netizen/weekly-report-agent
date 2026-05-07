# Weekly Report Agent

基于 **FastAPI + LangChain + 阿里云 Qwen** 的智能周报 Agent。项目支持自然语言日报录入、结构化任务抽取、周维度聚合、周报自动生成，并内置 eval harness 用于评估 `tasks / blockers / plans` 抽取质量。

## 功能特性

- 自然语言日报录入
- 基于 LangChain 接入阿里云 DashScope / Qwen
- 使用 Pydantic structured output 约束模型输出结构
- 自动抽取 `tasks`、`blockers`、`plans`
- 模型失败时回退本地规则解析
- 按日期查询日报
- 按周聚合日报数据
- 一键生成中文周报
- 内置 eval harness，评估结构化抽取的 recall / precision
- 提供简单 Web 页面入口

## 技术栈

- Python
- FastAPI
- LangChain
- 阿里云 DashScope / Qwen
- Pydantic
- Uvicorn
- HTML / CSS / JavaScript

## 项目结构

```text
WEEKLY-AGENT/
├─ app.py
├─ requirement.txt
├─ README.md
├─ data/
│  └─ eval_cases.json
├─ scripts/
│  └─ eval_structuring.py
└─ static/
   └─ index.html
```

说明：

- `app.py`：FastAPI 服务、日报结构化、周报生成逻辑
- `static/index.html`：网页入口
- `data/eval_cases.json`：结构化评估样例
- `scripts/eval_structuring.py`：eval harness 评估脚本
- `data/logs.json`：本地日报数据，默认不提交到 Git

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirement.txt
```

### 2. 配置阿里云 Qwen API

本项目使用阿里云 DashScope 的 OpenAI-compatible 接口。

PowerShell 示例：

```powershell
$env:ALIYUN_API_KEY="你的阿里云百炼 API Key"
$env:ALIYUN_MODEL="qwen-plus"
$env:ALIYUN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
```

也可以使用 DashScope 官方环境变量名：

```powershell
$env:DASHSCOPE_API_KEY="你的阿里云百炼 API Key"
```

默认配置：

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `ALIYUN_API_KEY` / `DASHSCOPE_API_KEY` | 无 | 阿里云百炼 API Key |
| `ALIYUN_MODEL` | `qwen-plus` | 使用的 Qwen 模型 |
| `ALIYUN_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | OpenAI-compatible 接口地址 |

### 3. 启动服务

```bash
uvicorn app:app --reload
```

打开浏览器访问：

```text
http://127.0.0.1:8000
```

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/logs` | 新增日报并自动结构化 |
| `GET` | `/logs?date=2026-04-04` | 查看某天日报 |
| `GET` | `/weekly?start=2026-03-30&end=2026-04-05` | 查看周数据 |
| `POST` | `/weekly-report` | 生成周报 |
| `GET` | `/debug/llm` | 检查 LLM 配置和调用状态 |

## 日报结构化流程

保存日报时，系统会按以下顺序处理：

```text
自然语言日报
↓
LangChain + Qwen structured output
↓ 成功
structured_by = aliyun_qwen_structured
↓ 失败
JSON prompt fallback
↓ 成功
structured_by = aliyun_qwen_json
↓ 失败
本地规则 fallback
↓
structured_by = local
```

结构化结果示例：

```json
{
  "summary": "完成 LangChain structured output 接入",
  "tasks": [
    {
      "title": "完成 LangChain structured output 接入",
      "status": "done"
    },
    {
      "title": "新增 eval harness",
      "status": "done"
    }
  ],
  "blockers": [],
  "plans": [
    "下周计划接入 LangGraph"
  ]
}
```

## Eval Harness

项目内置结构化评估脚本，用于评估模型对 `tasks`、`blockers`、`plans` 的抽取质量。

运行：

```bash
python scripts/eval_structuring.py
```

查看详细结果：

```bash
python scripts/eval_structuring.py --details
```

输出 JSON 报告：

```bash
python scripts/eval_structuring.py --json
```

评估指标：

- `source_counts`：结构化来源分布，例如 `aliyun_qwen_structured`、`aliyun_qwen_json`、`local`
- `Average recall`：应识别内容中被成功识别的比例
- `Average precision`：模型输出内容中正确内容的比例

## 调试 LLM 调用

访问：

```text
http://127.0.0.1:8000/debug/llm
```

成功时会返回类似：

```json
{
  "provider": "aliyun_dashscope_openai_compatible",
  "has_aliyun_api_key": true,
  "aliyun_model": "qwen-plus",
  "llm": "ok",
  "llm_response": "pong"
}
```

如果失败，请检查：

- API Key 是否设置在启动 `uvicorn` 的同一个终端中
- `ALIYUN_MODEL` 是否正确
- `ALIYUN_BASE_URL` 是否为 compatible-mode 地址
- 阿里云百炼服务是否开通
- 账户是否有可用额度

## 安全说明

请不要提交真实 API Key 或真实工作日志。

建议忽略：

```gitignore
.env
data/logs.json
```

如需提供示例数据，建议创建 `data/logs.example.json`。

## 后续优化方向

- 增加 `postprocess_structured_log`，进一步修正模型误分类
- 扩展 eval cases 到 30-50 条
- 为 eval harness 增加阈值门禁
- 增加 `/chat` 自然语言入口
- 使用 LangGraph 管理多步骤 Agent 工作流
- 接入向量检索，实现历史日报/周报记忆
- 接入飞书或企业微信机器人，自动提醒和发送周报

## 项目亮点

- 使用 LangChain + Qwen 构建 LLM 调用链路
- 使用 Pydantic structured output 提升结构化稳定性
- 设计模型失败 fallback，增强系统可用性
- 构建 eval harness，用 recall / precision 评估结构化质量
- 基于 FastAPI 实现完整日报录入、查询、聚合和周报生成流程
