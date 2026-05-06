import { FormEvent, useEffect, useMemo, useState } from 'react';
import { Activity, Bot, BrainCircuit, CheckCircle2, Clock3, LineChart, RefreshCw, Send, ShieldAlert, TrendingDown, TrendingUp, WalletCards } from 'lucide-react';

// 后端市场摘要接口返回的数据结构，用于顶部行情卡片和走势图。
type MarketSummary = {
  latest_price: number;
  symbol?: string;
  market_type?: string;
  interval?: string;
  open_time: string;
  change_24h: number;
  trend: string;
  trend_label: string;
  volatility: number;
  support: number;
  resistance: number;
  rsi_14?: number;
  macd?: number;
  atr_14?: number;
  funding_rate?: number;
  close_series: number[];
};

type LivePrice = {
  symbol: string;
  market_type: string;
  price: number;
  funding_rate?: number | null;
  source: string;
  fetched_at: string;
  open_time?: string;
  error?: string;
};

// 行情 K 线及技术指标行，用于市场表格和图表。
type MarketRow = {
  id: number;
  open_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  funding_rate?: number;
  ma_20?: number;
  ema_20?: number;
  rsi_14?: number;
  macd?: number;
  atr_14?: number;
  bb_upper?: number;
  bb_lower?: number;
};

// Agent 单次运行摘要，用于运行历史和决策展示。
type AgentRun = {
  id: number;
  trigger: string;
  market_summary: string;
  opinion_summary: string;
  decision: string;
  risk: string;
  should_execute: number;
  created_at: string;
  output?: {
    decision_label?: string;
    trade_event?: string;
    signal?: {
      bull_score: number;
      bear_score: number;
      bull_count: number;
      bear_count: number;
    };
  };
};

// SSE 实时流事件，用于展示 Agent 节点执行过程。
type AgentStreamEvent = {
  id: number;
  agent_run_id?: number | null;
  graph_name?: string | null;
  node_name?: string | null;
  status?: string | null;
  message: string;
  created_at?: string | null;
  output?: Record<string, unknown>;
  type?: string;
};

// 人工复核项结构，用于展示待确认的观点解析草稿。
type HumanReview = {
  id: number;
  status: string;
  suggested_question?: string | null;
  created_at: string;
  data?: Record<string, unknown>;
  draft?: {
    input_payload?: Record<string, unknown>;
    parsed_predictions?: Record<string, unknown>[];
  };
  draft_meta?: {
    analyst_name?: string;
    source_url?: string | null;
    published_at?: string | null;
    current_price?: number;
  };
};

// 预测验证结果结构，用于预测详情和验证列表。
type VerificationResult = {
  id: number;
  prediction_id: number;
  summary?: string;
  analyst_name?: string;
  actual_direction: string;
  direction_score: number;
  target_hit: number;
  target_score: number;
  time_score: number;
  modified_penalty: number;
  final_score: number;
  price_change_pct?: number | null;
  closest_price?: number | null;
  target_distance_pct?: number | null;
  quality_label?: string | null;
  status: string;
  highest_price: number;
  lowest_price: number;
  latest_price: number;
  created_at: string;
  data?: Record<string, unknown>;
  report?: VerificationReport;
};

type VerificationReport = {
  id?: number;
  prediction_id?: number;
  plain_language_summary?: string;
  failure_reason?: string | null;
  data?: Record<string, unknown>;
};

// 系统设置项结构，对应后端 settings 表。
type SettingItem = {
  key: string;
  value: string;
  value_type: string;
  description?: string;
  parsed_value: unknown;
};

type SchedulerStatus = {
  available: boolean;
  running: boolean;
  jobs: { id: string; name: string; next_run_time?: string | null }[];
  reason?: string;
};

type ReportScenario = {
  scenario?: string;
  description?: string;
  trigger_conditions?: string[];
  invalid_conditions?: string[];
  risk_factors?: string[];
  range_low?: number | null;
  range_high?: number | null;
};

type ReportAccountSnapshot = {
  equity?: number | null;
  roi?: number | null;
  drawdown?: number | null;
  max_drawdown?: number | null;
  open_position_count?: number | null;
  unrealized_pnl?: number | null;
};

// 每日报告结构，用于报告列表和详情展示。
type AgentReport = {
  id: number;
  title: string;
  report_type: string;
  created_at: string;
  data?: {
    executive_summary?: string;
    market_status?: string;
    analyst_consensus?: string;
    key_levels?: { support?: number | null; resistance?: number | null };
    scenarios?: ReportScenario[] | Record<string, ReportScenario>;
    recent_prediction_review?: string;
    prediction_change_review?: string;
    active_prediction_count?: number;
    recent_verification_count?: number;
    prediction_change_count?: number;
    account_snapshot?: ReportAccountSnapshot;
    risk_warnings?: string[];
    disclaimer?: string;
  };
};

// 分析师评分与账户摘要结构，用于排行榜和分析师详情。
type Analyst = {
  id: number;
  name: string;
  total_score: number;
  direction_win_rate: number;
  target_hit_rate: number;
  stability_score: number;
  virtual_roi: number;
  hard_win_rate?: number;
  weighted_win_rate?: number;
  direction_accuracy?: number;
  target_accuracy?: number;
  modification_rate?: number;
  intraday_win_rate?: number;
  short_win_rate?: number;
  medium_win_rate?: number;
  long_win_rate?: number;
  average_prediction_score?: number;
  latest_opinion?: string;
  prediction_count: number;
  pending_count: number;
  verified_count: number;
  account?: VirtualAccountSummary;
  account_equity?: number;
  account_roi?: number;
  account_unrealized_pnl?: number;
  open_position_count?: number;
};

// 单条预测记录结构，包含最新验证和改口信息。
type Prediction = {
  id: number;
  analyst_name: string;
  direction: string;
  target_price?: number | null;
  horizon: string;
  current_price: number;
  verification_time: string;
  status: string;
  confidence: string;
  summary: string;
  created_at: string;
  latest_final_score?: number | null;
  latest_quality_label?: string | null;
  latest_direction_score?: number | null;
  latest_target_score?: number | null;
  latest_target_distance_pct?: number | null;
  latest_price_change_pct?: number | null;
  latest_change_type?: string | null;
  latest_change_severity?: number | null;
  latest_change_reason?: string | null;
  latest_old_target_price?: number | null;
  latest_new_target_price?: number | null;
};

// 前端编辑预测时使用的表单状态。
type PredictionEditForm = {
  direction: string;
  horizon: string;
  target_price: string;
  verification_time: string;
  status: string;
  confidence: string;
  summary: string;
};

// 虚拟合约交易记录结构。
type Trade = {
  id: number;
  prediction_id?: number | null;
  account_type?: string | null;
  analyst_name?: string;
  prediction_summary?: string;
  action: string;
  side: string;
  size: number;
  entry_price: number;
  exit_price?: number | null;
  mark_price?: number | null;
  notional_usdt?: number | null;
  leverage?: number | null;
  margin?: number | null;
  fee?: number | null;
  funding_fee?: number | null;
  realized_pnl?: number | null;
  unrealized_pnl?: number | null;
  pnl: number;
  status: string;
  reason: string;
  opened_at: string;
  closed_at?: string | null;
};

// AI 聚合账户规则信号结构。
type AiTradeSignal = {
  decision?: string;
  direction?: string;
  should_execute?: boolean;
  confidence?: string;
  confidence_score?: number;
  bull_score?: number;
  bear_score?: number;
  difference?: number;
  threshold?: number;
  bull_count?: number;
  bear_count?: number;
  position_notional?: number;
  risk_notes?: string[];
  supporting_predictions?: {
    id?: number;
    analyst_id?: number | null;
    analyst_name?: string | null;
    direction?: string;
    target_price?: number | null;
    horizon?: string;
    confidence?: string;
    summary?: string;
    weight?: number;
  }[];
};

// 虚拟账户权益摘要结构，兼容 AI 聚合账户和分析师账户。
type VirtualAccountSummary = {
  analyst_id?: number | null;
  analyst_name?: string | null;
  account_type?: string;
  account_count?: number;
  snapshot_time: string;
  symbol: string;
  market_type: string;
  interval: string;
  initial_balance: number;
  wallet_balance: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  fee_paid: number;
  funding_fee: number;
  roi: number;
  drawdown: number;
  max_drawdown: number;
  max_equity: number;
  mark_price: number;
  open_position?: Trade | null;
  open_positions?: Trade[];
  analyst_accounts?: VirtualAccountSummary[];
  signal?: AiTradeSignal;
};

type EquityCurvePoint = {
  id?: number | null;
  snapshot_time: string;
  wallet_balance: number;
  equity: number;
  realized_pnl: number;
  unrealized_pnl: number;
  fee_paid: number;
  funding_fee: number;
  roi: number;
  drawdown: number;
  max_equity: number;
  mark_price: number;
  position_side?: string | null;
  position_size: number;
  notional_usdt: number;
  margin: number;
  leverage: number;
};

type Dashboard = {
  market: MarketSummary;
  pending_prediction_count: number;
  due_prediction_count: number;
  latest_agent_run?: AgentRun | null;
  open_trade?: Trade | null;
  closed_pnl: number;
  account?: VirtualAccountSummary;
  top_analysts: Analyst[];
};

type OpinionResponse = {
  predictions: Prediction[];
  agent_run?: AgentRun | null;
  needs_user_confirmation?: boolean;
  review_item_id?: number | null;
};

type TargetChangeRecord = {
  latest_old_target_price?: number | null;
  latest_new_target_price?: number | null;
  old_target_price?: number | null;
  new_target_price?: number | null;
};

type PredictionVersion = {
  id: number;
  prediction_id: number;
  new_prediction_id: number;
  old_direction: string;
  old_target_price?: number | null;
  old_horizon?: string | null;
  old_confidence?: string | null;
  new_direction?: string | null;
  new_target_price?: number | null;
  new_horizon?: string | null;
  new_confidence?: string | null;
  change_type?: string;
  change_severity?: number;
  reason: string;
  created_at: string;
  data?: Record<string, unknown>;
};

type PredictionReplay = {
  prediction?: Prediction;
  raw_opinion?: Record<string, unknown>;
  analyst?: Record<string, unknown>;
  review?: HumanReview | null;
  versions?: PredictionVersion[];
  verification_result?: VerificationResult;
  verification_report?: VerificationReport;
  trades?: Trade[];
  agent_runs?: AgentRun[];
};

type AgentRunReplay = {
  agent_run?: AgentRun;
  nodes?: {
    id: number;
    graph_name: string;
    node_name: string;
    status: string;
    input?: Record<string, unknown>;
    output?: Record<string, unknown>;
    error_message?: string | null;
  }[];
  trades?: Trade[];
  focus_predictions?: Prediction[];
};

// 前端视图枚举，对应顶部导航标签。
type AppView = 'overview' | 'analysts' | 'accounts' | 'predictions' | 'agent' | 'settings';

// 默认 API 前缀为 /bit，可通过 Vite 环境变量覆盖。
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/bit';
const MARKET_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'];
const APP_VIEWS: { id: AppView; label: string; description: string }[] = [
  { id: 'overview', label: '总览', description: '关键指标与今日待办' },
  { id: 'analysts', label: '分析师数据', description: '评分、账户与预测数据' },
  { id: 'accounts', label: '账户', description: 'AI 聚合账户与交易员账户' },
  { id: 'predictions', label: '预测验证', description: '预测、验证与回放' },
  { id: 'agent', label: 'Agent 与报告', description: '运行记录、日报与人工确认' },
  { id: 'settings', label: '系统设置', description: '调度任务与配置项' }
];

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  // 统一封装 JSON 请求和错误处理，所有后端接口都通过这里调用。
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  });
  if (!response.ok) {
    throw new Error(`${path} ${response.status}: ${await response.text()}`);
  }
  return response.json() as Promise<T>;
}

function formatNumber(value?: number | null, digits = 2): string {
  // 使用中文本地化数字格式展示价格、收益率和统计值。
  if (value === null || value === undefined || Number.isNaN(value)) return '-';
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value);
}

function formatDate(value?: string): string {
  if (!value) return '-';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(value));
}

function toDatetimeLocalValue(value?: string): string {
  // 将 ISO 时间转换成 datetime-local 输入框需要的本地时间格式。
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const timezoneOffsetMs = date.getTimezoneOffset() * 60 * 1000;
  return new Date(date.getTime() - timezoneOffsetMs).toISOString().slice(0, 16);
}

function directionText(value: string): string {
  return { bullish: '看涨', bearish: '看跌', sideways: '震荡' }[value] || value;
}

function horizonText(value: string): string {
  return { intraday: '日内', short: '短期', medium: '中期', long: '长期' }[value] || value;
}

function statusText(value: string): string {
  return { pending: '待验证', success: '成功', failed: '失败', modified: '已改口' }[value] || value;
}

function qualityText(value?: string | null): string {
  return {
    high_quality_success: '高质量成功',
    basic_success: '基本成功',
    partial: '部分正确',
    failed: '失败',
    unknown: '未知'
  }[value || ''] || value || '-';
}

function settingValueText(value: unknown): string {
  // 将不同类型的设置值转换成人类可读文本。
  if (value === true) return '是';
  if (value === false) return '否';
  if (Array.isArray(value)) return value.join('、');
  if (value && typeof value === 'object') return JSON.stringify(value);
  if (value === null || value === undefined || value === '') return '-';
  return String(value);
}

function streamEventTitle(event: AgentStreamEvent): string {
  if (event.type === 'local') return '界面操作';
  if (event.type === 'error') return '连接异常';
  if (event.type === 'heartbeat') return '等待中';
  return `${event.graph_name || 'agent'} · ${event.node_name || 'output'}`;
}

function reportScenarios(value?: ReportScenario[] | Record<string, ReportScenario>): ReportScenario[] {
  if (!value || typeof value !== 'object') return [];
  return Array.isArray(value) ? value : Object.values(value);
}

function isVerifiedPrediction(prediction: Prediction): boolean {
  return ['success', 'failed'].includes(prediction.status);
}

function predictionTimeValue(prediction: Prediction): number {
  const value = new Date(prediction.verification_time).getTime();
  return Number.isNaN(value) ? Number.MAX_SAFE_INTEGER : value;
}

function changeTypeText(value?: string | null, record?: TargetChangeRecord): string {
  if (value === 'target_price_added_or_removed' && record) {
    const oldTarget = record.latest_old_target_price !== undefined ? record.latest_old_target_price : record.old_target_price;
    const newTarget = record.latest_new_target_price !== undefined ? record.latest_new_target_price : record.new_target_price;
    if (oldTarget == null && newTarget != null) return '目标价新增';
    if (oldTarget != null && newTarget == null) return '目标价移除';
  }
  return {
    direction_reversal: '方向反转',
    target_price_shift: '目标价大幅变化',
    target_price_added: '目标价新增',
    target_price_removed: '目标价移除',
    target_price_added_or_removed: '目标价新增/移除',
    horizon_shift: '周期变化',
    confidence_shift: '置信度变化',
    multi_change: '多项变化'
  }[value || ''] || value || '-';
}

function decisionText(value?: string): string {
  return { open_long: '开多', open_short: '开空', observe: '观望' }[value || ''] || value || '-';
}

function tradeStatusText(value?: string): string {
  return { open: '持仓中', closed: '已平仓' }[value || ''] || value || '-';
}

function tradeSideText(value?: string): string {
  return { long: '多单', short: '空单' }[value || ''] || value || '-';
}

function Sparkline({ values }: { values: number[] }) {
  // 计算迷你走势图折线点位，自动按当前序列最大最小值缩放。
  const points = useMemo(() => {
    if (!values.length) return '';
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return values
      .map((value, index) => {
        const x = (index / Math.max(values.length - 1, 1)) * 100;
        const y = 42 - ((value - min) / span) * 34;
        return `${x},${y}`;
      })
      .join(' ');
  }, [values]);

  return (
    <svg className="sparkline" viewBox="0 0 100 48" preserveAspectRatio="none">
      <polyline points={points} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function MetricCard({ title, value, desc, icon }: { title: string; value: string; desc: string; icon: JSX.Element }) {
  return (
    <section className="metric-card">
      <div className="metric-icon">{icon}</div>
      <div>
        <p>{title}</p>
        <strong>{value}</strong>
        <span>{desc}</span>
      </div>
    </section>
  );
}

export function App() {
  // 主页面状态集中存放各业务模块数据，刷新时批量从后端加载。
  const [activeView, setActiveView] = useState<AppView>('overview');
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [livePrice, setLivePrice] = useState<LivePrice | null>(null);
  const [marketRows, setMarketRows] = useState<MarketRow[]>([]);
  const [analysts, setAnalysts] = useState<Analyst[]>([]);
  const [predictions, setPredictions] = useState<Prediction[]>([]);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [reviews, setReviews] = useState<HumanReview[]>([]);
  const [reports, setReports] = useState<AgentReport[]>([]);
  const [verificationResults, setVerificationResults] = useState<VerificationResult[]>([]);
  const [settings, setSettings] = useState<SettingItem[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [account, setAccount] = useState<VirtualAccountSummary | null>(null);
  const [equityCurve, setEquityCurve] = useState<EquityCurvePoint[]>([]);
  const [marketInterval, setMarketInterval] = useState('1h');
  const [selectedAnalystId, setSelectedAnalystId] = useState<number | null>(null);
  const [selectedAnalystAccount, setSelectedAnalystAccount] = useState<VirtualAccountSummary | null>(null);
  const [selectedAnalystCurve, setSelectedAnalystCurve] = useState<EquityCurvePoint[]>([]);
  const [selectedAnalystTrades, setSelectedAnalystTrades] = useState<Trade[]>([]);
  const [selectedReview, setSelectedReview] = useState<HumanReview | null>(null);
  const [immediateReview, setImmediateReview] = useState<HumanReview | null>(null);
  const [selectedVerification, setSelectedVerification] = useState<VerificationResult | null>(null);
  const [editingPrediction, setEditingPrediction] = useState<Prediction | null>(null);
  const [predictionEditForm, setPredictionEditForm] = useState<PredictionEditForm | null>(null);
  const [predictionReplay, setPredictionReplay] = useState<PredictionReplay | null>(null);
  const [agentReplay, setAgentReplay] = useState<AgentRunReplay | null>(null);
  const [analystName, setAnalystName] = useState('');
  const [content, setContent] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [showProjectNotice, setShowProjectNotice] = useState(true);
  const [streamEvents, setStreamEvents] = useState<AgentStreamEvent[]>([]);
  const [streamConnected, setStreamConnected] = useState(false);
  const [debugPanelCollapsed, setDebugPanelCollapsed] = useState(true);
  const [verifiedPredictionsExpanded, setVerifiedPredictionsExpanded] = useState(false);

  const appendStreamEvent = (event: AgentStreamEvent) => {
    // 追加 SSE 事件并按 id 去重，只保留最近 80 条用于调试面板展示。
    setStreamEvents((current) => {
      const exists = current.some((item) => item.id === event.id && item.type !== 'local');
      if (exists) return current;
      return [event, ...current].slice(0, 80);
    });
  };

  const loadMarketData = async (interval = marketInterval) => {
    // 按当前周期刷新行情摘要和 K 线列表。
    const [summaryData, rowsData] = await Promise.all([
      requestJson<MarketSummary>(`/api/market/summary?interval=${interval}`),
      requestJson<MarketRow[]>(`/api/market?interval=${interval}&limit=120`)
    ]);
    setDashboard((current) => current ? { ...current, market: summaryData } : current);
    setMarketRows(rowsData);
  };

  const loadLivePrice = async () => {
    // 高频刷新实时价格；失败时保留当前值并标记错误来源。
    try {
      const result = await requestJson<LivePrice>('/api/market/live-price?symbol=BTCUSDT&market_type=perpetual');
      setLivePrice(result);
    } catch (error) {
      setLivePrice((current) => current ? { ...current, source: 'unavailable', error: error instanceof Error ? error.message : '实时价格获取失败' } : current);
    }
  };

  const loadAnalystAccountDetail = async (analystId: number) => {
    // 加载单个分析师的账户、权益曲线和交易明细。
    setSelectedAnalystId(analystId);
    setLoading(true);
    try {
      const [accountData, curveData, tradeData] = await Promise.all([
        requestJson<VirtualAccountSummary>(`/api/account?analyst_id=${analystId}`),
        requestJson<EquityCurvePoint[]>(`/api/account/equity-curve?limit=300&analyst_id=${analystId}`),
        requestJson<Trade[]>(`/api/trades?analyst_id=${analystId}`)
      ]);
      setSelectedAnalystAccount(accountData);
      setSelectedAnalystCurve(curveData);
      setSelectedAnalystTrades(tradeData);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '加载分析师账户失败');
    } finally {
      setLoading(false);
    }
  };

  const loadAll = async () => {
    // 首屏和手动刷新使用的批量加载入口。
    setLoading(true);
    setMessage('');
    try {
      const [
        dashboardData,
        marketData,
        analystData,
        predictionData,
        tradeData,
        runData,
        reviewData,
        reportData,
        verificationData,
        settingsData,
        schedulerData,
        accountData,
        equityCurveData
      ] = await Promise.all([
        requestJson<Dashboard>('/api/dashboard'),
        requestJson<MarketRow[]>(`/api/market?interval=${marketInterval}&limit=120`),
        requestJson<Analyst[]>('/api/analysts'),
        requestJson<Prediction[]>('/api/predictions'),
        requestJson<Trade[]>('/api/trades?account_type=ai'),
        requestJson<AgentRun[]>('/api/agent/runs'),
        requestJson<HumanReview[]>('/api/reviews?status=pending'),
        requestJson<AgentReport[]>('/api/reports'),
        requestJson<VerificationResult[]>('/api/verification-results'),
        requestJson<{ items: SettingItem[] }>('/api/settings'),
        requestJson<SchedulerStatus>('/api/scheduler/status'),
        requestJson<VirtualAccountSummary>('/api/account/ai'),
        requestJson<EquityCurvePoint[]>('/api/account/ai/equity-curve?limit=300')
      ]);
      setDashboard(dashboardData);
      setMarketRows(marketData);
      setAnalysts(analystData);
      setPredictions(predictionData);
      setVerifiedPredictionsExpanded(false);
      setTrades(tradeData);
      setAgentRuns(runData);
      setReviews(reviewData);
      setReports(reportData);
      setVerificationResults(verificationData);
      setSettings(settingsData.items);
      setScheduler(schedulerData);
      setAccount(accountData);
      setEquityCurve(equityCurveData);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // 首次进入页面时加载所有数据和实时价格。
    void loadAll();
    void loadLivePrice();
  }, []);

  useEffect(() => {
    // 切换行情周期后，只刷新行情相关数据。
    void loadMarketData(marketInterval).catch((error) => {
      setMessage(error instanceof Error ? error.message : '行情周期加载失败');
    });
  }, [marketInterval]);

  useEffect(() => {
    // 每秒刷新一次实时价格。
    const timer = window.setInterval(() => {
      void loadLivePrice();
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    // 通过 SSE 订阅 Agent 节点输出和心跳事件。
    const source = new EventSource(`${API_BASE}/api/agent/stream`);
    source.addEventListener('open', () => {
      setStreamConnected(true);
    });
    source.addEventListener('agent_output', (event) => {
      appendStreamEvent(JSON.parse((event as MessageEvent).data) as AgentStreamEvent);
    });
    source.addEventListener('heartbeat', (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as AgentStreamEvent;
      setStreamConnected(true);
      setStreamEvents((current) => (current.length ? current : [payload]));
    });
    source.addEventListener('stream_error', (event) => {
      appendStreamEvent(JSON.parse((event as MessageEvent).data) as AgentStreamEvent);
    });
    source.onerror = () => {
      setStreamConnected(false);
    };
    return () => source.close();
  }, []);

  const submitOpinion = async (event: FormEvent<HTMLFormElement>) => {
    // 提交分析师观点，后端会解析预测并在必要时返回人工复核项。
    event.preventDefault();
    if (!analystName.trim() || !content.trim()) {
      setMessage('请填写分析师和观点内容');
      return;
    }
    setLoading(true);
    setMessage('');
    appendStreamEvent({ id: Date.now(), type: 'local', message: `开始解析 ${analystName.trim()} 的观点`, created_at: new Date().toISOString() });
    try {
      const result = await requestJson<OpinionResponse>('/api/opinions', {
        method: 'POST',
        body: JSON.stringify({ analyst_name: analystName, content, source_url: sourceUrl || null })
      });
      setMessage(`已生成 ${result.predictions.length} 条预测，Agent 决策：${decisionText(result.agent_run?.decision)}${result.needs_user_confirmation ? '；存在字段需要人工确认' : ''}`);
      appendStreamEvent({ id: Date.now() + 1, type: 'local', message: `观点解析完成：生成 ${result.predictions.length} 条预测`, created_at: new Date().toISOString() });
      setContent('');
      setSourceUrl('');
      await loadAll();
      if (result.needs_user_confirmation && result.review_item_id) {
        const review = await requestJson<HumanReview>(`/api/reviews/${result.review_item_id}`);
        setImmediateReview(review);
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '提交失败');
      appendStreamEvent({ id: Date.now() + 2, type: 'error', message: error instanceof Error ? error.message : '提交失败', created_at: new Date().toISOString() });
    } finally {
      setLoading(false);
    }
  };

  const runAgent = async () => {
    // 手动触发 AI 聚合交易 Agent。
    setLoading(true);
    setMessage('');
    appendStreamEvent({ id: Date.now(), type: 'local', message: '开始运行 AI 聚合交易 Agent', created_at: new Date().toISOString() });
    try {
      const result = await requestJson<AgentRun>('/api/agent/run', {
        method: 'POST',
        body: JSON.stringify({ trigger: 'manual' })
      });
      setMessage(`Agent 完成：${decisionText(result.decision)}，${result.risk}`);
      appendStreamEvent({ id: Date.now() + 1, type: 'local', message: `Agent 完成：${decisionText(result.decision)}，${result.risk}`, created_at: new Date().toISOString() });
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Agent 运行失败');
      appendStreamEvent({ id: Date.now() + 2, type: 'error', message: error instanceof Error ? error.message : 'Agent 运行失败', created_at: new Date().toISOString() });
    } finally {
      setLoading(false);
    }
  };

  const verifyDue = async () => {
    // 手动验证所有已到期预测。
    setLoading(true);
    try {
      const result = await requestJson<{ verified_count: number }>('/api/predictions/verify-due', { method: 'POST' });
      setMessage(`已验证 ${result.verified_count} 条到期预测`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '验证失败');
    } finally {
      setLoading(false);
    }
  };

  const createReport = async () => {
    // 手动生成 BTC 每日报告。
    setLoading(true);
    setMessage('');
    appendStreamEvent({ id: Date.now(), type: 'local', message: '开始生成每日 BTC 报告', created_at: new Date().toISOString() });
    try {
      const result = await requestJson<AgentReport>('/api/reports/daily', { method: 'POST' });
      setMessage(`日报已生成：#${result.id}`);
      appendStreamEvent({ id: Date.now() + 1, type: 'local', message: `日报已生成：#${result.id}`, created_at: new Date().toISOString() });
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '生成日报失败');
      appendStreamEvent({ id: Date.now() + 2, type: 'error', message: error instanceof Error ? error.message : '生成日报失败', created_at: new Date().toISOString() });
    } finally {
      setLoading(false);
    }
  };

  const loadReviewDetail = async (reviewId: number) => {
    setLoading(true);
    try {
      const result = await requestJson<HumanReview>(`/api/reviews/${reviewId}`);
      setSelectedReview(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '加载人工确认详情失败');
    } finally {
      setLoading(false);
    }
  };

  const confirmReview = async (review: HumanReview) => {
    // 确认人工复核草稿，将解析结果正式写入预测表。
    setLoading(true);
    try {
      const result = await requestJson<{ predictions: Prediction[] }>(`/api/reviews/${review.id}/confirm`, {
        method: 'POST',
        body: JSON.stringify({
          analyst_name: review.draft_meta?.analyst_name || null,
          source_url: review.draft_meta?.source_url || null,
          published_at: review.draft_meta?.published_at || null,
          predictions: review.draft?.parsed_predictions || []
        })
      });
      setMessage(`已确认人工复核，生成 ${result.predictions.length} 条预测`);
      setSelectedReview(null);
      setImmediateReview((current) => current?.id === review.id ? null : current);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '确认失败');
    } finally {
      setLoading(false);
    }
  };

  const rejectReview = async (review: HumanReview) => {
    // 拒绝人工复核项，避免错误解析进入正式预测。
    setLoading(true);
    try {
      await requestJson<HumanReview>(`/api/reviews/${review.id}/reject`, {
        method: 'POST',
        body: JSON.stringify({ reason: '前端人工拒绝' })
      });
      setMessage('已拒绝人工复核项');
      setSelectedReview(null);
      setImmediateReview((current) => current?.id === review.id ? null : current);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '拒绝失败');
    } finally {
      setLoading(false);
    }
  };

  const loadPredictionVerification = async (predictionId: number) => {
    setLoading(true);
    try {
      const result = await requestJson<VerificationResult>(`/api/predictions/${predictionId}/verification`);
      setSelectedVerification(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '加载验证详情失败');
    } finally {
      setLoading(false);
    }
  };

  const startEditPrediction = (prediction: Prediction) => {
    // 初始化预测编辑表单。
    setEditingPrediction(prediction);
    setPredictionEditForm({
      direction: prediction.direction,
      horizon: prediction.horizon,
      target_price: prediction.target_price === null || prediction.target_price === undefined ? '' : String(prediction.target_price),
      verification_time: toDatetimeLocalValue(prediction.verification_time),
      status: prediction.status,
      confidence: prediction.confidence || 'medium',
      summary: prediction.summary || ''
    });
  };

  const submitPredictionEdit = async (event: FormEvent) => {
    // 提交人工修正后的预测字段。
    event.preventDefault();
    if (!editingPrediction || !predictionEditForm) return;
    setLoading(true);
    try {
      await requestJson<Prediction>(`/api/predictions/${editingPrediction.id}`, {
        method: 'PUT',
        body: JSON.stringify({
          direction: predictionEditForm.direction,
          horizon: predictionEditForm.horizon,
          target_price: predictionEditForm.target_price.trim() ? Number(predictionEditForm.target_price) : null,
          verification_time: predictionEditForm.verification_time ? new Date(predictionEditForm.verification_time).toISOString() : null,
          status: predictionEditForm.status,
          confidence: predictionEditForm.confidence,
          summary: predictionEditForm.summary
        })
      });
      setMessage(`已人工修正预测 #${editingPrediction.id}`);
      setEditingPrediction(null);
      setPredictionEditForm(null);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '修正预测失败');
    } finally {
      setLoading(false);
    }
  };

  const deletePrediction = async (prediction: Prediction) => {
    // 删除预测前要求用户确认，避免误删验证和报告数据。
    if (!window.confirm(`确认删除预测 #${prediction.id}？关联验证结果和报告会一并删除。`)) return;
    setLoading(true);
    try {
      await requestJson<{ deleted: boolean }>(`/api/predictions/${prediction.id}`, { method: 'DELETE' });
      setMessage(`已删除预测 #${prediction.id}`);
      if (selectedVerification?.prediction_id === prediction.id) setSelectedVerification(null);
      if (predictionReplay?.prediction?.id === prediction.id) setPredictionReplay(null);
      if (editingPrediction?.id === prediction.id) {
        setEditingPrediction(null);
        setPredictionEditForm(null);
      }
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '删除预测失败');
    } finally {
      setLoading(false);
    }
  };

  const loadPredictionReplay = async (predictionId: number) => {
    setLoading(true);
    try {
      const result = await requestJson<PredictionReplay>(`/api/predictions/${predictionId}/replay`);
      setPredictionReplay(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '加载预测回放失败');
    } finally {
      setLoading(false);
    }
  };

  const loadAgentReplay = async (agentRunId: number) => {
    setLoading(true);
    try {
      const result = await requestJson<AgentRunReplay>(`/api/agent/runs/${agentRunId}/replay`);
      setAgentReplay(result);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '加载 Agent 回放失败');
    } finally {
      setLoading(false);
    }
  };

  const runSchedulerTask = async (taskName: string) => {
    // 手动触发后台调度任务，便于在设置页测试。
    setLoading(true);
    try {
      const result = await requestJson<{ status: string }>(`/api/scheduler/tasks/${taskName}/run`, { method: 'POST' });
      setMessage(`任务 ${taskName} 执行结果：${result.status}`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '任务执行失败');
    } finally {
      setLoading(false);
    }
  };

  const refreshAccountSnapshot = async () => {
    // 立即记录一次 AI 聚合账户权益快照。
    setLoading(true);
    try {
      const result = await requestJson<VirtualAccountSummary>('/api/account/ai/snapshot', { method: 'POST' });
      setMessage(`AI 账户快照已刷新：权益 ${formatNumber(result.equity)} USDT，ROI ${formatNumber(result.roi)}%`);
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '账户快照刷新失败');
    } finally {
      setLoading(false);
    }
  };

  const market = dashboard?.market;
  // 以下派生状态统一从接口数据计算，避免在 JSX 中重复处理。
  const latestMarketRow = marketRows[marketRows.length - 1];
  const latestRun = dashboard?.latest_agent_run;
  const openTrade = dashboard?.open_trade;
  const accountSummary = account || dashboard?.account || null;
  const analystAccounts = analysts.map((analyst) => analyst.account).filter((item): item is VirtualAccountSummary => Boolean(item));
  const selectedAnalyst = analysts.find((analyst) => analyst.id === selectedAnalystId);
  const activeAccountDetail = selectedAnalystAccount || analystAccounts.find((item) => item.analyst_id === selectedAnalystId) || null;
  const openTrades = trades.filter((trade) => trade.status === 'open');
  const closedTrades = trades.filter((trade) => trade.status !== 'open');
  const winningClosedTrades = closedTrades.filter((trade) => (trade.pnl || 0) > 0);
  const totalClosedPnl = closedTrades.reduce((sum, trade) => sum + Number(trade.pnl || 0), 0);
  const totalFee = trades.reduce((sum, trade) => sum + Number(trade.fee || 0) + Number(trade.funding_fee || 0), 0);
  const longExposure = openTrades.filter((trade) => trade.side === 'long').reduce((sum, trade) => sum + Number(trade.notional_usdt || 0), 0);
  const shortExposure = openTrades.filter((trade) => trade.side === 'short').reduce((sum, trade) => sum + Number(trade.notional_usdt || 0), 0);
  const openNotional = longExposure + shortExposure;
  const marginUsage = accountSummary?.equity ? (openTrades.reduce((sum, trade) => sum + Number(trade.margin || 0), 0) / accountSummary.equity) * 100 : 0;
  const winRate = closedTrades.length ? (winningClosedTrades.length / closedTrades.length) * 100 : 0;
  const riskLevel = marginUsage >= 50 || Math.abs(accountSummary?.drawdown || 0) >= 20 ? '高' : marginUsage >= 25 || Math.abs(accountSummary?.drawdown || 0) >= 10 ? '中' : '低';
  const displayPrice = livePrice?.price || market?.latest_price;
  const liveSourceText = livePrice?.source === 'db_fallback' ? '数据库备用价' : livePrice?.source === 'unavailable' ? '实时源不可用' : livePrice?.source ? 'Binance 实时价' : '等待实时价格';
  const sortedPredictions = [...predictions].sort((left, right) => predictionTimeValue(left) - predictionTimeValue(right) || left.id - right.id);
  const pendingPredictions = sortedPredictions.filter((prediction) => !isVerifiedPrediction(prediction));
  const verifiedPredictions = sortedPredictions.filter(isVerifiedPrediction);
  const visibleVerifiedPredictions = verifiedPredictionsExpanded ? verifiedPredictions : [];
  const rankedAnalysts = [...analysts].sort((left, right) => Number(right.total_score || 0) - Number(left.total_score || 0));
  const topAnalysts = rankedAnalysts.slice(0, 8);
  const analystDetailCards = (
    <div className="analyst-list">
      {rankedAnalysts.map((analyst) => (
        <article className="analyst-card" key={analyst.id}>
          <div>
            <strong>{analyst.name}</strong>
            <p>{analyst.latest_opinion || '暂无最新观点'}</p>
          </div>
          <div className="score-pill">{formatNumber(analyst.total_score)} 分</div>
          <div className="mini-grid">
            <span>硬胜率 {formatNumber(analyst.hard_win_rate ?? analyst.direction_win_rate)}%</span>
            <span>加权 {formatNumber(analyst.weighted_win_rate)}%</span>
            <span>方向 {formatNumber(analyst.direction_accuracy ?? analyst.direction_win_rate)}%</span>
            <span>目标 {formatNumber(analyst.target_accuracy ?? analyst.target_hit_rate)}%</span>
            <span>稳定 {formatNumber(analyst.stability_score)}%</span>
            <span>改口 {formatNumber(analyst.modification_rate)}%</span>
            <span>账户ROI {formatNumber(analyst.account_roi ?? analyst.virtual_roi)}%</span>
            <span>短期 {formatNumber(analyst.short_win_rate)}%</span>
            <span>中期 {formatNumber(analyst.medium_win_rate)}%</span>
            <span>长期 {formatNumber(analyst.long_win_rate)}%</span>
            <span>权益 {formatNumber(analyst.account_equity)} USDT</span>
            <span>持仓 {formatNumber(analyst.open_position_count, 0)} 个</span>
            <span>预测 {formatNumber(analyst.prediction_count, 0)} 条</span>
          </div>
        </article>
      ))}
      {!rankedAnalysts.length && <div className="empty">暂无分析师。</div>}
    </div>
  );
  const agentRunList = (
    <div className="run-list">
      {agentRuns.map((run) => (
        <article className="run-card" key={run.id}>
          <div className="run-head">
            <strong>{decisionText(run.decision)}</strong>
            <span>{formatDate(run.created_at)}</span>
          </div>
          <p>{run.market_summary}</p>
          <p>{run.opinion_summary}</p>
          <em>{run.output?.trade_event || run.risk}</em>
          <button className="ghost-button tiny" type="button" onClick={() => loadAgentReplay(run.id)} disabled={loading}>节点回放</button>
        </article>
      ))}
      {!agentRuns.length && <div className="empty">暂无 Agent 运行记录。</div>}
    </div>
  );

  return (
    <main className="app-shell">
      <header className="hero">
        <div>
          <span className="eyebrow"><Bot size={16} /> BTC 分析师智能中枢</span>
          <h1>BTC 分析师追踪与合约模拟系统</h1>
          <p>集中管理实时行情、分析师观点、预测验证、独立虚拟合约账户和 LangGraph 报告。</p>
        </div>
        <div className="hero-actions">
          <button className="ghost-button" onClick={loadAll} disabled={loading}>
            <RefreshCw size={16} /> 刷新
          </button>
          <button className="primary-button" onClick={runAgent} disabled={loading}>
            <BrainCircuit size={16} /> 运行 Agent
          </button>
          <button className="ghost-button" onClick={createReport} disabled={loading}>
            <LineChart size={16} /> 生成日报
          </button>
        </div>
      </header>

      {message && <div className="message">{message}</div>}

      {showProjectNotice && (
        <section className="project-notice-backdrop" role="dialog" aria-modal="true" aria-labelledby="project-notice-title">
          <div className="project-notice-modal">
            <div className="project-notice-header">
              <span className="project-notice-icon"><ShieldAlert size={24} /></span>
              <div>
                <h2 id="project-notice-title">项目说明</h2>
                <p>本项目为实验项目，用于演示加密货币行情分析、观点追踪与智能体工作流。</p>
              </div>
            </div>
            <div className="project-notice-points">
              <span>由于国内加密货币行情信息较少，当前版本未加载相关信息爬虫。</span>
              <span>系统仅添加了数条示例信息，用于展示页面、流程和功能效果。</span>
            </div>
            <div className="project-notice-actions">
              <button className="primary-button" type="button" onClick={() => setShowProjectNotice(false)}>我知道了</button>
            </div>
          </div>
        </section>
      )}

      {immediateReview && (
        <section className="quick-review-popover">
          <div className="run-head">
            <div>
              <strong>需要人工确认</strong>
              <p>{immediateReview.suggested_question || '请确认该观点解析结果。'}</p>
            </div>
            <span>#{immediateReview.id}</span>
          </div>
          <div className="quick-review-summary">
            <span>分析师：{immediateReview.draft_meta?.analyst_name || '未命名分析师'}</span>
            <span>预测数：{formatNumber(immediateReview.draft?.parsed_predictions?.length || 0, 0)} 条</span>
          </div>
          <pre>{JSON.stringify(immediateReview.draft?.parsed_predictions || immediateReview.data || {}, null, 2)}</pre>
          <div className="inline-actions">
            <button className="primary-button tiny" type="button" onClick={() => confirmReview(immediateReview)} disabled={loading}>确认入库</button>
            <button className="ghost-button tiny" type="button" onClick={() => setImmediateReview(null)}>稍后处理</button>
            <button className="ghost-button tiny danger" type="button" onClick={() => rejectReview(immediateReview)} disabled={loading}>拒绝</button>
            <button className="ghost-button tiny" type="button" onClick={() => { setSelectedReview(immediateReview); setImmediateReview(null); }}>详情</button>
          </div>
        </section>
      )}

      <nav className="view-tabs">
        {APP_VIEWS.map((view) => (
          <button
            className={activeView === view.id ? 'active' : ''}
            key={view.id}
            type="button"
            onClick={() => setActiveView(view.id)}
          >
            <strong>{view.label}</strong>
            <span>{view.description}</span>
          </button>
        ))}
      </nav>

      <section className={`debug-panel ${debugPanelCollapsed ? 'collapsed' : 'expanded'}`}>
        {debugPanelCollapsed ? (
          <button className="debug-tab" type="button" onClick={() => setDebugPanelCollapsed(false)}>
            <strong>调试窗口</strong>
            <span>{streamConnected ? '已连接' : '未连接'} · {formatNumber(streamEvents.length, 0)} 条</span>
          </button>
        ) : (
          <div className="debug-body">
            <div className="stream-head">
              <div>
                <strong>调试窗口</strong>
                <span>{streamConnected ? '实时连接中' : '等待连接'} · AI 节点输出</span>
              </div>
              <div className="debug-actions">
                <button className="ghost-button tiny" type="button" onClick={() => setStreamEvents([])}>清空</button>
                <button className="ghost-button tiny" type="button" onClick={() => setDebugPanelCollapsed(true)}>收起</button>
              </div>
            </div>
            <div className="stream-list">
              {streamEvents.slice(0, 12).map((event) => (
                <article className={`stream-item ${event.status === 'failed' || event.type === 'error' ? 'failed' : ''}`} key={`${event.type || 'event'}-${event.id}-${event.created_at || ''}`}>
                  <div className="stream-meta">
                    <span>{streamEventTitle(event)}</span>
                    <em>{formatDate(event.created_at || undefined)}</em>
                  </div>
                  <p>{event.message}</p>
                </article>
              ))}
              {!streamEvents.length && <div className="empty">等待 AI 节点输出、报告生成、观点解析或交易决策。</div>}
            </div>
          </div>
        )}
      </section>

      <section className="metrics-grid">
        <MetricCard title="BTC 实时价格" value={`$${formatNumber(displayPrice)}`} desc={`${liveSourceText} · 24h ${formatNumber(market?.change_24h)}%`} icon={<Activity size={22} />} />
        <MetricCard title="待验证预测" value={formatNumber(dashboard?.pending_prediction_count, 0)} desc={`到期 ${formatNumber(dashboard?.due_prediction_count, 0)} 条`} icon={<Clock3 size={22} />} />
        <MetricCard title="Agent 最新动作" value={decisionText(latestRun?.decision)} desc={latestRun?.risk || '尚未运行'} icon={<ShieldAlert size={22} />} />
        <MetricCard title="AI 账户权益" value={`${formatNumber(accountSummary?.equity)} USDT`} desc={`ROI ${formatNumber(accountSummary?.roi)}% · 回撤 ${formatNumber(accountSummary?.max_drawdown)}%`} icon={<WalletCards size={22} />} />
      </section>

      {activeView === 'accounts' && (
      <section className="panel account-panel">
        <div className="panel-title">
          <div>
            <h2>AI 聚合交易账户</h2>
            <p>独立于交易员账户，综合所有交易员预测、评分、置信度和市场状态生成虚拟交易。{accountSummary?.symbol || 'BTCUSDT'} · 标记价 ${formatNumber(accountSummary?.mark_price)} · 最近快照 {formatDate(accountSummary?.snapshot_time)}</p>
          </div>
          <button className="ghost-button" type="button" onClick={refreshAccountSnapshot} disabled={loading}>
            <RefreshCw size={16} /> 刷新 AI 快照
          </button>
        </div>
        <div className="account-layout">
          <div>
            <Sparkline values={equityCurve.map((item) => item.equity)} />
            <div className="market-stats">
              <span>初始权益：{formatNumber(accountSummary?.initial_balance)} USDT</span>
              <span>钱包余额：{formatNumber(accountSummary?.wallet_balance)} USDT</span>
              <span>账户权益：{formatNumber(accountSummary?.equity)} USDT</span>
              <span>已实现：{formatNumber(accountSummary?.realized_pnl)} USDT</span>
              <span>未实现：{formatNumber(accountSummary?.unrealized_pnl)} USDT</span>
              <span>ROI：{formatNumber(accountSummary?.roi)}%</span>
              <span>当前回撤：{formatNumber(accountSummary?.drawdown)}%</span>
              <span>最大回撤：{formatNumber(accountSummary?.max_drawdown)}%</span>
              <span>手续费：{formatNumber(accountSummary?.fee_paid)} USDT</span>
              <span>资金费：{formatNumber(accountSummary?.funding_fee)} USDT</span>
              <span>最高权益：{formatNumber(accountSummary?.max_equity)} USDT</span>
              <span>聚合方向：{decisionText(accountSummary?.signal?.decision)}</span>
              <span>信号置信度：{accountSummary?.signal?.confidence || '-'}</span>
              <span>建议名义：{formatNumber(accountSummary?.signal?.position_notional)} USDT</span>
            </div>
          </div>
          <div className="position-card">
            <h3>AI 当前持仓</h3>
            {accountSummary?.open_positions?.length ? (
              <div className="run-list">
                {accountSummary.open_positions.map((position) => (
                  <article className="run-card compact-card" key={position.id}>
                    <div className="run-head">
                      <strong>AI 聚合账户 · {tradeSideText(position.side)}</strong>
                      <span>{formatNumber(position.leverage)}x</span>
                    </div>
                    <div className="mini-grid">
                      <span>名义 {formatNumber(position.notional_usdt)} USDT</span>
                      <span>保证金 {formatNumber(position.margin)} USDT</span>
                      <span>开仓 ${formatNumber(position.entry_price)}</span>
                      <span>标记 ${formatNumber(position.mark_price)}</span>
                      <span>未实现 {formatNumber(position.unrealized_pnl)} USDT</span>
                      <span>资金费 {formatNumber(position.funding_fee)} USDT</span>
                    </div>
                    <p>{position.prediction_summary || position.reason}</p>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty">AI 账户暂无持仓，等待聚合信号达到交易阈值。</div>
            )}
          </div>
        </div>
        <div className="trading-summary-grid">
          <div className="setting-item"><strong>风险等级</strong><span className={`risk-${riskLevel === '高' ? 'high' : riskLevel === '中' ? 'medium' : 'low'}`}>{riskLevel}</span></div>
          <div className="setting-item"><strong>保证金占用</strong><span>{formatNumber(marginUsage)}%</span></div>
          <div className="setting-item"><strong>持仓名义</strong><span>{formatNumber(openNotional)} USDT</span></div>
          <div className="setting-item"><strong>多/空敞口</strong><span>{formatNumber(longExposure)} / {formatNumber(shortExposure)} USDT</span></div>
          <div className="setting-item"><strong>闭仓胜率</strong><span>{formatNumber(winRate)}%</span></div>
          <div className="setting-item"><strong>闭仓盈亏</strong><span className={totalClosedPnl >= 0 ? 'up' : 'down'}>{formatNumber(totalClosedPnl)} USDT</span></div>
          <div className="setting-item"><strong>累计费用</strong><span>{formatNumber(totalFee)} USDT</span></div>
          <div className="setting-item"><strong>AI 交易统计</strong><span>{formatNumber(openTrades.length, 0)} 持仓 / {formatNumber(closedTrades.length, 0)} 已平</span></div>
        </div>
        {accountSummary?.signal?.supporting_predictions?.length ? (
          <div className="run-list compact-summary">
            {accountSummary.signal.supporting_predictions.slice(0, 4).map((prediction) => (
              <article className="run-card compact-card" key={prediction.id || `${prediction.analyst_name}-${prediction.weight}`}>
                <div className="run-head">
                  <strong>{prediction.analyst_name || '交易员信号'}</strong>
                  <span>权重 {formatNumber(prediction.weight)}</span>
                </div>
                <p>{prediction.summary || `${prediction.direction || '-'} · ${prediction.horizon || '-'}`}</p>
              </article>
            ))}
          </div>
        ) : null}
        <div className="analyst-account-grid">
          {analystAccounts.map((item) => (
            <article
              className={`analyst-account-card ${selectedAnalystId === item.analyst_id ? 'selected' : ''}`}
              key={item.analyst_id || item.analyst_name || item.snapshot_time}
              onClick={() => item.analyst_id && void loadAnalystAccountDetail(item.analyst_id)}
            >
              <div className="run-head">
                <strong>{item.analyst_name || '未命名分析师'}</strong>
                <span>{formatNumber(item.open_positions?.length || 0, 0)} 个持仓</span>
              </div>
              <div className="mini-grid">
                <span>权益 {formatNumber(item.equity)} USDT</span>
                <span>ROI {formatNumber(item.roi)}%</span>
                <span>已实现 {formatNumber(item.realized_pnl)} USDT</span>
                <span>未实现 {formatNumber(item.unrealized_pnl)} USDT</span>
                <span>最大回撤 {formatNumber(item.max_drawdown)}%</span>
                <span>保证金 {formatNumber((item.open_positions || []).reduce((sum, trade) => sum + Number(trade.margin || 0), 0))} USDT</span>
                <span>手续费 {formatNumber(item.fee_paid)} USDT</span>
                <span>资金费 {formatNumber(item.funding_fee)} USDT</span>
              </div>
              <button className="ghost-button tiny" type="button" disabled={!item.analyst_id || loading}>查看详情</button>
            </article>
          ))}
          {!analystAccounts.length && <div className="empty">暂无分析师独立账户。</div>}
        </div>
        {activeAccountDetail && (
          <div className="detail-panel account-detail">
            <div className="panel-title compact">
              <div>
                <h2>{activeAccountDetail.analyst_name || selectedAnalyst?.name || '分析师账户'} 详情</h2>
                <p>独立虚拟合约账户、权益曲线、当前持仓和该分析师交易记录。</p>
              </div>
            </div>
            <Sparkline values={selectedAnalystCurve.map((item) => item.equity)} />
            <div className="market-stats">
              <span>权益：{formatNumber(activeAccountDetail.equity)} USDT</span>
              <span>ROI：{formatNumber(activeAccountDetail.roi)}%</span>
              <span>已实现：{formatNumber(activeAccountDetail.realized_pnl)} USDT</span>
              <span>未实现：{formatNumber(activeAccountDetail.unrealized_pnl)} USDT</span>
              <span>最大回撤：{formatNumber(activeAccountDetail.max_drawdown)}%</span>
              <span>持仓数：{formatNumber(activeAccountDetail.open_positions?.length || 0, 0)}</span>
              <span>手续费：{formatNumber(activeAccountDetail.fee_paid)} USDT</span>
              <span>资金费：{formatNumber(activeAccountDetail.funding_fee)} USDT</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>方向</th>
                    <th>状态</th>
                    <th>开仓价</th>
                    <th>平仓/标记</th>
                    <th>名义/保证金</th>
                    <th>盈亏</th>
                    <th>关联预测</th>
                  </tr>
                </thead>
                <tbody>
                  {selectedAnalystTrades.map((trade) => (
                    <tr key={trade.id}>
                      <td>{tradeSideText(trade.side)}</td>
                      <td>{tradeStatusText(trade.status)}</td>
                      <td>${formatNumber(trade.entry_price)}</td>
                      <td>${formatNumber(trade.exit_price ?? trade.mark_price)}</td>
                      <td>{formatNumber(trade.notional_usdt)} / {formatNumber(trade.margin)} USDT</td>
                      <td className={trade.pnl >= 0 ? 'up' : 'down'}>{formatNumber(trade.pnl)} USDT</td>
                      <td>{trade.prediction_summary || trade.reason || '-'}</td>
                    </tr>
                  ))}
                  {!selectedAnalystTrades.length && <tr><td colSpan={7}>暂无该分析师交易记录。</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
      )}

      {activeView === 'overview' && (
      <section className="main-grid">
        <div className="panel market-panel">
          <div className="panel-title">
            <div>
              <h2>行情摘要</h2>
              <p>{market?.symbol || 'BTCUSDT'} · {market?.interval || marketInterval} · 已加载 {marketRows.length} 根 K 线。</p>
            </div>
            <div className="inline-actions">
              <select value={marketInterval} onChange={(event) => setMarketInterval(event.target.value)}>
                {MARKET_INTERVALS.map((interval) => (
                  <option value={interval} key={interval}>{interval}</option>
                ))}
              </select>
              {market && market.change_24h >= 0 ? <TrendingUp className="up" /> : <TrendingDown className="down" />}
            </div>
          </div>
          <div className="price-row">
            <strong>${formatNumber(displayPrice)}</strong>
            <span className={market && market.change_24h >= 0 ? 'up' : 'down'}>{formatNumber(market?.change_24h)}%</span>
          </div>
          <Sparkline values={marketRows.length ? marketRows.map((row) => row.close) : market?.close_series || []} />
          <div className="market-stats">
            <span>支撑：${formatNumber(market?.support)}</span>
            <span>压力：${formatNumber(market?.resistance)}</span>
            <span>RSI：{formatNumber(market?.rsi_14)}</span>
            <span>ATR：{formatNumber(market?.atr_14)}</span>
            <span>MACD：{formatNumber(market?.macd, 4)}</span>
            <span>资金费率：{formatNumber(market?.funding_rate, 6)}</span>
            <span>MA20：{formatNumber(latestMarketRow?.ma_20)}</span>
            <span>EMA20：{formatNumber(latestMarketRow?.ema_20)}</span>
          </div>
        </div>

        <form className={activeView === 'overview' ? 'panel opinion-form' : 'hidden'} onSubmit={submitOpinion}>
          <div className="panel-title">
            <div>
              <h2>录入分析师观点</h2>
              <p>提交后会自动解析预测并触发 Agent 决策。</p>
            </div>
            <Send />
          </div>
          <label>
            分析师名称
            <input value={analystName} onChange={(event) => setAnalystName(event.target.value)} placeholder="例如：Analyst A" />
          </label>
          <label>
            来源链接
            <input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="可选" />
          </label>
          <label>
            观点原文
            <textarea value={content} onChange={(event) => setContent(event.target.value)} placeholder="例如：BTC 短期可能涨到 80000，中期可能回落到 75000。" />
          </label>
          <button className="primary-button full" disabled={loading}>
            <Send size={16} /> 提交并解析
          </button>
        </form>
      </section>
      )}

      <section className={activeView === 'analysts' ? 'panel analyst-data-panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>分析师数据</h2>
            <p>完整展示分析师评分、胜率、稳定性、账户收益、权益、持仓与预测数据。</p>
          </div>
          <LineChart />
        </div>
        {analystDetailCards}
      </section>

      <section className={activeView === 'predictions' ? 'panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>预测验证</h2>
            <p>展示 Agent 从观点中生成的结构化预测，已生成 {verificationResults.length} 条验证结果。</p>
          </div>
          <button className="ghost-button" onClick={verifyDue} disabled={loading}>
            <CheckCircle2 size={16} /> 验证到期
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>分析师</th>
                <th>方向</th>
                <th>周期</th>
                <th>目标价</th>
                <th>状态</th>
                <th>质量/分数</th>
                <th>验证时间</th>
                <th>摘要</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {pendingPredictions.map((prediction) => (
                <tr key={prediction.id}>
                  <td>{prediction.analyst_name}</td>
                  <td><span className={`tag ${prediction.direction}`}>{directionText(prediction.direction)}</span></td>
                  <td>{horizonText(prediction.horizon)}</td>
                  <td>${formatNumber(prediction.target_price)}</td>
                  <td>
                    <div className="prediction-status">
                      <span>{statusText(prediction.status)}</span>
                      {prediction.latest_change_type && <em>{changeTypeText(prediction.latest_change_type, prediction)}</em>}
                    </div>
                  </td>
                  <td>{qualityText(prediction.latest_quality_label)} · {formatNumber(prediction.latest_final_score, 3)}</td>
                  <td>{formatDate(prediction.verification_time)}</td>
                  <td>{prediction.summary}</td>
                  <td>
                    <div className="prediction-actions">
                      <button className="ghost-button tiny" type="button" onClick={() => loadPredictionVerification(prediction.id)} disabled={loading}>验证</button>
                      <button className="ghost-button tiny" type="button" onClick={() => loadPredictionReplay(prediction.id)} disabled={loading}>回放</button>
                      <button className="ghost-button tiny" type="button" onClick={() => startEditPrediction(prediction)} disabled={loading}>修正</button>
                      <button className="ghost-button tiny danger" type="button" onClick={() => deletePrediction(prediction)} disabled={loading}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
              {verifiedPredictions.length > 0 && (
                <tr className="prediction-group-row">
                  <td colSpan={9}>
                    <button className="ghost-button tiny" type="button" onClick={() => setVerifiedPredictionsExpanded((current) => !current)}>
                      {verifiedPredictionsExpanded ? '收起已验证' : '展开已验证'} · {verifiedPredictions.length} 条
                    </button>
                  </td>
                </tr>
              )}
              {visibleVerifiedPredictions.map((prediction) => (
                <tr className="verified-prediction-row" key={prediction.id}>
                  <td>{prediction.analyst_name}</td>
                  <td><span className={`tag ${prediction.direction}`}>{directionText(prediction.direction)}</span></td>
                  <td>{horizonText(prediction.horizon)}</td>
                  <td>${formatNumber(prediction.target_price)}</td>
                  <td>
                    <div className="prediction-status">
                      <span>{statusText(prediction.status)}</span>
                      {prediction.latest_change_type && <em>{changeTypeText(prediction.latest_change_type, prediction)}</em>}
                    </div>
                  </td>
                  <td>{qualityText(prediction.latest_quality_label)} · {formatNumber(prediction.latest_final_score, 3)}</td>
                  <td>{formatDate(prediction.verification_time)}</td>
                  <td>{prediction.summary}</td>
                  <td>
                    <div className="prediction-actions">
                      <button className="ghost-button tiny" type="button" onClick={() => loadPredictionVerification(prediction.id)} disabled={loading}>验证</button>
                      <button className="ghost-button tiny" type="button" onClick={() => loadPredictionReplay(prediction.id)} disabled={loading}>回放</button>
                      <button className="ghost-button tiny" type="button" onClick={() => startEditPrediction(prediction)} disabled={loading}>修正</button>
                      <button className="ghost-button tiny danger" type="button" onClick={() => deletePrediction(prediction)} disabled={loading}>删除</button>
                    </div>
                  </td>
                </tr>
              ))}
              {!predictions.length && <tr><td colSpan={9}>暂无预测，先录入一条观点。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>

      <section className={activeView === 'overview' ? 'two-columns' : 'hidden'}>
        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>分析师排行榜</h2>
              <p>按综合分排序，仅展示分数。</p>
            </div>
            <LineChart />
          </div>
          <div className="leaderboard-list">
            {topAnalysts.map((analyst, index) => (
              <article className="leaderboard-item" key={analyst.id}>
                <span className="leaderboard-rank">#{index + 1}</span>
                <strong>{analyst.name}</strong>
                <div className="score-pill">{formatNumber(analyst.total_score)} 分</div>
              </article>
            ))}
            {!topAnalysts.length && <div className="empty">暂无分析师。</div>}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>Agent 运行记录</h2>
              <p>每次决策保留输入摘要、输出和交易行为。</p>
            </div>
            <Bot />
          </div>
          {agentRunList}
        </div>
      </section>

      <section className={activeView === 'agent' ? 'panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>Agent 运行记录</h2>
            <p>每次决策保留输入摘要、输出和交易行为。</p>
          </div>
          <Bot />
        </div>
        {agentRunList}
      </section>

      <section className={activeView === 'agent' ? 'two-columns' : 'hidden'}>
        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>LangGraph 报告</h2>
              <p>由每日 BTC 报告 Graph 生成，包含市场摘要、分析师共识和风险提示。</p>
            </div>
            <BrainCircuit />
          </div>
          <div className="run-list">
            {reports.map((report) => (
              <article className="run-card" key={report.id}>
                <div className="run-head">
                  <strong>{report.title}</strong>
                  <span>{formatDate(report.created_at)}</span>
                </div>
                <p>{report.data?.executive_summary || report.data?.market_status || '暂无摘要'}</p>
                <div className="mini-grid">
                  <span>支撑 ${formatNumber(report.data?.key_levels?.support)}</span>
                  <span>压力 ${formatNumber(report.data?.key_levels?.resistance)}</span>
                  <span>活跃预测 {formatNumber(report.data?.active_prediction_count, 0)}</span>
                  <span>近期验证 {formatNumber(report.data?.recent_verification_count, 0)}</span>
                  <span>观点变化 {formatNumber(report.data?.prediction_change_count, 0)}</span>
                  <span>账户 ROI {formatNumber(report.data?.account_snapshot?.roi)}%</span>
                  <span>账户权益 ${formatNumber(report.data?.account_snapshot?.equity)}</span>
                  <span>持仓数 {formatNumber(report.data?.account_snapshot?.open_position_count, 0)}</span>
                </div>
                {report.data?.analyst_consensus && <p>{report.data.analyst_consensus}</p>}
                {report.data?.recent_prediction_review && <p>{report.data.recent_prediction_review}</p>}
                {report.data?.prediction_change_review && <p>{report.data.prediction_change_review}</p>}
                {reportScenarios(report.data?.scenarios).slice(0, 3).map((scenario, index) => (
                  <p key={`${report.id}-scenario-${index}`}><strong>{scenario.scenario || '情景'}</strong>：{scenario.description || '-'}</p>
                ))}
                {!!report.data?.risk_warnings?.length && <p>风险提示：{report.data.risk_warnings.join('；')}</p>}
                <em>{report.data?.disclaimer || '仅用于信息整理，不构成投资建议。'}</em>
              </article>
            ))}
            {!reports.length && <div className="empty">暂无报告，可点击“生成日报”。</div>}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>人工确认队列</h2>
              <p>当观点解析存在低置信字段或数据异常时，会进入确认队列。</p>
            </div>
            <ShieldAlert />
          </div>
          <div className="run-list">
            {reviews.map((review) => (
              <article className="run-card" key={review.id}>
                <div className="run-head">
                  <strong>{review.status}</strong>
                  <span>{formatDate(review.created_at)}</span>
                </div>
                <p>{review.suggested_question || '请复核该观点解析结果。'}</p>
                <div className="inline-actions">
                  <button className="ghost-button tiny" type="button" onClick={() => loadReviewDetail(review.id)} disabled={loading}>详情</button>
                </div>
              </article>
            ))}
            {!reviews.length && <div className="empty">暂无待确认项。</div>}
          </div>
        </div>
      </section>

      <section className={activeView === 'settings' ? 'two-columns' : 'hidden'}>
        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>调度任务</h2>
              <p>{scheduler?.available ? (scheduler.running ? '调度器运行中' : '调度器未启用') : scheduler?.reason || '调度器不可用'}</p>
            </div>
            <Clock3 />
          </div>
          <div className="inline-actions">
            <button className="ghost-button" type="button" onClick={() => runSchedulerTask('verify_due')} disabled={loading}>验证到期</button>
            <button className="ghost-button" type="button" onClick={() => runSchedulerTask('market_sync')} disabled={loading}>同步行情</button>
            <button className="ghost-button" type="button" onClick={() => runSchedulerTask('account_snapshot')} disabled={loading}>账户快照</button>
            <button className="ghost-button" type="button" onClick={() => runSchedulerTask('daily_report')} disabled={loading}>生成日报</button>
          </div>
          <div className="run-list">
            {(scheduler?.jobs || []).map((job) => (
              <article className="run-card" key={job.id}>
                <div className="run-head">
                  <strong>{job.id}</strong>
                  <span>{job.next_run_time ? formatDate(job.next_run_time) : '暂无下次运行'}</span>
                </div>
              </article>
            ))}
          </div>
        </div>

        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>系统设置</h2>
              <p>展示关键配置；修改接口已在后端开放。</p>
            </div>
            <ShieldAlert />
          </div>
          <div className="settings-grid">
            {settings.map((item) => (
              <div className="setting-item" key={item.key}>
                <strong>{item.description || item.key}</strong>
                <span>{settingValueText(item.parsed_value)}</span>
                <small>{item.key}</small>
              </div>
            ))}
          </div>
        </div>
      </section>

      {selectedReview && (
        <section className="panel detail-panel">
          <div className="panel-title compact">
            <div>
              <h2>人工确认详情 #{selectedReview.id}</h2>
              <p>{selectedReview.suggested_question || '请复核该观点解析结果。'}</p>
            </div>
            <div className="inline-actions">
              <button className="primary-button" type="button" onClick={() => confirmReview(selectedReview)} disabled={loading}>确认入库</button>
              <button className="ghost-button" type="button" onClick={() => rejectReview(selectedReview)} disabled={loading}>拒绝</button>
              <button className="ghost-button" type="button" onClick={() => setSelectedReview(null)}>关闭</button>
            </div>
          </div>
          <div className="detail-grid">
            <div>
              <h3>草稿元数据</h3>
              <pre>{JSON.stringify(selectedReview.draft_meta || {}, null, 2)}</pre>
            </div>
            <div>
              <h3>解析草稿</h3>
              <pre>{JSON.stringify(selectedReview.draft || selectedReview.data || {}, null, 2)}</pre>
            </div>
          </div>
        </section>
      )}

      {editingPrediction && predictionEditForm && (
        <section className="panel detail-panel">
          <div className="panel-title compact">
            <div>
              <h2>人工修正预测 #{editingPrediction.id}</h2>
              <p>{editingPrediction.analyst_name} · 修改后会标记为人工修正，并重新计算分析师评分。</p>
            </div>
            <button className="ghost-button" type="button" onClick={() => { setEditingPrediction(null); setPredictionEditForm(null); }}>关闭</button>
          </div>
          <form className="manual-edit-form" onSubmit={submitPredictionEdit}>
            <div className="detail-grid">
              <label>
                方向
                <select value={predictionEditForm.direction} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, direction: event.target.value })}>
                  <option value="bullish">看涨</option>
                  <option value="bearish">看跌</option>
                  <option value="sideways">震荡</option>
                </select>
              </label>
              <label>
                周期
                <select value={predictionEditForm.horizon} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, horizon: event.target.value })}>
                  <option value="intraday">日内</option>
                  <option value="short">短期</option>
                  <option value="medium">中期</option>
                  <option value="long">长期</option>
                </select>
              </label>
              <label>
                目标价
                <input type="number" step="0.01" value={predictionEditForm.target_price} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, target_price: event.target.value })} />
              </label>
              <label>
                验证时间
                <input type="datetime-local" value={predictionEditForm.verification_time} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, verification_time: event.target.value })} />
              </label>
              <label>
                状态
                <select value={predictionEditForm.status} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, status: event.target.value })}>
                  <option value="pending">待验证</option>
                  <option value="success">成功</option>
                  <option value="failed">失败</option>
                  <option value="modified">已改口</option>
                </select>
              </label>
              <label>
                置信度
                <select value={predictionEditForm.confidence} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, confidence: event.target.value })}>
                  <option value="low">低</option>
                  <option value="medium">中</option>
                  <option value="high">高</option>
                </select>
              </label>
            </div>
            <label>
              摘要
              <textarea value={predictionEditForm.summary} onChange={(event) => setPredictionEditForm({ ...predictionEditForm, summary: event.target.value })} />
            </label>
            <div className="inline-actions">
              <button className="primary-button" type="submit" disabled={loading}>保存修正</button>
              <button className="ghost-button" type="button" onClick={() => { setEditingPrediction(null); setPredictionEditForm(null); }}>取消</button>
            </div>
          </form>
        </section>
      )}

      {selectedVerification && (
        <section className="panel detail-panel">
          <div className="panel-title compact">
            <div>
              <h2>验证详情 #{selectedVerification.prediction_id}</h2>
              <p>{selectedVerification.summary || '结构化验证结果'}</p>
            </div>
            <button className="ghost-button" type="button" onClick={() => setSelectedVerification(null)}>关闭</button>
          </div>
          <div className="market-stats">
            <span>状态：{selectedVerification.status}</span>
            <span>质量：{qualityText(selectedVerification.quality_label)}</span>
            <span>最终分：{formatNumber(selectedVerification.final_score, 3)}</span>
            <span>方向分：{formatNumber(selectedVerification.direction_score, 3)}</span>
            <span>目标分：{formatNumber(selectedVerification.target_score, 3)}</span>
            <span>时间分：{formatNumber(selectedVerification.time_score, 3)}</span>
            <span>涨跌幅：{formatNumber(selectedVerification.price_change_pct)}%</span>
            <span>目标距离：{formatNumber(selectedVerification.target_distance_pct)}%</span>
            <span>最近目标价：${formatNumber(selectedVerification.closest_price)}</span>
            <span>最高价：${formatNumber(selectedVerification.highest_price)}</span>
            <span>最低价：${formatNumber(selectedVerification.lowest_price)}</span>
            <span>最新价：${formatNumber(selectedVerification.latest_price)}</span>
          </div>
          {selectedVerification.report && (
            <div className="detail-grid">
              <div>
                <h3>验证报告</h3>
                <p>{selectedVerification.report.plain_language_summary || '暂无报告摘要'}</p>
              </div>
              <div>
                <h3>失败原因</h3>
                <p>{selectedVerification.report.failure_reason || '该预测未生成失败原因，或当前验证未失败。'}</p>
              </div>
            </div>
          )}
        </section>
      )}

      {predictionReplay && (
        <section className="panel detail-panel">
          <div className="panel-title compact">
            <div>
              <h2>预测回放 #{predictionReplay.prediction?.id || '-'}</h2>
              <p>{predictionReplay.prediction?.summary || '完整链路回放'}</p>
            </div>
            <button className="ghost-button" type="button" onClick={() => setPredictionReplay(null)}>关闭</button>
          </div>
          <div className="detail-grid">
            <div>
              <h3>原始观点</h3>
              <pre>{JSON.stringify(predictionReplay.raw_opinion || {}, null, 2)}</pre>
            </div>
            <div>
              <h3>验证与交易</h3>
              <pre>{JSON.stringify({ verification_result: predictionReplay.verification_result, verification_report: predictionReplay.verification_report, trades: predictionReplay.trades }, null, 2)}</pre>
            </div>
          </div>
          <div className="run-list">
            {(predictionReplay.versions || []).map((version) => (
              <article className="run-card" key={version.id}>
                <div className="run-head">
                  <strong>{changeTypeText(version.change_type)}</strong>
                  <span>严重度 {formatNumber((version.change_severity || 0) * 100)}%</span>
                </div>
                <p>{version.reason}</p>
                <div className="mini-grid">
                  <span>方向 {directionText(version.old_direction)} → {directionText(version.new_direction || '-')}</span>
                  <span>目标 ${formatNumber(version.old_target_price)} → ${formatNumber(version.new_target_price)}</span>
                  <span>周期 {horizonText(version.old_horizon || '-')} → {horizonText(version.new_horizon || '-')}</span>
                  <span>置信度 {version.old_confidence || '-'} → {version.new_confidence || '-'}</span>
                  <span>时间 {formatDate(version.created_at)}</span>
                </div>
              </article>
            ))}
            {!predictionReplay.versions?.length && <div className="empty">暂无观点变化记录。</div>}
          </div>
        </section>
      )}

      {agentReplay && (
        <section className="panel detail-panel">
          <div className="panel-title compact">
            <div>
              <h2>Agent 回放 #{agentReplay.agent_run?.id || '-'}</h2>
              <p>节点数 {agentReplay.nodes?.length || 0} · 交易数 {agentReplay.trades?.length || 0}</p>
            </div>
            <button className="ghost-button" type="button" onClick={() => setAgentReplay(null)}>关闭</button>
          </div>
          <div className="run-list">
            {(agentReplay.nodes || []).map((node) => (
              <article className="run-card" key={node.id}>
                <div className="run-head">
                  <strong>{node.node_name}</strong>
                  <span>{node.status}</span>
                </div>
                <pre>{JSON.stringify(node.output || {}, null, 2)}</pre>
              </article>
            ))}
          </div>
        </section>
      )}

      <section className={activeView === 'accounts' ? 'panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>AI 交易记录</h2>
            <p>共 {formatNumber(trades.length, 0)} 笔 AI 聚合账户交易，{formatNumber(openTrades.length, 0)} 笔持仓中；每笔交易保留支撑预测、费用与触发原因。</p>
          </div>
          <WalletCards />
        </div>
        <div className="trading-summary-grid compact-summary">
          <div className="setting-item"><strong>已平仓</strong><span>{formatNumber(closedTrades.length, 0)} 笔</span></div>
          <div className="setting-item"><strong>胜率</strong><span>{formatNumber(winRate)}%</span></div>
          <div className="setting-item"><strong>净盈亏</strong><span className={totalClosedPnl >= 0 ? 'up' : 'down'}>{formatNumber(totalClosedPnl)} USDT</span></div>
          <div className="setting-item"><strong>总费用</strong><span>{formatNumber(totalFee)} USDT</span></div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>账户/预测</th>
                <th>方向</th>
                <th>状态</th>
                <th>开仓价</th>
                <th>平仓/标记</th>
                <th>名义/保证金</th>
                <th>费用</th>
                <th>盈亏</th>
                <th>时间</th>
                <th>原因</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((trade) => (
                <tr key={trade.id}>
                  <td>
                    <strong>AI 聚合账户</strong>
                    <p>{trade.prediction_summary || (trade.prediction_id ? `预测 #${trade.prediction_id}` : '未关联预测')}</p>
                  </td>
                  <td>{tradeSideText(trade.side)}</td>
                  <td>{tradeStatusText(trade.status)}</td>
                  <td>${formatNumber(trade.entry_price)}</td>
                  <td>${formatNumber(trade.exit_price ?? trade.mark_price)}</td>
                  <td>{formatNumber(trade.notional_usdt)} USDT / {formatNumber(trade.margin)} 保证金 / {formatNumber(trade.leverage)}x</td>
                  <td>手续费 {formatNumber(trade.fee)} · 资金费 {formatNumber(trade.funding_fee)}</td>
                  <td className={trade.pnl >= 0 ? 'up' : 'down'}>{formatNumber(trade.pnl)} USDT</td>
                  <td>{formatDate(trade.opened_at)}{trade.closed_at ? ` → ${formatDate(trade.closed_at)}` : ''}</td>
                  <td>{trade.reason || '-'}</td>
                </tr>
              ))}
              {!trades.length && <tr><td colSpan={10}>暂无 AI 交易。</td></tr>}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
