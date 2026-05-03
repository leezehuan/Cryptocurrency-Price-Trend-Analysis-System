# BTC 分析师追踪与 Agent 决策面板

这是根据 `bit设计.md` 渐进升级的 BTC 分析师追踪平台：前后端分离、LangGraph Agent 为核心、PostgreSQL/SQLite 本地数据库为事实来源、虚拟交易作为 Agent 行为结果记录。

## 已实现范围

- **后端 API**：FastAPI 单体服务。
- **本地数据库**：默认 SQLite；配置 `DATABASE_URL` 或 `POSTGRES_DSN` 后使用 PostgreSQL，启动时自动建表并生成示例 BTC 1h 行情。
- **核心表**：分析师、原始观点、预测、预测版本、行情、指标、虚拟交易、Agent 运行记录、Agent 节点运行记录、人工确认项、验证报告、Agent 报告。
- **LangGraph 工作流**：观点录入、虚拟交易信号、预测验证、每日 BTC 报告。
- **观点解析**：LangGraph + LLM 结构化解析，失败时自动回退规则版解析。
- **改口检测**：同一分析师同一周期在验证前方向反转，会标记旧预测为已修改并扣稳定性分。
- **Agent 决策**：读取行情、指标、待验证预测和当前持仓，通过 LangGraph 节点输出开多、开空或观望。
- **虚拟交易**：虚拟交易并入 Agent，记录开仓、平仓、手续费、盈亏。
- **前端页面**：轻量版闭环：行情、观点录入、LangGraph 节点追踪、预测验证、Agent 决策、虚拟交易记录、每日 BTC 报告。

## 目录结构

```text
backend/
  app/
    main.py        FastAPI 入口
    database.py    SQLite 建表、行情种子数据、指标计算
    services.py    API 服务层，调用 LangGraph 工作流和确定性规则
    graphs.py      LangGraph 工作流编排
    graph_nodes.py LangGraph 节点实现
    llm_client.py  OpenAI-compatible 模型调用与 JSON 解析
    schemas.py     请求模型
  requirements.txt
frontend/
  src/
    App.tsx
    main.tsx
    styles.css
  package.json
```

## 启动后端

在项目根目录 `d:\Project\bit` 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001
```

如需使用 PostgreSQL，先复制 `.env.example` 为 `.env` 并设置连接串，或在当前终端设置：

```powershell
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/bit_agent"
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8001
```

也可以进入 `backend` 目录执行：

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
```

接口文档：`http://127.0.0.1:8001/docs`

## 数据库迁移与烟测

后端 smoke test：

```powershell
.\.venv\Scripts\python.exe scripts\smoke_backend.py
```

从默认 SQLite 数据库迁移到 PostgreSQL：

```powershell
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5432/bit_agent"
.\.venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py
```

也可以通过 `SQLITE_DB_PATH` 指定源 SQLite 文件。

## 同步真实 BTC 行情

后端启动后可调用：

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8001/api/market/sync-real?market_type=perpetual&interval=1h&limit=500"
```

也可以直接在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -c "from backend.app.database import connect, clear_demo_data, sync_real_market_data; conn=connect(); clear_demo_data(conn, include_market=True); print(sync_real_market_data(conn, limit=500, replace=True)); conn.close()"
```

该接口支持 `symbol`、`interval`、`limit`、`days`、`replace` 和 `market_type` 参数，默认保存真实 `BTCUSDT` 永续合约 1h K 线；`market_type=perpetual` 时会尝试写入最新资金费率。

多周期行情与指标：

```text
GET /api/market?interval=1h&limit=120
GET /api/market/summary?interval=1h
POST /api/market/sync-real?interval=1h&limit=500&replace=false
POST /api/market/sync-intervals
```

## 系统设置与调度任务

系统设置接口：

```text
GET /api/settings
PUT /api/settings/{key}
POST /api/settings/reset-defaults
```

调度任务接口：

```text
GET /api/scheduler/status
GET /api/scheduler/runs
POST /api/scheduler/tasks/{task_name}/run
```

当前支持的 `task_name` 包括 `market_sync`、`verify_due`、`daily_report`、`account_snapshot`。后台自动调度由 `scheduler.enabled` 等 settings 控制，手动触发接口不依赖开关。

## 合约虚拟账户

账户接口：

```text
GET /api/account
GET /api/account/equity-curve?limit=300
POST /api/account/snapshot
```

虚拟账户按合约口径统计账户权益、钱包余额、ROI、最大回撤、已实现/未实现盈亏、手续费、资金费和当前持仓。Agent 开仓、反手和平仓时会自动生成账户快照，也可通过 `account_snapshot` 调度任务或 `POST /api/account/snapshot` 手动刷新。

## 模型 API 与 Prompt 配置

配置目录：

```text
config/
  model_api.json                 模型供应商、base_url、模型名、温度等默认配置
  prompts.json                   LangGraph 多节点 prompts
  model_api.local.example.json   本地密钥覆盖示例
```

真实 API Key 推荐放在环境变量，或复制 `model_api.local.example.json` 为 `model_api.local.json` 后填写。`model_api.local.json` 已加入 `.gitignore`。

后端配置查看接口：

```text
GET /api/config
```

模型连接测试：

```text
POST /api/config/model/test
```

## LangGraph 工作流

已接入的工作流：

- **观点录入与解析**：文本清洗、BTC 相关性、分析师识别、发布时间识别、观点摘要、预测拆分、时间标准化、置信度处理、异常检测、人工确认判断、入库。
- **虚拟交易信号**：加载行情和待验证预测、规则打分、风险判断、交易决策、信号解释、虚拟交易执行。
- **预测验证**：加载到期预测、程序规则验证、LLM 解释、失败归因、保存验证报告。
- **每日 BTC 报告**：市场摘要、分析师共识、情景推演、日报生成、保存报告。

节点运行明细：

```text
GET /api/agent/runs/{agent_run_id}/nodes
```

回放接口：

```text
GET /api/predictions/{prediction_id}/replay
GET /api/analysts/{analyst_id}/replay
GET /api/agent/runs/{agent_run_id}/replay
```

生成日报：

```text
POST /api/reports/daily
GET /api/reports
```

人工确认队列：

```text
GET /api/reviews?status=pending
GET /api/reviews/{review_id}
POST /api/reviews/{review_id}/confirm
POST /api/reviews/{review_id}/reject
POST /api/reviews/{review_id}/resolve
```

结构化验证结果：

```text
GET /api/verification-results
GET /api/predictions/{prediction_id}/verification
```

## 启动前端

```powershell
npm install
npm run dev
```

执行目录：`frontend`

前端地址：`http://127.0.0.1:5173`

## 试用示例

在前端录入：

```text
BTC 短期可能涨到 80000，中期可能回落到 75000。
```

系统会生成两条预测，并触发一次 Agent 决策。

## 后续替换点

- **行情数据**：已支持 Binance 手动同步和多周期保存，后续可补充 WebSocket 或更完整的交易所适配。
- **人工确认**：后端已支持草稿确认/拒绝，前端已提供详情查看和确认入口，后续可继续增强字段级编辑。
- **验证任务**：已接入 APScheduler 和手动任务接口，后续可增加更细的任务状态推送。
