# BTC 分析师追踪与 Agent 决策面板 📈

> 基于 FastAPI + React + LangGraph 的 BTC 行情分析、分析师观点追踪、预测验证与虚拟交易决策系统

---

# 使用必看

请先安装 Python 与 Node.js 环境。项目默认使用 SQLite 本地数据库，启动后会自动建表并写入示例 BTC 1h 行情；如需切换 PostgreSQL，请配置 `.env` 中的 `DATABASE_URL` 或 `POSTGRES_DSN`。模型 API Key 建议通过环境变量或 `config/model_api.local.json` 管理，避免提交真实密钥。

## 📖 项目简介

**BTC 分析师追踪与 Agent 决策面板**是一个面向 BTC 行情研判和分析师观点验证的全栈应用。系统以本地数据库为事实来源，后端通过 FastAPI 提供 API 服务，前端通过 React + Vite 构建可视化面板，核心 Agent 流程由 LangGraph 编排。

系统围绕“观点录入 → 预测拆分 → 行情跟踪 → 到期验证 → Agent 决策 → 虚拟交易 → 报告生成”形成闭环，适合用于验证分析师观点质量、观察预测改口行为、记录 Agent 决策过程，并通过虚拟账户衡量策略表现。

---

## ✨ 核心特性

| 特性 | 说明 |
|------|------|
| **前后端分离** | 后端 FastAPI，前端 React + Vite + TypeScript |
| **本地事实库** | 默认 SQLite，支持通过配置切换 PostgreSQL |
| **LangGraph 工作流** | 覆盖观点解析、虚拟交易信号、预测验证、每日 BTC 报告 |
| **观点结构化解析** | 优先调用 LLM 结构化解析，失败时自动回退规则版解析 |
| **改口检测** | 同一分析师同一周期在验证前方向反转时，标记旧预测并扣稳定性分 |
| **预测验证** | 根据到期行情进行规则验证，并可生成 LLM 解释与失败归因 |
| **Agent 决策** | 综合行情、指标、待验证预测和持仓状态，输出开多、开空或观望 |
| **虚拟交易** | 记录开仓、平仓、手续费、资金费、已实现/未实现盈亏 |
| **合约账户面板** | 展示钱包余额、账户权益、ROI、最大回撤、权益曲线与当前持仓 |
| **调度任务** | 支持行情同步、预测验证、日报生成、账户快照等后台任务 |
| **节点追踪** | 保存 Agent 运行记录和节点运行明细，便于回放与排查 |

---

## 🏗 系统架构

```text
┌──────────────────────────────────────────────┐
│             React 前端面板 (frontend/)        │
│  - 行情看板  - 观点录入  - Agent 节点追踪      │
│  - 预测验证  - 虚拟交易  - 日报与账户曲线       │
└──────────────────────┬───────────────────────┘
                       │ HTTP API
┌──────────────────────▼───────────────────────┐
│              FastAPI 后端 (backend/app/)      │
│  main.py        API 入口与路由                 │
│  services.py    业务服务与数据聚合             │
│  database.py    建表、种子数据、行情同步        │
│  scheduler.py   APScheduler 调度任务           │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│             LangGraph Agent 工作流            │
│  graphs.py       工作流编排                    │
│  graph_nodes.py  节点实现                      │
│  graph_state.py  状态定义                      │
│  llm_client.py   OpenAI-compatible 模型调用    │
└──────────────┬───────────────┬───────────────┘
               │               │
               ▼               ▼
┌─────────────────────┐ ┌─────────────────────┐
│ SQLite / PostgreSQL │ │ 外部行情与模型服务    │
│ 分析师 / 观点 / 预测 │ │ Binance 行情接口      │
│ 行情 / 指标 / 交易   │ │ OpenAI-compatible LLM │
│ 报告 / 调度 / 账户   │ │                     │
└─────────────────────┘ └─────────────────────┘
```

---

## 📂 目录结构

```text
bit/
├── backend/
│   └── app/
│       ├── main.py              # FastAPI 入口、路由与生命周期
│       ├── database.py          # 数据库连接、建表、种子数据、行情同步
│       ├── services.py          # API 服务层与核心业务逻辑
│       ├── graphs.py            # LangGraph 工作流编排
│       ├── graph_nodes.py       # LangGraph 节点实现
│       ├── graph_state.py       # Agent 状态结构
│       ├── llm_client.py        # 模型调用、JSON 解析与连接测试
│       ├── scheduler.py         # 调度器启动、状态与任务执行
│       ├── schemas.py           # 请求模型定义
│       └── config_loader.py     # 运行时配置加载
├── frontend/
│   ├── src/
│   │   ├── App.tsx              # 前端主页面
│   │   ├── main.tsx             # React 入口
│   │   └── styles.css           # 页面样式
│   ├── package.json             # 前端脚本与依赖
│   └── tsconfig.json            # TypeScript 配置
├── config/
│   ├── model_api.json           # 模型供应商、base_url、模型名、温度等默认配置
│   ├── model_api.local.example.json # 本地密钥覆盖示例
│   └── prompts.json             # LangGraph 多节点 Prompt 配置
├── scripts/
│   ├── smoke_backend.py         # 后端烟测脚本
│   └── migrate_sqlite_to_postgres.py # SQLite 到 PostgreSQL 迁移脚本
├── requirements.txt             # 根依赖入口，指向 backend/requirements.txt
├── .env.example                 # 环境变量示例
├── bit设计.md                   # 原始设计文档
├── bit简化.md                   # 简化设计说明
└── bit提示词.md                 # Prompt 设计资料
```

---

## 📦 环境依赖

### 后端环境

建议使用 **Python 3.10+**。

| 包名 | 用途 |
|------|------|
| `fastapi` | 后端 Web API 框架 |
| `uvicorn` | ASGI 服务启动器 |
| `pydantic` | 请求与配置数据校验 |
| `python-dotenv` | `.env` 环境变量加载 |
| `langgraph` | Agent 工作流编排 |
| `apscheduler` | 后台调度任务 |
| `psycopg[binary]` | PostgreSQL 连接 |
| `httpx` | 外部 HTTP 请求，如行情和模型服务 |

### 前端环境

建议使用 **Node.js 18+**。

| 包名 | 用途 |
|------|------|
| `react` / `react-dom` | 前端 UI 框架 |
| `vite` | 前端开发与构建工具 |
| `typescript` | 类型检查 |
| `lucide-react` | 图标组件 |

---

## ⚙️ 配置说明

### 1. 数据库配置

项目默认使用 SQLite，无需额外配置即可启动。若要使用 PostgreSQL，可复制 `.env.example` 为 `.env`，并设置：

```env
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/bit_agent
```

也可以在 PowerShell 当前终端临时设置：

```powershell
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/bit_agent"
```

### 2. 模型 API 配置

默认配置文件位于：

```text
config/
├── model_api.json
├── model_api.local.example.json
└── prompts.json
```

推荐将真实 API Key 放在环境变量中，或复制 `config/model_api.local.example.json` 为 `config/model_api.local.json` 后填写。本地密钥文件已加入 `.gitignore`，不要把真实密钥提交到仓库。

后端提供配置查看与模型连接测试接口：

```text
GET  /api/config
POST /api/config/model/test
```

### 3. Prompt 配置

`config/prompts.json` 维护 LangGraph 多节点 Prompt，可用于调整观点解析、信号解释、验证归因、日报生成等节点行为。

---

## 🚀 快速开始

### 1. 克隆项目

```powershell
git clone <your-repository-url>
```

进入项目根目录：

```powershell
d:
Set-Location d:\Project\bit
```

### 2. 启动后端

在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001
```

接口文档：

```text
http://127.0.0.1:8001/docs
```

健康检查：

```text
http://127.0.0.1:8001/api/health
```

如果已经进入 `backend` 目录，也可以执行：

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

### 3. 启动前端

在 `frontend` 目录执行：

```powershell
npm install
npm run dev
```

前端地址：

```text
http://127.0.0.1:5173
```

---

## 💬 使用方式

### 录入分析师观点

在前端观点录入区输入自然语言观点，例如：

```text
BTC 短期可能涨到 80000，中期可能回落到 75000。
```

系统会尝试识别分析师、发布时间、方向、目标价、周期和置信度，并拆分生成结构化预测。

### 触发 Agent 决策

观点入库后，可触发 Agent 决策流程。Agent 会读取行情、技术指标、待验证预测和当前持仓，输出：

```text
开多 / 开空 / 观望
```

如果产生交易行为，系统会记录虚拟交易、手续费、资金费、账户快照和节点执行过程。

### 查看验证与回放

预测到期后，可通过验证任务判断预测成功、失败或部分命中，并查看验证解释、失败归因、Agent 运行节点和回放信息。

### 生成每日 BTC 报告

系统支持基于市场摘要、分析师共识和情景推演生成每日 BTC 报告，可在前端或通过 API 触发。

---

## 🛠 主要接口

### 基础与行情

```text
GET  /api
GET  /api/health
GET  /api/dashboard
GET  /api/market?interval=1h&limit=120
GET  /api/market/summary?interval=1h
GET  /api/market/live-price
POST /api/market/sync-real?interval=1h&limit=500&replace=false
POST /api/market/sync-intervals
POST /api/market/sync-history
```

### 分析师、观点与预测

```text
GET  /api/analysts
GET  /api/opinions
POST /api/opinions
GET  /api/predictions
GET  /api/predictions/{prediction_id}/replay
GET  /api/analysts/{analyst_id}/replay
```

### Agent 与节点追踪

```text
GET  /api/agent/runs
POST /api/agent/run
GET  /api/agent/stream-events
GET  /api/agent/stream
GET  /api/agent/runs/{agent_run_id}/nodes
GET  /api/agent/runs/{agent_run_id}/replay
```

### 预测验证与人工确认

```text
POST /api/predictions/verify-due
GET  /api/verification-results
GET  /api/predictions/{prediction_id}/verification
GET  /api/reviews?status=pending
GET  /api/reviews/{review_id}
POST /api/reviews/{review_id}/confirm
POST /api/reviews/{review_id}/reject
POST /api/reviews/{review_id}/resolve
```

### 虚拟账户与交易

```text
GET  /api/account
GET  /api/account/equity-curve?limit=300
POST /api/account/snapshot
GET  /api/account/ai
GET  /api/account/ai/equity-curve?limit=300
POST /api/account/ai/snapshot
GET  /api/trades
```

### 报告

```text
POST /api/reports/daily
GET  /api/reports
```

### 系统设置与调度任务

```text
GET  /api/settings
PUT  /api/settings/{key}
POST /api/settings/reset-defaults
GET  /api/scheduler/status
GET  /api/scheduler/runs
POST /api/scheduler/tasks/{task_name}/run
```

当前支持的调度任务包括：

| 任务名 | 说明 |
|--------|------|
| `market_sync` | 同步 BTC 行情 |
| `verify_due` | 验证到期预测 |
| `daily_report` | 生成每日 BTC 报告 |
| `account_snapshot` | 记录账户快照 |

---

## 🔄 LangGraph 工作流

系统已接入 4 条核心工作流：

| 工作流 | 说明 |
|--------|------|
| **观点录入与解析** | 文本清洗、BTC 相关性判断、分析师识别、发布时间识别、观点摘要、预测拆分、时间标准化、置信度处理、异常检测、人工确认判断、入库 |
| **虚拟交易信号** | 加载行情和待验证预测、规则打分、风险判断、交易决策、信号解释、虚拟交易执行 |
| **预测验证** | 加载到期预测、程序规则验证、LLM 解释、失败归因、保存验证报告 |
| **每日 BTC 报告** | 市场摘要、分析师共识、情景推演、日报生成、保存报告 |

节点运行明细可通过以下接口查看：

```text
GET /api/agent/runs/{agent_run_id}/nodes
```

---

## 📊 数据与行情

### 默认数据

后端启动时会自动初始化数据库，并在无行情数据时生成示例 BTC 1h K 线，便于前端立即展示和测试。

### 同步真实 BTC 行情

后端启动后，可调用：

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8001/api/market/sync-real?market_type=perpetual&interval=1h&limit=500"
```

也可以在项目根目录直接执行：

```powershell
.\.venv\Scripts\python.exe -c "from backend.app.database import connect, clear_demo_data, sync_real_market_data; conn=connect(); clear_demo_data(conn, include_market=True); print(sync_real_market_data(conn, limit=500, replace=True)); conn.close()"
```

该接口支持 `symbol`、`interval`、`limit`、`days`、`replace` 和 `market_type` 参数。默认保存 `BTCUSDT` 永续合约 1h K 线；当 `market_type=perpetual` 时，会尝试写入最新资金费率。

---

## 🧪 数据库迁移与烟测

### 后端烟测

```powershell
.\.venv\Scripts\python.exe scripts\smoke_backend.py
```

### SQLite 迁移到 PostgreSQL

```powershell
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/bit_agent"
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py
```

也可以通过 `SQLITE_DB_PATH` 指定源 SQLite 文件。

---

## 📋 核心数据对象

系统围绕以下对象组织数据：

| 对象 | 说明 |
|------|------|
| **分析师** | 记录分析师身份、观点数量、预测表现和稳定性 |
| **原始观点** | 保存用户录入的自然语言观点 |
| **预测** | 从观点中拆分出的方向、周期、目标价、置信度等结构化信息 |
| **预测版本** | 保存预测被修改或改口前后的版本 |
| **行情与指标** | BTC K 线、资金费率和技术指标 |
| **虚拟交易** | Agent 或分析师策略产生的模拟交易记录 |
| **账户快照** | 账户权益、钱包余额、ROI、回撤和持仓状态 |
| **Agent 运行记录** | 一次 Agent 执行的整体输入、输出和状态 |
| **节点运行记录** | LangGraph 每个节点的输入、输出、耗时和错误 |
| **人工确认项** | 低置信度或异常解析结果的人工审核队列 |
| **验证报告** | 到期预测的验证结果、解释和失败归因 |
| **Agent 报告** | 每日 BTC 报告等生成内容 |

---

## 🔮 后续优化方向

- **实时行情**：在现有 Binance 手动同步基础上补充 WebSocket 流式行情。
- **交易所适配**：扩展更多交易所、现货/合约市场和更多周期数据。
- **字段级人工编辑**：增强人工确认队列，支持预测字段逐项修正。
- **策略评估**：加入更完整的回测指标、风控参数和策略对比。
- **权限与多用户**：增加登录认证、用户隔离和团队协作能力。
- **任务状态推送**：通过 SSE 或 WebSocket 推送调度任务与 Agent 节点进度。

---

## 📄 许可证

本项目用于学习、研究和演示 BTC 分析师追踪与 Agent 决策闭环，不构成任何投资建议。虚拟交易结果仅用于系统验证和策略分析。
