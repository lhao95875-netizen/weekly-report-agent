# 周报 Agent

一个最小可用的周报 Agent：
- 每天记录工作内容
- 自动转成结构化日报
- 按周聚合日报
- 一键生成周报
- 提供简单网页入口

## 启动

```bash
pip install -r requirement.txt
uvicorn app:app --reload
```

打开浏览器访问：`http://127.0.0.1:8000`

## 接口

- `POST /logs` 新增日报
- `GET /logs?date=2026-04-04` 查看某天日报
- `GET /weekly?start=2026-03-30&end=2026-04-05` 查看周数据
- `POST /weekly-report` 生成周报

## Gemini 配置

如果你想接入 Gemini，大模型相关环境变量如下：

- `GEMINI_API_KEY`
- `GEMINI_MODEL`，默认 `gemini-2.0-flash`
- `GEMINI_BASE_URL`，默认 `https://generativelanguage.googleapis.com/v1beta`

Windows `bat` 可以这样写：

```bat
@echo off
set GEMINI_API_KEY=你的key
set GEMINI_MODEL=gemini-2.0-flash
set GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta
uvicorn app:app --reload
```

说明：
- 只设置 `GEMINI_API_KEY` 也可以，其他两个有默认值
- 新增日报时会优先调用 Gemini 做自动拆分
- 如果模型返回格式不对、超时或调用失败，会自动回退到本地规则拆分
- 本地规则现在也支持把一整段日报按 `；`、`。`、编号、短横线等分隔符拆开，不一定非要手动换行
