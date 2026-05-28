# BTC 分析师追踪与趋势分析系统：项目现状说明

> 本文档根据项目代码整理，用于说明系统定位、模块结构、核心数据流和使用口径。

---

# 一、项目定位

项目定位为：

> **BTC 行情监控 + 分析师观点追踪 + 预测验证 + LangGraph Agent 分析与报告系统**

系统采用以事实数据、结构化预测和验证闭环为核心的分析架构，重点围绕以下能力展开：

- **分析师观点管理**：保存分析师原文、来源、发布时间与基础资料。
- **结构化预测生成**：从自然语言观点中抽取方向、周期、目标价、置信度等字段。
- **预测验证闭环**：在验证时间到达后，根据行情数据评估方向、目标价和时间窗口表现。
- **观点变化追踪**：记录预测修改、目标价调整、方向变化与稳定性影响。
- **Agent 方向分析**：结合行情、技术指标、待验证预测和分析师表现生成偏多、偏空或观望结论。
- **报告与回放**：保存 Agent 运行记录、节点明细、验证解释和每日报告。

项目的核心目标是沉淀可验证、可追踪、可解释的 BTC 观点分析链路。

---

# 二、整体架构

项目可以理解为五层：

```text
React 前端
  ↓ HTTP API，默认前缀 /bit
FastAPI 后端
  ↓ 服务层与调度器
LangGraph 工作流
  ↓ 读写事实数据
SQLite / PostgreSQL
  ↓ 外部数据
Binance 行情接口 / OpenAI-compatible LLM
```

## 1. 前端层

路径：`frontend/`

技术栈：

- React 18
- TypeScript
- Vite
- lucide-react

前端主要视图包括：

- **总览**：关键指标、行情摘要、待处理信息。
- **分析师数据**：分析师评分、预测统计、观点记录与回放。
- **预测验证**：待验证预测、验证结果、改口记录与详情回放。
- **Agent 与报告**：Agent 运行记录、节点输出、日报和人工确认。
- **系统设置**：模型连接、调度状态、行情同步和系统参数。

前端默认 API 前缀为：

```text
/bit
```

也可以通过 `VITE_API_BASE_URL` 覆盖。

## 2. API 层

路径：`backend/app/main.py`

FastAPI 负责：

- 提供行情、分析师、观点、预测、Agent、报告、设置等接口。
- 在服务启动时初始化数据库。
- 启动和关闭 APScheduler 调度器。
- 兼容 `/bit` 前缀访问。
- 提供 SSE 流式接口输出 Agent 节点事件。

## 3. 服务层

路径：`backend/app/services.py`

服务层负责核心业务逻辑：

- 仪表盘聚合。
- 行情摘要和指标读取。
- 分析师列表与评分统计。
- 观点创建、预测入库和人工确认。
- 预测修改、删除和验证。
- Agent 分析触发和运行记录查询。
- 每日报告生成。
- 调度任务执行记录。

## 4. LangGraph 工作流层

路径：

- `backend/app/graphs.py`
- `backend/app/graph_nodes.py`
- `backend/app/graph_state.py`

当前项目包含 4 条核心工作流：

| 工作流 | 作用 |
|---|---|
| `opinion_ingestion` | 清洗观点、识别 BTC 相关性、识别分析师、抽取预测、标准化时间、异常检测、入库或转人工确认 |
| `agent_analysis` | 加载行情和待验证预测，生成方向共识、风险提示、分析解释并保存运行记录 |
| `prediction_verification` | 加载到期预测，规则验证，生成解释、失败归因和验证报告 |
| `daily_report` | 汇总行情、分析师共识、情景分析和预测表现，保存每日报告 |

工作流优先使用 LangGraph；如果执行环境未提供 LangGraph，系统会按节点顺序执行相同流程。

## 5. 数据层

路径：`backend/app/database.py`

数据库支持：

- SQLite：默认本地数据库，文件位于 `backend/data/bit_agent.sqlite3`
- PostgreSQL：通过 `DATABASE_URL` 或 `POSTGRES_DSN` 配置

数据层负责：

- 初始化表结构。
- 写入默认设置。
- 同步 BTC 行情。
- 计算基础技术指标。
- 为 PostgreSQL 提供少量 SQL 兼容转换。

---

# 三、核心数据对象

项目中的主要数据表如下：

| 表名 | 作用 |
|---|---|
| `analysts` | 分析师基础信息、方向准确率、目标价命中率、稳定性、分周期表现等 |
| `raw_opinions` | 用户录入的原始分析师观点 |
| `predictions` | 从观点中拆分出的结构化预测 |
| `prediction_versions` | 预测改动、目标价调整、方向变化等版本记录 |
| `market_data` | BTC K 线、成交量、资金费率等行情数据 |
| `indicators` | MA、EMA、RSI、MACD、ATR、布林带等技术指标 |
| `agent_runs` | Agent 分析运行的整体输入、输出、结论和风险 |
| `agent_node_runs` | 每个 LangGraph 节点的输入、输出、状态、耗时和错误 |
| `human_review_items` | 需要人工确认的异常或低置信度解析结果 |
| `opinion_review_drafts` | 人工确认前的观点解析草稿 |
| `verification_results` | 预测验证的量化结果和最终质量标签 |
| `verification_reports` | 预测验证解释、失败原因和可展示报告 |
| `agent_reports` | 每日 BTC 报告等 Agent 生成内容 |
| `settings` | 系统配置项 |
| `scheduled_task_runs` | 后台调度任务执行记录 |

这些数据对象共同组成了“观点录入 → 结构化预测 → 到期验证 → Agent 分析 → 回放与报告”的事实库。

---

# 四、核心业务闭环

## 1. 行情同步

系统支持同步 BTCUSDT 多周期行情。

默认周期包括：

- `1m`
- `5m`
- `15m`
- `1h`
- `4h`
- `1d`

行情写入 `market_data`，指标写入 `indicators`。

相关接口：

```text
GET  /api/market
GET  /api/market/summary
GET  /api/market/live-price
POST /api/market/sync-real
POST /api/market/sync-intervals
POST /api/market/sync-history
```

## 2. 观点录入与预测生成

用户提交分析师观点后，系统会运行 `opinion_ingestion` 工作流：

```text
原始观点
  ↓
文本清洗
  ↓
BTC 相关性判断
  ↓
分析师与发布时间识别
  ↓
观点摘要
  ↓
预测抽取
  ↓
时间与置信度标准化
  ↓
异常检测
  ↓
直接入库或进入人工确认
```

相关接口：

```text
POST /api/opinions
GET  /api/opinions
GET  /api/analysts
GET  /api/analysts/{analyst_id}/replay
```

## 3. 预测验证

每条预测都包含 `verification_time`。验证任务触发后，系统会运行 `prediction_verification` 工作流，评估：

- 方向是否正确。
- 目标价是否命中或接近。
- 时间窗口是否匹配。
- 观点变化对结果的影响。
- 最终质量标签与评分结果。

相关接口：

```text
GET  /api/predictions
PUT  /api/predictions/{prediction_id}
DELETE /api/predictions/{prediction_id}
POST /api/predictions/verify-due
GET  /api/predictions/{prediction_id}/verification
GET  /api/predictions/{prediction_id}/replay
GET  /api/verification-results
```

## 4. Agent 分析

`agent_analysis` 工作流聚焦方向分析，流程如下：

```text
加载行情与待验证预测
  ↓
规则共识评分
  ↓
风险规则判断
  ↓
方向决策规则
  ↓
LLM 或规则解释
  ↓
展示文本中文化
  ↓
保存 Agent 运行
  ↓
返回最终分析结果
```

Agent 输出重点包括：

- 市场摘要。
- 分析师观点共识。
- 偏多 / 偏空 / 观望。
- 风险提示。
- 解释说明。
- 重点关注预测。

相关接口：

```text
POST /api/agent/run
GET  /api/agent/runs
GET  /api/agent/runs/{agent_run_id}/nodes
GET  /api/agent/runs/{agent_run_id}/replay
GET  /api/agent/stream-events
GET  /api/agent/stream
```

## 5. 人工确认

当观点解析存在不确定性时，系统会进入人工确认队列。

典型场景包括：

- 分析师身份不明确。
- 发布时间缺失或难以确定。
- 方向、周期、目标价信息不足。
- 预测之间存在明显冲突。
- 目标价与当前价格偏离异常。

相关接口：

```text
GET  /api/reviews
GET  /api/reviews/{review_id}
POST /api/reviews/{review_id}/confirm
POST /api/reviews/{review_id}/reject
POST /api/reviews/{review_id}/resolve
```

## 6. 每日报告

系统支持基于行情摘要、分析师共识、情景推演和预测表现生成日报。

相关接口：

```text
POST /api/reports/daily
GET  /api/reports
```

---

# 五、调度任务设计

调度器使用 APScheduler。

配置项来自 `settings` 表：

| 配置项 | 说明 |
|---|---|
| `scheduler.enabled` | 是否启用常规定时任务 |
| `scheduler.market_sync_minutes` | 行情同步间隔 |
| `scheduler.verify_due_minutes` | 到期验证失败后的重试间隔 |
| `scheduler.daily_report_hour` | 每日报告 UTC 小时 |

项目中的调度任务包括：

| 任务 | 触发方式 | 说明 |
|---|---|---|
| `market_sync` | interval | 按配置间隔同步行情 |
| `daily_report` | cron | 每天固定 UTC 小时生成报告 |
| `verify_due` | date | 调度到下一条待验证预测的 `verification_time` |

到期预测验证流程如下：

```text
查询下一条 pending 预测的最早 verification_time
  ↓
创建一次性 date 任务
  ↓
到点验证所有已到期预测
  ↓
验证完成后重新安排下一次 date 任务
```

这种设计可以让验证调度与预测到期时间保持一致，并在预测新增、修改和删除后及时刷新下一次触发时间。

---

# 六、配置与 Prompt

模型配置文件位于：

```text
config/model_api.json
config/model_api.local.example.json
```

推荐将真实 API Key 放在：

- 环境变量
- `config/model_api.local.json`

Prompt 配置位于：

```text
config/prompts.json
```

Prompt 主要覆盖以下能力：

- 观点录入与解析。
- 观点变化检测。
- 预测验证解释。
- 分析师评分说明。
- Agent 方向分析。
- 每日报告生成。
- 展示文本中文化。

---

# 七、API 概览

## 基础与行情

```text
GET  /api
GET  /api/health
GET  /api/dashboard
GET  /api/market
GET  /api/market/summary
GET  /api/market/live-price
POST /api/market/sync-real
POST /api/market/sync-intervals
POST /api/market/sync-history
```

## 配置、设置与调度

```text
GET  /api/config
POST /api/config/model/test
GET  /api/settings
PUT  /api/settings/{key}
POST /api/settings/reset-defaults
GET  /api/scheduler/status
GET  /api/scheduler/runs
POST /api/scheduler/tasks/{task_name}/run
```

## 分析师、观点与预测

```text
GET  /api/analysts
GET  /api/analysts/{analyst_id}/replay
POST /api/opinions
GET  /api/opinions
GET  /api/predictions
PUT  /api/predictions/{prediction_id}
DELETE /api/predictions/{prediction_id}
GET  /api/predictions/{prediction_id}/replay
POST /api/predictions/verify-due
```

## Agent、验证、人工确认与报告

```text
POST /api/agent/run
GET  /api/agent/runs
GET  /api/agent/stream-events
GET  /api/agent/stream
GET  /api/agent/runs/{agent_run_id}/nodes
GET  /api/agent/runs/{agent_run_id}/replay
GET  /api/verification-results
GET  /api/predictions/{prediction_id}/verification
GET  /api/reviews
GET  /api/reviews/{review_id}
POST /api/reviews/{review_id}/confirm
POST /api/reviews/{review_id}/reject
POST /api/reviews/{review_id}/resolve
POST /api/reports/daily
GET  /api/reports
```

---

# 八、前端页面理解

## 1. 总览

用于快速查看：

- 最新 BTC 价格。
- 市场摘要。
- 待验证预测。
- 最新 Agent 分析。
- 系统运行状态。

## 2. 分析师数据

用于查看：

- 分析师评分。
- 方向准确率。
- 目标价命中率。
- 稳定性评分。
- 分周期表现。
- 最近观点和回放。

## 3. 预测验证

用于查看：

- 待验证预测。
- 已验证预测分组。
- 成功、失败、部分正确等结果。
- 预测修改和目标价变化。
- 验证详情与回放。

## 4. Agent 与报告

用于查看：

- Agent 运行记录。
- Agent 节点输出。
- SSE 实时输出。
- 每日报告。
- 人工确认队列。

## 5. 系统设置

用于管理：

- 模型连接测试。
- 调度状态。
- 行情同步。
- 默认周期和验证参数。
- 系统设置重置。

---

# 九、开发重点

项目后续可以围绕“更稳定、更可解释、更易用”持续增强。

## 1. 文档与口径统一

- 统一 README、`bit简化.md`、前端页面和后端接口说明。
- 保持项目定位、页面命名和接口分类一致。
- 为新成员提供更清晰的阅读入口。

## 2. 预测验证体验

- 优化验证详情页的信息层级。
- 增强预测修改记录展示。
- 增加失败归因筛选和统计。

## 3. Agent 回放体验

- 更清晰地展示每个节点输入、输出、错误和耗时。
- 将 Agent 分析结论与具体预测、行情指标建立更强关联。
- 增强 SSE 运行过程展示。

## 4. 分析师画像

- 按周期、方向、置信度维度生成更细画像。
- 增加近期表现和历史表现对比。
- 提供更直观的改口率、目标价偏差和稳定性解释。

## 5. 部署与数据迁移

- 完善 SQLite 到 PostgreSQL 的迁移说明。
- 补充生产部署配置。
- 增加备份、恢复和数据清理策略。

---

# 十、项目总结

项目可以概括为：

> **一个以 LangGraph 为核心、以本地数据库为事实来源、以预测验证为闭环的 BTC 分析师追踪与趋势分析系统。**

核心链路如下：

```text
行情同步
  ↓
观点录入
  ↓
Agent 解析
  ↓
预测入库
  ↓
到期验证
  ↓
分析师评分更新
  ↓
Agent 分析与报告
  ↓
节点回放和人工复核
```

产品形态可以理解为：

> **BTC 分析师观点追踪、预测验证与 Agent 趋势分析面板**
