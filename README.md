# Weekly Report Agent

基于 **FastAPI + LangChain + 阿里云 Qwen** 的智能周报 Agent。项目支持自然语言日报录入、聊天式意图识别、结构化任务抽取、周维度聚合、周报自动生成，并内置 eval harness 用于评估 `tasks / blockers / plans` 抽取质量。

## 功能特性

- 自然语言日报录入
- 基于 LangChain 接入阿里云 DashScope / Qwen
- 使用 Pydantic structured output 约束模型输出结构
- 聊天式意图识别，支持保存日报、查询日报、周数据、周报、git 提交、eval、LLM 调试
- 自动抽取 `tasks`、`blockers`、`plans`
- **自动扫描多个 git 仓库，按作者与周范围采集提交作为周报素材**
- **上周计划闭环对比：自动核对上周 plans 与本周 tasks，标出已完成 / 未完成 / 新增工作**
- 意图识别优先走 LLM，失败时回退本地规则
- 结构化分解优先走 LLM，失败时回退本地规则
- 按日期查询日报
- 周数据和周报默认自动使用上个周一到周日
- 一键生成中文周报
- 内置 eval harness，评估结构化抽取的 recall / precision
- 默认启用 eval 质量门禁，不达标时返回非零退出码
- 支持 JSON eval 报告输出
- 提供支持日报直录与聊天式调试的 Web 页面入口

## 技术栈

- Python
- FastAPI
- LangChain
- 阿里云 DashScope / Qwen
- Pydantic
- Uvicorn
- HTML / CSS / JavaScript

## 系统架构

```text
 ┌──────────────── 数据源 ────────────────┐
 │ 日报录入        Git 提交      上周日报 │
 │ (自然语言)      (多仓库扫描)   (plans) │
 └────┬───────────────┬──────────────┬───┘
      ↓               ↓              ↓
  FastAPI API / Web UI   git_collector   plan_tracker
      ↓               ↓              ↓
  LangChain + Qwen   按仓库分组提交   计划闭环对比
  structured output  (hash/time/msg)  done/missed/new
      └───────────────┴──────────────┘
                      ↓
              Weekly Aggregator（三类材料融合）
                      ↓
              Weekly Report Generator
                      ↓
          本地规则 fallback / 流式输出
```

周报生成时会同时拿到三类材料，缺任何一类都不影响其余部分：

1. `weekly_logs`：本周日报的结构化结果（tasks / blockers / plans）
2. `git_commits`：本周 git 提交，按仓库分组
3. `plan_followup`：上周计划 vs 本周工作的对比结果

结构化质量评估链路：

```text
Eval Cases (data/eval_cases.json)
      ↓
Current Structuring Pipeline
      ↓
Expected vs Actual Matching
      ↓
Per-label Precision / Recall
      ↓
Quality Gate
      ↓
JSON Report / CI Exit Code
```

## 项目结构

```text
WEEKLY-AGENT/
├─ app.py
├─ git_collector.py
├─ plan_tracker.py
├─ config.json            # 本地配置，不提交
├─ config.example.json
├─ requirement.txt
├─ README.md
├─ data/
│  ├─ eval_cases.json
│  └─ logs.json
├─ reports/
│  └─ eval-example.json
├─ scripts/
│  ├─ eval_structuring.py
│  └─ send_weekly_report.py
└─ static/
   └─ index.html
```

说明：

- `app.py`：FastAPI 服务、日报结构化、周报生成逻辑
- `git_collector.py`：扫描仓库、按作者与周范围采集 git 提交
- `plan_tracker.py`：上周计划与本周工作的闭环对比
- `static/index.html`：网页入口
- `config.json`：本地 git 扫描配置（不提交，参考 `config.example.json`）
- `data/eval_cases.json`：结构化评估样例
- `scripts/eval_structuring.py`：eval harness 评估脚本
- `reports/eval-example.json`：示例 eval JSON 报告
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

PowerShell 示例：

```powershell
$env:ALIYUN_API_KEY="你的阿里云百炼 API Key"
$env:ALIYUN_MODEL="qwen-plus"
$env:ALIYUN_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
uvicorn app:app --reload
```

如果你只想先跑本地规则和前端，也可以不配置 API Key，但意图识别与结构化分解就会更多依赖回退逻辑。

打开浏览器访问：

```text
http://127.0.0.1:8000
```

## Git 提交采集（数据源）

周报不再只依赖手动录入的日报：系统会自动扫描多个仓库的 git 提交，作为周报素材。

### 配置

复制示例配置：

```bash
cp config.example.json config.json
```

编辑 `config.json`：

```json
{
  "git": {
    "scan_dir": "D:/CURSOR",
    "max_depth": 3,
    "author": "",
    "exclude_dirs": ["node_modules", ".venv", "dist", "build"]
  }
}
```

| 字段 | 说明 |
|---|---|
| `scan_dir` | 仓库根目录，扫描其下所有含 `.git` 的目录 |
| `max_depth` | 扫描深度，默认 3（参考 kang-skills/weekly-report） |
| `author` | 提交作者；留空自动读取 `git config user.name`。支持正则，例如 `zhangsan|zhangsan@company.com` 可匹配多个身份 |
| `exclude_dirs` | 扫描时跳过的目录 |

也可以用环境变量覆盖，优先级高于 `config.json`：

```powershell
$env:WEEKLY_GIT_SCAN_DIR="D:/CURSOR"
$env:WEEKLY_GIT_MAX_DEPTH="3"
$env:WEEKLY_GIT_AUTHOR="lhao95875-netizen"
```

### 采集流程

```text
扫描 scanDir 下所有 git 仓库（max_depth 层）
      ↓
读取 git 用户身份（或 config / 环境变量指定）
      ↓
git log --all --no-merges --since --until 过滤本周提交
      ↓
按仓库分组：repo / commit_count / commits(hash, date, message)
      ↓
作为证据底料喂给周报生成
```

只采集提交元信息（时间 + 提交信息），不读取 diff，速度快也避免上下文爆炸。

### 单独预览采集结果

```bash
curl "http://127.0.0.1:8000/git/commits?start=2026-09-15&end=2026-09-21"
```

返回示例：

```json
{
  "configured": true,
  "scan_dir": "D:/CURSOR",
  "author": "lhao95875-netizen",
  "scanned_repos": 8,
  "active_repos": 2,
  "total_commits": 17,
  "repos": [
    {
      "repo": "weekly-report-agent",
      "commit_count": 2,
      "commits": [
        {"hash": "2cfea6c", "date": "2026-09-18 14:20", "message": "feat: add git commit collector"}
      ]
    }
  ]
}
```

### 排错

| 现象 | 检查项 |
|---|---|
| `configured: false` | 没有配置 `git.scan_dir`，或环境变量为空 |
| `scanned_repos: 0` | 扫描目录写错，或仓库层级超过 `max_depth` |
| `total_commits: 0` | 作者不匹配（用正则兼容多个身份）、日期区间内确实没有提交 |
| 命中了不该算的提交 | 提交信息乱码通常为编码问题，模块统一按 UTF-8 读取 |

## 计划闭环对比

周报里最值钱的信息是「上周说要做的事，这周做了没有」。生成周报时会自动：

1. 取出上一周日报结构化出的 `plans`
2. 与本周的 `tasks` / `blockers` 做相似度匹配（字符级 Jaccard，阈值 0.42，计划与完成的措辞差异较大，阈值比结构化 eval 更宽松）
3. 输出三类结果：

| 字段 | 含义 |
|---|---|
| `completed_plans` | 上周计划中本周已完成或推进的项，附匹配到的本周条目与分数 |
| `missed_plans` | 上周计划中本周没有工作痕迹的项 |
| `new_work` | 本周不属于上周计划的新增工作 |

周报的「上周计划回顾」部分直接由该结果生成；没有上周数据时会优雅跳过这一段。

## API 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/chat` | 自然语言 Agent 入口，自动识别意图并调用工具 |
| `POST` | `/logs` | 新增日报并自动结构化 |
| `GET` | `/logs?date=2026-04-04` | 查看某天日报 |
| `GET` | `/weekly?start=2026-03-30&end=2026-04-05` | 查看周数据（聊天式入口默认自动取上个周一到周日） |
| `GET` | `/git/commits?start=2026-09-15&end=2026-09-21` | 采集并返回这段时间各仓库的 git 提交 |
| `POST` | `/weekly-report` | 生成周报（默认融合日报 + git 提交 + 计划闭环） |
| `GET` | `/debug/llm` | 检查 LLM 配置和调用状态 |

`/weekly-report` 支持两个可选开关：

```bash
curl -X POST http://127.0.0.1:8000/weekly-report \
  -H "Content-Type: application/json" \
  -d '{"start":"2026-09-15","end":"2026-09-21","include_git":true,"include_followup":true}'
```

| 字段 | 默认值 | 说明 |
|---|---|---|
| `include_git` | `true` | 是否自动采集 git 提交 |
| `include_followup` | `true` | 是否自动对比上周计划 |

返回的 `git` 字段是采集摘要（扫描目录、作者、仓库数、提交数），`plan_followup` 字段是闭环对比明细，完整提交内容已融合进 `report` 正文。

## Agent Chat 入口

`/chat` 是项目的自然语言 Agent 入口。用户不需要直接记住每个 REST API，可以用一句话表达目标，系统会先识别意图，再调用对应工具。

当前支持的意图：

| intent | 说明 | 对应工具 |
|---|---|---|
| `create_log` | 保存日报 | `tool_create_log` |
| `get_daily_log` | 查询某天日报 | `tool_get_daily_log` |
| `get_weekly_data` | 查询周数据 | `tool_get_weekly_data` |
| `get_weekly_report` | 生成周报 | `tool_create_weekly_report` |
| `get_git_commits` | 查看 git 提交 | `tool_get_git_commits` |
| `run_eval` | 返回 eval harness 运行命令 | `tool_run_eval` |
| `debug_llm` | 检查 LLM 状态 | `/debug/llm` |

`/chat` 的返回里会同时包含 `intent` 和 `result`，方便你判断是意图识别错误、工具执行失败，还是 LLM 配置问题。

示例：保存今天日报。

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我保存今天日报：完成 README 展示优化，新增 eval 示例报告，明天开始接入 LangGraph"}'
```

示例：生成本周周报。

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"帮我生成本周周报"}'
```

示例：运行 eval harness。

```bash
curl -X POST http://127.0.0.1:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"运行 eval harness"}'
```

返回结构会包含识别到的 `intent` 和工具执行 `result`，便于调试 Agent 路由行为。

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

项目内置结构化评估脚本，用于评估模型对 `tasks`、`blockers`、`plans` 的抽取质量，并默认启用质量门禁和 JSON 报告输出。

当前脚本的门禁默认值是：

- `--min-recall 0.85`
- `--min-precision 0.85`
- `--max-local-rate 0.20`

如果想只看详细结果而不写文件，可以直接运行：

基础运行：

```bash
python scripts/eval_structuring.py
```

运行时会自动应用默认门禁；如果指标未达标，脚本会返回非零退出码，适合接 CI 或发布前检查。

查看详细结果：

```bash
python scripts/eval_structuring.py --details
```

输出 JSON 到终端：

```bash
python scripts/eval_structuring.py --json
```

保存 JSON 报告：

```bash
python scripts/eval_structuring.py --output reports/eval-latest.json
```

启用质量门禁：

```bash
python scripts/eval_structuring.py \
  --min-recall 0.85 \
  --min-precision 0.85 \
  --max-local-rate 0.20 \
  --output reports/eval-latest.json
```

如果整体 recall / precision 低于阈值，或本地 fallback 比例高于阈值，脚本会返回非零退出码，可用于 CI 或发布前检查。

### 示例输出

```text
=== Weekly Agent Structuring Eval ===
Total cases: 10
Source counts: {'aliyun_qwen_structured': 8, 'aliyun_qwen_json': 1, 'local': 1}
Structured rate: 90.00%
Local fallback rate: 10.00%
Average recall: 91.67%
Average precision: 88.33%

=== Per-label Metrics ===
tasks    recall=95.00% precision=90.48% hits=19/20 actual=21
blockers recall=85.71% precision=85.71% hits=6/7 actual=7
plans    recall=93.33% precision=93.33% hits=14/15 actual=15

=== Quality Gate ===
PASS

Saved JSON report to: reports/eval-latest.json
```

### 质量门禁说明

Eval harness 会先执行当前结构化链路，再把实际输出和 `data/eval_cases.json` 中的期望结果进行匹配，聚合出整体和分标签指标。

门禁参数：

| 参数 | 说明 |
|---|---|
| `--min-recall` | 整体平均 recall 必须达到的最低值 |
| `--min-precision` | 整体平均 precision 必须达到的最低值 |
| `--max-local-rate` | 本地规则 fallback 比例允许的最高值 |

如果任一指标不达标，脚本会输出失败原因并返回非零退出码，便于后续接入 GitHub Actions 或其他 CI 流程。

评估指标：

- `source_counts`：结构化来源分布，例如 `aliyun_qwen_structured`、`aliyun_qwen_json`、`local`
- `structured_rate`：非本地规则结构化比例
- `local_fallback_rate`：本地规则 fallback 比例
- `avg_recall`：整体平均召回率
- `avg_precision`：整体平均准确率
- `label_metrics.tasks.recall / precision`：任务抽取召回率和准确率
- `label_metrics.blockers.recall / precision`：阻塞项抽取召回率和准确率
- `label_metrics.plans.recall / precision`：计划项抽取召回率和准确率
- `quality_gate`：质量门禁是否启用、是否通过以及失败原因

完整示例报告见：`reports/eval-example.json`。

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

请不要提交真实 API Key、真实工作日志或本地 eval 最新报告。

建议忽略：

```gitignore
.env
data/logs.json
reports/eval-latest.json
config.json
```

如需提供示例数据，建议创建 `data/logs.example.json`；如需展示 eval 结果，建议提交脱敏后的 `reports/eval-example.json`。

## 后续优化方向

- 增加 `postprocess_structured_log`，进一步修正模型误分类
- 扩展 eval cases 到 30-50 条
- 增加报告生成质量的 faithfulness 检测（周报每一条是否都能在材料中找到依据）
- 使用 LangGraph 管理多步骤 Agent 工作流，让模型自主决定工具调用顺序
- 增加会话记忆，支持「再正式一点 / 第三条展开写」这类多轮修订
- 周报生成改流式输出（SSE）
- SQLite 持久化周报历史与版本
- 把 `scripts/send_weekly_report.py` 接上定时任务，每周自动生成并推送
- 接入 GitHub Actions，让 eval harness 成为 CI 质量门禁
- 封装 MCP tools，让外部 Agent 客户端调用日报和周报能力

## 项目亮点

- 使用 LangChain + Qwen 构建 LLM 调用链路
- 使用 Pydantic structured output 提升结构化稳定性
- 设计模型失败 fallback，增强系统可用性
- **多源证据聚合：日报 + 多仓库 git 提交 + 上周计划，周报不再只依赖手打日报**
- **计划闭环对比：自动核对上周承诺与本周交付，输出已完成 / 未完成 / 新增工作**
- 构建 eval harness，用 recall / precision / fallback rate 评估结构化质量，并支持质量门禁
- 输出 JSON eval 报告，便于追踪 prompt、模型和规则变更前后的质量变化
- 基于 FastAPI 实现完整日报录入、查询、聚合和周报生成流程
