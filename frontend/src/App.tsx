import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, Bot, BrainCircuit, CheckCircle2, Clock3, Database, Flame, LineChart, RefreshCw, Send, ShieldAlert, TrendingDown, TrendingUp } from 'lucide-react';

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
  created_at: string;
  output?: {
    decision_label?: string;
    analysis_event?: string;
    signal?: {
      bull_score: number;
      bear_score: number;
      bull_count: number;
      bear_count: number;
    };
    react_tools_used?: string[];
    react_tool_results?: Record<string, unknown>;
    evidence_conflict?: {
      has_conflict?: boolean;
      conflict_points?: string[];
      overall_confidence?: string;
      summary?: string;
    };
    gate_context_summary?: Record<string, unknown>;
    reflection?: {
      is_adequate?: boolean;
      weak_points?: string[];
      confidence_adjustment?: number;
      correction_suggestion?: string;
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
  data?: {
    context_snapshot?: Record<string, unknown>;
    [key: string]: unknown;
  };
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
    contract_status?: string;
    sentiment_status?: string;
    memory_status?: string;
    sentiment_topics?: string[];
    memory_summary?: string[];
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

type BtcContract = {
  last_price?: number;
  funding_rate?: number;
  open_interest?: number;
  change_pct_24h?: number;
  high_24h?: number;
  low_24h?: number;
  volume_24h?: number;
  [key: string]: unknown;
};

type GateSourceStatus = Record<string, { count?: number; latest?: string | null } | Record<string, unknown>>;

const GATE_STATUS_LABELS: Record<string, string> = {
  btc_contract_metrics: 'BTC 合约数据',
  sentiment_snapshots: '市场情绪快照',
  square_posts: '热门帖子',
  mcp_raw_records: 'MCP 原始记录',
  active_memories: '活跃记忆',
  analyst_source_accounts: '分析师源账户',
  followed_user_posts: '关注用户帖子',
};

type GateSyncTaskResult = {
  status?: string;
  error?: string;
  error_message?: string | null;
  data?: Record<string, unknown>;
  task_name?: string;
  [key: string]: unknown;
};

type SourceAccount = {
  id?: number;
  analyst_id?: number | null;
  source_platform?: string;
  source_user_id?: string;
  display_name?: string;
  enabled?: number;
  created_at?: string;
  [key: string]: unknown;
};

type SentimentSnapshot = {
  overall_sentiment?: string;
  bull_ratio?: number;
  bear_ratio?: number;
  funding_rate?: number;
  snapshot_time?: string;
  dominant_topics?: string[];
  crowd_positioning?: string;
  [key: string]: unknown;
};

type MarketMemory = {
  id: number;
  memory_type: string;
  symbol: string;
  title: string;
  content: string;
  importance: number;
  sentiment?: string;
  expectation?: string;
  source?: string;
  is_active: number;
  valid_from?: string;
  valid_until?: string;
  created_at: string;
  [key: string]: unknown;
};

type SquarePost = {
  id: number;
  post_id?: string;
  content: string;
  author?: string;
  author_id?: string;
  hot_score?: number;
  repost_count?: number;
  is_hot_post?: number;
  is_followed_user?: number;
  created_at: string;
  [key: string]: unknown;
};

type Dashboard = {
  market: MarketSummary;
  pending_prediction_count: number;
  due_prediction_count: number;
  latest_agent_run?: AgentRun | null;
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
};

type AgentEvidence = {
  agent_run_id?: number;
  input_snapshot?: Record<string, unknown>;
  output_snapshot?: Record<string, unknown>;
  evidence_refs?: string[];
  node_runs?: Record<string, unknown>[];
};

type AgentReflection = {
  agent_run_id?: number;
  reflection?: Record<string, unknown>;
  node_runs?: Record<string, unknown>[];
};

type AgentRunReplay = {
  agent_run?: AgentRun;
  evidence?: AgentEvidence;
  reflection?: AgentReflection;
  nodes?: {
    id: number;
    graph_name: string;
    node_name: string;
    status: string;
    input?: Record<string, unknown>;
    output?: Record<string, unknown>;
    error_message?: string | null;
  }[];
  focus_predictions?: Prediction[];
};


type TradeAdvice = {
  success: boolean;
  suggested_direction: string;
  suggested_size: number;
  suggested_price_type: string;
  reason: string;
  errors?: string[];
};

type GateFuturesAccount = {
  currency?: string;
  available?: string;
  total?: string;
  position_margin?: string;
  error?: string;
};

type GatePosition = {
  contract: string;
  size: number;
  leverage: string;
  entry_price: string;
  mark_price: string;
  liq_price?: string;
  unrealised_pnl: string;
  realised_pnl?: string;
  margin?: string;
  value?: string;
  mode?: string;
  error?: string;
};

type GateOrder = {
  order_id: string;
  status: string;
  contract: string;
  size: number;
  price: string;
  left: number;
  fill_price?: string;
  text?: string;
  tif?: string;
  create_time?: number;
  is_close?: boolean;
  reduce_only?: boolean;
  error?: string;
};

type GateTrade = {
  trade_id?: string;
  order_id?: string;
  contract?: string;
  size?: number;
  price?: string;
  fill_price?: string;
  role?: string;
  create_time?: number;
  error?: string;
};

// 前端视图枚举，对应顶部导航标签。
type AppView = 'overview' | 'analysts' | 'predictions' | 'agent' | 'memory' | 'square' | 'mock_trade' | 'settings';

// 默认 API 前缀为 /bit，可通过 Vite 环境变量覆盖。
const API_BASE = import.meta.env.VITE_API_BASE_URL || '/bit';
const MARKET_INTERVALS = ['1m', '5m', '15m', '1h', '4h', '1d'];
const APP_VIEWS: { id: AppView; label: string; description: string }[] = [
  { id: 'overview', label: '总览', description: '关键指标与今日待办' },
  { id: 'analysts', label: '分析师数据', description: '评分与预测数据' },
  { id: 'predictions', label: '预测验证', description: '预测、验证与回放' },
  { id: 'agent', label: 'Agent 与报告', description: '运行记录、日报与人工确认' },
  { id: 'memory', label: '市场记忆', description: '活跃记忆与历史信号' },
  { id: 'square', label: '广场热门', description: 'Gate 广场热帖与观点' },
  { id: 'mock_trade', label: '模拟交易', description: 'Testnet 模拟账户与 AI 建议下单' },
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

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map((item) => String(item)) : [];
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

function gateTaskText(task: string): string {
  return {
    gate_btc_contract_sync: 'BTC 合约',
    gate_news_sync: 'Gate 资讯',
    gate_square_hot_sync: 'Square 热门',
    gate_square_user_sync: 'Square 关注',
    gate_info_sync: 'Gate Info',
    market_sentiment_build: '市场情绪',
    market_memory_compact: '市场记忆'
  }[task] || task;
}

function gateSyncResultText(task: string, value: unknown): string {
  const result = value && typeof value === 'object' ? value as GateSyncTaskResult : {};
  const data = result.data && typeof result.data === 'object' ? result.data : result;
  const error = result.error_message || result.error || data.error;
  const label = gateTaskText(task);
  if (error || result.status === 'failed') return `${label}失败：${String(error || '未知错误')}`;
  if (data.synced === false) {
    const reason = String(data.reason || data.error || '无可用数据');
    if (reason === 'no followed users configured') return `${label}已跳过：未配置关注用户`;
    return `${label}未同步：${reason}`;
  }
  if (task === 'gate_square_hot_sync') {
    const count = typeof data.count === 'number' ? data.count : Number(data.count || 0);
    const tool = data.tool ? `，工具 ${String(data.tool)}` : '';
    const translated = data.translated_count != null ? Number(data.translated_count) : 0;
    const translateNote = translated > 0 ? `，其中 ${translated} 条为英文翻译` : '';
    return count > 0 ? `${label}新增/更新 ${count} 条热门贴${tool}${translateNote}` : `${label}同步成功，但没有拿到热门贴${tool}`;
  }
  if (typeof data.count === 'number' || data.count != null) return `${label}完成：${String(data.count)} 条`;
  return `${label}完成`;
}

function gateSyncSummary(results: Record<string, unknown>): string {
  return Object.entries(results).map(([task, result]) => gateSyncResultText(task, result)).join('；');
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
  return { bullish: '偏多', bearish: '偏空', open_long: '偏多', open_short: '偏空', observe: '观望' }[value || ''] || value || '-';
}

function sentimentText(value?: string): string {
  return { extreme_greed: '极度贪婪', greed: '贪婪', neutral: '中性', fear: '恐惧', extreme_fear: '极度恐惧', bullish: '看涨', bearish: '看跌', mixed: '混合' }[value || ''] || value || '-';
}

function memoryTypeText(value?: string): string {
  return { market_sentiment_memory: '情绪信号', btc_trend_memory: 'BTC 趋势', btc_contract_memory: '合约信号', event_memory: '事件记忆', risk_memory: '风险信号' }[value || ''] || value || '-';
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
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [reviews, setReviews] = useState<HumanReview[]>([]);
  const [reports, setReports] = useState<AgentReport[]>([]);
  const [verificationResults, setVerificationResults] = useState<VerificationResult[]>([]);
  const [settings, setSettings] = useState<SettingItem[]>([]);
  const [scheduler, setScheduler] = useState<SchedulerStatus | null>(null);
  const [marketInterval, setMarketInterval] = useState('1h');
  const [selectedReview, setSelectedReview] = useState<HumanReview | null>(null);
  const [immediateReview, setImmediateReview] = useState<HumanReview | null>(null);
  const [selectedVerification, setSelectedVerification] = useState<VerificationResult | null>(null);
  const [editingPrediction, setEditingPrediction] = useState<Prediction | null>(null);
  const [predictionEditForm, setPredictionEditForm] = useState<PredictionEditForm | null>(null);
  const [predictionReplay, setPredictionReplay] = useState<PredictionReplay | null>(null);
  const [agentReplay, setAgentReplay] = useState<AgentRunReplay | null>(null);
  const [analystName, setAnalystName] = useState('');
  const [analystDropdownOpen, setAnalystDropdownOpen] = useState(false);
  const [content, setContent] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [showProjectNotice, setShowProjectNotice] = useState(true);
  const [streamEvents, setStreamEvents] = useState<AgentStreamEvent[]>([]);
  const [streamConnected, setStreamConnected] = useState(false);
  const [debugPanelCollapsed, setDebugPanelCollapsed] = useState(true);
  const [expandedEventId, setExpandedEventId] = useState<number | null>(null);
  const streamListRef = useRef<HTMLDivElement>(null);
  const [verifiedPredictionsExpanded, setVerifiedPredictionsExpanded] = useState(false);
  const [btcContract, setBtcContract] = useState<BtcContract | null>(null);
  const [sentiment, setSentiment] = useState<SentimentSnapshot | null>(null);
  const [memories, setMemories] = useState<MarketMemory[]>([]);
  const [squarePosts, setSquarePosts] = useState<SquarePost[]>([]);
  const [gateStatus, setGateStatus] = useState<GateSourceStatus | null>(null);
  const [sourceAccounts, setSourceAccounts] = useState<SourceAccount[]>([]);
  const [squareFilter, setSquareFilter] = useState<'all' | 'hot' | 'followed'>('all');
  const [tradeForm, setTradeForm] = useState<{ direction: string; price_type: string; price: string; amount_usdt: string }>({ direction: 'long', price_type: 'market', price: '', amount_usdt: '' });
  const [adviceLoading, setAdviceLoading] = useState(false);
  const [gateFuturesAccount, setGateFuturesAccount] = useState<GateFuturesAccount | null>(null);
  const [gatePositions, setGatePositions] = useState<GatePosition[]>([]);
  const [gateOrders, setGateOrders] = useState<GateOrder[]>([]);
  const [gateTrades, setGateTrades] = useState<GateTrade[]>([]);

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
        runData,
        reviewData,
        reportData,
        verificationData,
        settingsData,
        schedulerData,
        contractData,
        sentimentData,
        memoryData,
        squareData,
        gateStatusData,
        sourceAccountData,
      ] = await Promise.all([
        requestJson<Dashboard>('/api/dashboard'),
        requestJson<MarketRow[]>(`/api/market?interval=${marketInterval}&limit=120`),
        requestJson<Analyst[]>('/api/analysts'),
        requestJson<Prediction[]>('/api/predictions'),
        requestJson<AgentRun[]>('/api/agent/runs'),
        requestJson<HumanReview[]>('/api/reviews?status=pending'),
        requestJson<AgentReport[]>('/api/reports'),
        requestJson<VerificationResult[]>('/api/verification-results'),
        requestJson<{ items: SettingItem[] }>('/api/settings'),
        requestJson<SchedulerStatus>('/api/scheduler/status'),
        requestJson<BtcContract>('/api/market/btc-contract').catch(() => null),
        requestJson<SentimentSnapshot>('/api/sentiment/market').catch(() => null),
        requestJson<MarketMemory[]>('/api/memory?limit=50').catch(() => []),
        requestJson<SquarePost[]>('/api/square/hot?limit=30').catch(() => []),
        requestJson<GateSourceStatus>('/api/sources/gate/status').catch(() => null),
        requestJson<SourceAccount[]>('/api/sources/gate/accounts').catch(() => [])
      ]);
      setDashboard(dashboardData);
      setMarketRows(marketData);
      setAnalysts(analystData);
      setPredictions(predictionData);
      setVerifiedPredictionsExpanded(false);
      setAgentRuns(runData);
      setReviews(reviewData);
      setReports(reportData);
      setVerificationResults(verificationData);
      setSettings(settingsData.items);
      setScheduler(schedulerData);
      setBtcContract(contractData);
      setSentiment(sentimentData);
      setMemories(memoryData as MarketMemory[]);
      setSquarePosts(squareData as SquarePost[]);
      setGateStatus(gateStatusData);
      setSourceAccounts(sourceAccountData as SourceAccount[]);
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

  // 切换到模拟交易视图时自动刷新 Gate 数据
  useEffect(() => {
    if (activeView === 'mock_trade') {
      void loadGateData();
    }
  }, [activeView]);

  // 新事件到达时自动滚动到调试列表顶部
  useEffect(() => {
    if (streamListRef.current && !debugPanelCollapsed) {
      streamListRef.current.scrollTop = 0;
    }
  }, [streamEvents, debugPanelCollapsed]);

  useEffect(() => {
    // 通过 SSE 订阅 Agent 节点输出和心跳事件。传入当前最大 id，只接收新事件，不加载历史。
    const afterId = streamEvents.length > 0 ? Math.max(...streamEvents.map((e) => e.id)) : 0;
    const source = new EventSource(`${API_BASE}/api/agent/stream?after_id=${afterId}`);
    source.addEventListener('open', () => {
      setStreamConnected(true);
    });
    source.addEventListener('agent_output', (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as AgentStreamEvent;
      appendStreamEvent(payload);
      if (payload.node_name) {
        setDebugPanelCollapsed(false);
      }
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

  const fetchTradeAdvice = async () => {
    setAdviceLoading(true);
    appendStreamEvent({ id: Date.now(), type: 'local', message: '开始生成 AI 交易建议…', created_at: new Date().toISOString() });
    setDebugPanelCollapsed(false);
    try {
      const advice = await requestJson<TradeAdvice>('/api/mock-trade/advice', { method: 'POST' });
      if (advice.success) {
        setTradeForm({
          direction: advice.suggested_direction === 'short' ? 'short' : 'long',
          price: '',
          price_type: advice.suggested_price_type || 'market',
          amount_usdt: String(advice.suggested_size || ''),
        });
        setMessage(`AI 建议：${advice.suggested_direction === 'long' ? '开多' : advice.suggested_direction === 'short' ? '开空' : '观望'} ${advice.suggested_size || ''} USDT · ${advice.reason}`);
        appendStreamEvent({ id: Date.now() + 1, type: 'local', message: `AI 建议：${advice.suggested_direction === 'long' ? '开多' : advice.suggested_direction === 'short' ? '开空' : '观望'} ${advice.suggested_size || ''} USDT`, created_at: new Date().toISOString(), output: { suggested_direction: advice.suggested_direction, suggested_size: advice.suggested_size, suggested_price_type: advice.suggested_price_type, reason: advice.reason } });
      } else {
        setMessage(advice.errors?.[0] || 'AI 建议生成失败');
        appendStreamEvent({ id: Date.now() + 1, type: 'error', message: `AI 建议生成失败：${advice.errors?.[0] || '未知错误'}`, created_at: new Date().toISOString() });
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '获取建议失败');
      appendStreamEvent({ id: Date.now() + 2, type: 'error', message: error instanceof Error ? error.message : '获取建议失败', created_at: new Date().toISOString() });
    } finally {
      setAdviceLoading(false);
    }
  };

  const executeTrade = async () => {
    const amt = Number(tradeForm.amount_usdt) || 0;
    if (amt <= 0) {
      setMessage('请输入数量（USDT）');
      return;
    }
    const priceTypeLabel = tradeForm.price_type === 'market' ? '市价' : '限价';
    const priceLabel = tradeForm.price_type === 'limit' && tradeForm.price ? `@ ${tradeForm.price} USDT` : '';
    if (!window.confirm(`确认在 Testnet 执行 ${tradeForm.direction === 'long' ? '开多' : '开空'} ${amt} USDT ${priceTypeLabel}单 ${priceLabel}？`)) return;
    setLoading(true);
    try {
      const result = await requestJson<{ success: boolean; order_id?: string; error?: string; balance?: number }>('/api/mock-trade/execute', {
        method: 'POST',
        body: JSON.stringify({ direction: tradeForm.direction, size: 1, price_type: tradeForm.price_type, amount_usdt: amt, price: Number(tradeForm.price) || 0 })
      });
      if (result.success) {
        setMessage(`Testnet 下单成功：订单 ${result.order_id}，余额 ${result.balance}`);
        appendStreamEvent({ id: Date.now(), type: 'local', message: `Testnet 下单成功：${result.order_id}`, created_at: new Date().toISOString(), output: result });
      } else {
        setMessage(`下单失败：${result.error || '未知错误'}`);
        appendStreamEvent({ id: Date.now(), type: 'error', message: `Testnet 下单失败：${result.error || '未知错误'}`, created_at: new Date().toISOString() });
      }
      await loadAll();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '下单失败');
    } finally {
      setLoading(false);
    }
  };


  const loadGateData = async () => {
    try {
      const [accountData, positionsData, ordersData, tradesData] = await Promise.all([
        requestJson<GateFuturesAccount>('/api/gate/account').catch(() => null),
        requestJson<GatePosition[]>('/api/gate/positions').catch(() => []),
        requestJson<GateOrder[]>('/api/gate/orders?status=open').catch(() => []),
        requestJson<GateTrade[]>('/api/gate/trades').catch(() => []),
      ]);
      setGateFuturesAccount(accountData);
      setGatePositions(positionsData);
      setGateOrders(ordersData);
      setGateTrades(tradesData);
    } catch { /* ignore */ }
  };

  const cancelGateOrder = async (orderId: string) => {
    if (!window.confirm(`确认撤销订单 ${orderId}？`)) return;
    try {
      const result = await requestJson<{ order_id?: string; status?: string; error?: string }>('/api/gate/orders/cancel', {
        method: 'POST',
        body: JSON.stringify({ order_id: orderId }),
      });
      if (result.error) {
        setMessage(`撤单失败：${result.error}`);
      } else {
        setMessage(`订单 ${result.order_id} 已撤销，状态 ${result.status}`);
        await loadGateData();
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '撤单失败');
    }
  };

  const updateGateLeverage = async (leverage: string) => {
    if (!window.confirm(`确认调整杠杆为 ${leverage}x？`)) return;
    try {
      const result = await requestJson<{ contract?: string; leverage?: string; error?: string }>('/api/gate/positions/leverage', {
        method: 'POST',
        body: JSON.stringify({ leverage }),
      });
      if (result.error) {
        setMessage(`调杠杆失败：${result.error}`);
      } else {
        setMessage(`${result.contract} 杠杆已调整为 ${result.leverage}x`);
        await loadGateData();
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '调杠杆失败');
    }
  };

  const runAgent = async () => {
    setLoading(true);
    setMessage('');
    appendStreamEvent({ id: Date.now(), type: 'local', message: '开始运行 Agent 分析', created_at: new Date().toISOString() });
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
      const [result, evidence, reflection] = await Promise.all([
        requestJson<AgentRunReplay>(`/api/agent/runs/${agentRunId}/replay`),
        requestJson<AgentEvidence>(`/api/agent/runs/${agentRunId}/evidence`).catch(() => undefined),
        requestJson<AgentReflection>(`/api/agent/runs/${agentRunId}/reflection`).catch(() => undefined)
      ]);
      setAgentReplay({ ...result, evidence, reflection });
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

  const runGateSync = async (tasks: string[]) => {
    setLoading(true);
    try {
      const result = await requestJson<Record<string, unknown>>('/api/sources/gate/sync', {
        method: 'POST',
        body: JSON.stringify({ tasks })
      });
      const summary = gateSyncSummary(result);
      appendStreamEvent({
        id: Date.now(),
        type: 'local',
        message: `Gate 同步结果：${summary}`,
        created_at: new Date().toISOString(),
        output: result
      });
      setDebugPanelCollapsed(false);
      await loadAll();
      setMessage(`Gate 数据同步完成：${summary}`);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Gate 数据同步失败';
      appendStreamEvent({ id: Date.now(), type: 'error', message, created_at: new Date().toISOString() });
      setDebugPanelCollapsed(false);
      setMessage(message);
    } finally {
      setLoading(false);
    }
  };

  const market = dashboard?.market;
  const latestMarketRow = marketRows[marketRows.length - 1];
  const latestRun = dashboard?.latest_agent_run;
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
            <span>短期 {formatNumber(analyst.short_win_rate)}%</span>
            <span>中期 {formatNumber(analyst.medium_win_rate)}%</span>
            <span>长期 {formatNumber(analyst.long_win_rate)}%</span>
            <span>预测 {formatNumber(analyst.prediction_count, 0)} 条</span>
          </div>
        </article>
      ))}
      {!rankedAnalysts.length && <div className="empty">暂无分析师。</div>}
    </div>
  );
  const agentRunList = (
    <div className="run-list">
      {agentRuns.slice(0, 5).map((run) => (
        <article className="run-card" key={run.id}>
          <div className="run-head">
            <strong>{decisionText(run.decision)}</strong>
            <span>{formatDate(run.created_at)}</span>
          </div>
          <p>{run.market_summary}</p>
          <p>{run.opinion_summary}</p>
          <em>{run.output?.analysis_event || run.risk}</em>
          {run.output?.react_tools_used && run.output.react_tools_used.length > 0 && (
            <div className="agent-tools-row">
              <span className="agent-label">ReAct 工具</span>
              {run.output.react_tools_used.map((tool) => <span className="tag sideways" key={tool}>{tool}</span>)}
            </div>
          )}
          {run.output?.evidence_conflict?.has_conflict && (
            <div className="agent-reflection-row conflict">
              <span className="agent-label">证据冲突</span>
              <span>{run.output.evidence_conflict.summary || '多源证据存在冲突'}</span>
              {run.output.evidence_conflict.conflict_points?.map((point, i) => <em key={i}>{point}</em>)}
            </div>
          )}
          {run.output?.reflection && !run.output.reflection.is_adequate && (
            <div className="agent-reflection-row">
              <span className="agent-label">反思修正</span>
              <span>{run.output.reflection.correction_suggestion || '证据不充分'}</span>
              {run.output.reflection.weak_points?.map((wp, i) => <em key={i}>{wp}</em>)}
            </div>
          )}
          <div className="inline-actions">
            <button className="ghost-button tiny" type="button" onClick={() => loadAgentReplay(run.id)} disabled={loading}>证据/反思/节点</button>
          </div>
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
          <h1>BTC 分析师追踪与趋势分析系统</h1>
          <p>集中管理实时行情、分析师观点、预测验证和BTC行情分析报告。</p>
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
            <div className="stream-list" ref={streamListRef}>
              {streamEvents.map((event) => {
                const isExpanded = expandedEventId === event.id;
                const hasOutput = event.output && Object.keys(event.output).length > 0;
                return (
                  <article
                    className={`stream-item ${event.status === 'failed' || event.type === 'error' ? 'failed' : ''} ${isExpanded ? 'expanded' : ''} ${hasOutput ? 'clickable' : ''}`}
                    key={`${event.type || 'event'}-${event.id}-${event.created_at || ''}`}
                  >
                    <div
                      className="stream-meta"
                      onClick={() => hasOutput && setExpandedEventId(isExpanded ? null : event.id)}
                    >
                      <span>{streamEventTitle(event)} {event.status && event.status !== 'success' ? `· ${event.status}` : ''}</span>
                      <em>{formatDate(event.created_at || undefined)}</em>
                    </div>
                    <p onClick={() => hasOutput && setExpandedEventId(isExpanded ? null : event.id)}>{event.message}</p>
                    {isExpanded && hasOutput && (
                      <pre className="stream-detail" onClick={(e) => e.stopPropagation()}>{JSON.stringify(event.output, null, 2)}</pre>
                    )}
                  </article>
                );
              })}
              {!streamEvents.length && <div className="empty">等待 AI 节点输出、报告生成或观点解析。</div>}
            </div>
          </div>
        )}
      </section>

      <section className="metrics-grid">
        <MetricCard title="BTC 实时价格" value={`$${formatNumber(displayPrice)}`} desc={`${liveSourceText} · 24h ${formatNumber(market?.change_24h)}%`} icon={<Activity size={22} />} />
        <MetricCard title="待验证预测" value={formatNumber(dashboard?.pending_prediction_count, 0)} desc={`到期 ${formatNumber(dashboard?.due_prediction_count, 0)} 条`} icon={<Clock3 size={22} />} />
        <MetricCard title="Agent 最新动作" value={decisionText(latestRun?.decision)} desc={latestRun?.risk || '尚未运行'} icon={<ShieldAlert size={22} />} />
        <MetricCard title="分析师数量" value={formatNumber(analysts.length, 0)} desc={`Top 分 ${formatNumber(topAnalysts[0]?.total_score)} · 报告 ${formatNumber(reports.length, 0)} 篇`} icon={<LineChart size={22} />} />
      </section>

      {activeView === 'overview' && (
      <section className="gate-context-row">
        <div className="gate-card">
          <div className="gate-card-head">
            <Database size={16} />
            <strong>BTC 合约状态</strong>
          </div>
          {btcContract && btcContract.last_price ? (
            <div className="gate-card-stats">
              <span>标记价 ${formatNumber(btcContract.last_price)}</span>
              <span>资金费率 {formatNumber(btcContract.funding_rate, 6)}</span>
              <span>持仓量 {formatNumber(btcContract.open_interest, 0)}</span>
              <span>24h {formatNumber(btcContract.change_pct_24h)}%</span>
            </div>
          ) : <div className="empty">暂无合约数据</div>}
        </div>
        <div className="gate-card">
          <div className="gate-card-head">
            <Flame size={16} />
            <strong>市场情绪</strong>
          </div>
          {sentiment && sentiment.overall_sentiment ? (
            <div className="gate-card-stats">
              <span>情绪 {sentimentText(sentiment.overall_sentiment)}</span>
              <span>看涨 {formatNumber((sentiment.bull_ratio || 0) * 100)}%</span>
              <span>看跌 {formatNumber((sentiment.bear_ratio || 0) * 100)}%</span>
              <span>快照 {formatDate(sentiment.snapshot_time)}</span>
            </div>
          ) : <div className="empty">暂无情绪快照</div>}
        </div>
        <div className="gate-card">
          <div className="gate-card-head">
            <BrainCircuit size={16} />
            <strong>最新记忆 · {memories.length} 条</strong>
          </div>
          {memories.length ? (
            <div className="gate-memory-list">
              {memories.slice(0, 5).map((mem) => (
                <div className="gate-memory-item" key={mem.id}>
                  <span className={`tag ${mem.sentiment || ''}`}>{sentimentText(mem.sentiment)}</span>
                  <span>{mem.title}</span>
                  <em>{formatNumber(mem.importance, 2)}</em>
                </div>
              ))}
            </div>
          ) : <div className="empty">暂无市场记忆</div>}
        </div>
        <div className="gate-card">
          <div className="gate-card-head">
            <Database size={16} />
            <strong>Gate 数据源状态</strong>
          </div>
          {gateStatus ? (
            <div className="gate-card-stats">
              {Object.entries(gateStatus).slice(0, 6).map(([key, value]) => {
                const item = asRecord(value);
                return <span key={key}>{GATE_STATUS_LABELS[key] || key}：{formatNumber(Number(item.count || 0), 0)} · {formatDate(String(item.latest || ''))}</span>;
              })}
            </div>
          ) : <div className="empty">暂无数据源状态</div>}
        </div>
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
          <label className="analyst-combobox-label">
            分析师名称
            <div className="analyst-combobox">
              <input
                value={analystName}
                onChange={(event) => { setAnalystName(event.target.value); setAnalystDropdownOpen(true); }}
                onFocus={() => setAnalystDropdownOpen(true)}
                onBlur={() => setTimeout(() => setAnalystDropdownOpen(false), 150)}
                placeholder="选择或输入分析师名称"
              />
              {analystDropdownOpen && analysts.filter(a => a.name.toLowerCase().includes(analystName.toLowerCase().trim()) || !analystName.trim()).length > 0 && (
                <ul className="analyst-dropdown">
                  {analysts
                    .filter(a => a.name.toLowerCase().includes(analystName.toLowerCase().trim()) || !analystName.trim())
                    .map(a => (
                      <li key={a.id} onMouseDown={() => { setAnalystName(a.name); setAnalystDropdownOpen(false); }}>{a.name}<span className="analyst-dropdown-score">评分 {formatNumber(a.total_score)}</span></li>
                    ))}
                </ul>
              )}
            </div>
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
            <p>完整展示分析师评分、胜率、稳定性与预测数据。</p>
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
              <h2>BTC行情分析报告</h2>
              <p>最新一次 BTC 行情分析，包含市场摘要、分析师共识和风险提示。</p>
            </div>
            <LineChart />
          </div>
          <div className="run-list">
            {reports.length > 0 ? (() => {
              const report = reports[0];
              return (
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
                  </div>
                  {report.data?.analyst_consensus && <p>{report.data.analyst_consensus}</p>}
                  {report.data?.recent_prediction_review && <p>{report.data.recent_prediction_review}</p>}
                  {report.data?.prediction_change_review && <p>{report.data.prediction_change_review}</p>}
                  {report.data?.contract_status && <p className="gate-report-line">{report.data.contract_status}</p>}
                  {report.data?.sentiment_status && <p className="gate-report-line">{report.data.sentiment_status}</p>}
                  {report.data?.memory_status && <p className="gate-report-line">{report.data.memory_status}</p>}
                  {!!report.data?.sentiment_topics?.length && <p className="gate-report-line">情绪主题：{report.data.sentiment_topics.join('、')}</p>}
                  {!!report.data?.memory_summary?.length && <p className="gate-report-line">记忆摘要：{report.data.memory_summary.join('；')}</p>}
                  {reportScenarios(report.data?.scenarios).slice(0, 3).map((scenario, index) => (
                    <p key={`${report.id}-scenario-${index}`}><strong>{scenario.scenario || '情景'}</strong>：{scenario.description || '-'}</p>
                  ))}
                  {!!report.data?.risk_warnings?.length && <p>风险提示：{report.data.risk_warnings.join('；')}</p>}
                  <em>{report.data?.disclaimer || '仅用于信息整理，不构成投资建议。'}</em>
                </article>
              );
            })() : <div className="empty">暂无报告，可点击"生成日报"。</div>}
          </div>
        </div>
      </section>

      <section className={activeView === 'agent' ? 'panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>Agent 运行记录</h2>
            <p>每次分析保留输入摘要、输出和节点过程。</p>
          </div>
          <Bot />
        </div>
        {agentRunList}
      </section>

      <section className={activeView === 'agent' ? 'two-columns' : 'hidden'}>
        <div className="panel">
          <div className="panel-title compact">
            <div>
              <h2>BTC行情分析报告</h2>
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
                </div>
                {report.data?.analyst_consensus && <p>{report.data.analyst_consensus}</p>}
                {report.data?.recent_prediction_review && <p>{report.data.recent_prediction_review}</p>}
                {report.data?.prediction_change_review && <p>{report.data.prediction_change_review}</p>}
                {report.data?.contract_status && <p className="gate-report-line">{report.data.contract_status}</p>}
                {report.data?.sentiment_status && <p className="gate-report-line">{report.data.sentiment_status}</p>}
                {report.data?.memory_status && <p className="gate-report-line">{report.data.memory_status}</p>}
                {!!report.data?.sentiment_topics?.length && <p className="gate-report-line">情绪主题：{report.data.sentiment_topics.join('、')}</p>}
                {!!report.data?.memory_summary?.length && <p className="gate-report-line">记忆摘要：{report.data.memory_summary.join('；')}</p>}
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

      <section className={activeView === 'memory' ? 'panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>市场记忆</h2>
            <p>按重要性排序，展示 Agent 活跃记忆。共 {memories.length} 条。</p>
          </div>
          <BrainCircuit />
        </div>
        <div className="memory-grid">
          {memories.map((mem) => (
            <article className="memory-card" key={mem.id}>
              <div className="memory-card-head">
                <span className={`tag ${mem.sentiment || ''}`}>{memoryTypeText(mem.memory_type)}</span>
                <span className="memory-importance">{formatNumber(mem.importance, 2)}</span>
              </div>
              <strong>{mem.title}</strong>
              <p>{mem.content}</p>
              <div className="memory-meta">
                <span>情绪 {sentimentText(mem.sentiment)}</span>
                {mem.expectation && <span>预期 {mem.expectation}</span>}
                <span>来源 {mem.source || '-'}</span>
                <span>{formatDate(mem.created_at)}</span>
                {mem.valid_until && <span>有效至 {formatDate(mem.valid_until)}</span>}
              </div>
            </article>
          ))}
          {!memories.length && <div className="empty">暂无活跃市场记忆。可在"系统设置"中触发记忆压缩任务。</div>}
        </div>
      </section>

      <section className={activeView === 'square' ? 'panel' : 'hidden'}>
        <div className="panel-title compact">
          <div>
            <h2>广场热门</h2>
            <p>Gate 广场热帖与关注用户观点。共 {squarePosts.length} 条。</p>
          </div>
          <div className="inline-actions">
            <button className={`ghost-button tiny ${squareFilter === 'all' ? 'active-filter' : ''}`} type="button" onClick={() => setSquareFilter('all')}>全部</button>
            <button className={`ghost-button tiny ${squareFilter === 'hot' ? 'active-filter' : ''}`} type="button" onClick={() => setSquareFilter('hot')}>热门</button>
            <button className={`ghost-button tiny ${squareFilter === 'followed' ? 'active-filter' : ''}`} type="button" onClick={() => setSquareFilter('followed')}>关注</button>
          </div>
        </div>
        <div className="square-list">
          {squarePosts
            .filter((post) => {
              if (squareFilter === 'hot') return post.is_hot_post === 1;
              if (squareFilter === 'followed') return post.is_followed_user === 1;
              return true;
            })
            .map((post) => (
            <article className="square-card" key={post.id}>
              <div className="run-head">
                <strong>{post.author || '匿名用户'}</strong>
                <span>{formatDate(post.created_at)}</span>
              </div>
              <p>{post.content}</p>
              <div className="square-meta">
                {post.is_hot_post === 1 && <span className="tag bearish">🔥 热门</span>}
                {post.is_followed_user === 1 && <span className="tag bullish">⭐ 关注</span>}
                {post.hot_score != null && <span>热度 {formatNumber(post.hot_score, 0)}</span>}
                {post.repost_count != null && <span>转发 {formatNumber(post.repost_count, 0)}</span>}
              </div>
            </article>
          ))}
          {!squarePosts.length && <div className="empty">暂无广场帖子。可在"系统设置"中触发广场同步任务。</div>}
        </div>
      </section>

      <section className={activeView === 'mock_trade' ? 'mock-trade-panel' : 'hidden'}>
        <div className="mock-trade-header">
          <div className="panel-title compact">
            <div>
              <h2>模拟交易</h2>
              <p>Gate Testnet 模拟账户，AI 建议下单。</p>
            </div>
            <div className="inline-actions">
              <button className="ghost-button" type="button" onClick={loadGateData} disabled={loading}>刷新 Gate 数据</button>
            </div>
          </div>
        </div>
        <div className="mock-trade-grid">
          <div className="panel account-panel">
            <div className="panel-title compact" style={{ marginTop: '1rem' }}>
              <h3>Gate Testnet 实时</h3>
            </div>
            {gateFuturesAccount && !gateFuturesAccount.error ? (
              <div className="account-card">
                <div className="account-row"><span>可用余额</span><strong>{gateFuturesAccount.available} USDT</strong></div>
                <div className="account-row"><span>总权益</span><span>{gateFuturesAccount.total} USDT</span></div>
                <div className="account-row"><span>仓位保证金</span><span>{gateFuturesAccount.position_margin} USDT</span></div>
              </div>
            ) : <div className="empty">{gateFuturesAccount?.error || '未连接 Gate Testnet'}</div>}
            {gatePositions.length > 0 && !gatePositions[0]?.error ? (
              <div className="position-list" style={{ marginTop: '0.5rem' }}>
                {gatePositions.map((pos, idx) => (
                  <div className="position-card" key={idx}>
                    <span className={`tag ${Number(pos.size) > 0 ? 'long' : 'short'}`}>{Number(pos.size) > 0 ? '做多' : '做空'}</span>
                    <span>{pos.contract} {Math.abs(pos.size)} 张</span>
                    <span>入场 {pos.entry_price}</span>
                    <span>标记 {pos.mark_price}</span>
                    {pos.liq_price && <span>强平 {pos.liq_price}</span>}
                    <span className={Number(pos.unrealised_pnl) >= 0 ? 'up' : 'down'}>未实现 {pos.unrealised_pnl}</span>
                    <span>杠杆 {pos.leverage}x</span>
                  </div>
                ))}
                <div className="inline-actions" style={{ marginTop: '0.5rem' }}>
                  {[1, 3, 5, 10, 20].map((lev) => (
                    <button key={lev} className="ghost-button tiny" type="button" onClick={() => updateGateLeverage(String(lev))}>{lev}x</button>
                  ))}
                </div>
              </div>
            ) : <div className="empty">Gate 无持仓</div>}
          </div>
          <div className="panel trade-form-panel">
            <div className="panel-title compact"><h3>开单</h3></div>
            <div className="trade-form">
              <label>
                方向
                <div className="direction-radio">
                  <label><input type="radio" name="trade-direction" value="long" checked={tradeForm.direction === 'long'} onChange={() => setTradeForm({ ...tradeForm, direction: 'long' })} /> 开多</label>
                  <label><input type="radio" name="trade-direction" value="short" checked={tradeForm.direction === 'short'} onChange={() => setTradeForm({ ...tradeForm, direction: 'short' })} /> 开空</label>
                </div>
              </label>
              <label>
                价格类型
                <select value={tradeForm.price_type} onChange={(event) => setTradeForm({ ...tradeForm, price_type: event.target.value })}>
                  <option value="market">市价</option>
                  <option value="limit">限价</option>
                </select>
              </label>
              {tradeForm.price_type === 'limit' && (
                <label>
                  委托价格（USDT）
                  <input type="number" min="0" step="0.01" value={tradeForm.price} onChange={(event) => setTradeForm({ ...tradeForm, price: event.target.value })} placeholder="输入限价" />
                </label>
              )}
              <label>
                数量（USDT）
                <input type="number" min="0" step="0.01" value={tradeForm.amount_usdt} onChange={(event) => setTradeForm({ ...tradeForm, amount_usdt: event.target.value })} placeholder="输入 USDT 数量" />
              </label>
              <div className="slider-row">
                <label className="slider-label">快捷数量</label>
                <input
                  type="range"
                  min="0"
                  max="100"
                  step="1"
                  value={gateFuturesAccount && Number(gateFuturesAccount.available) > 0 ? Math.min(100, Math.round((Number(tradeForm.amount_usdt) || 0) / Number(gateFuturesAccount.available) * 100)) : 0}
                  onChange={(event) => {
                    const pct = Number(event.target.value);
                    const balance = Number(gateFuturesAccount?.available) || 0;
                    if (balance > 0) {
                      setTradeForm({ ...tradeForm, amount_usdt: String(Math.max(0, Math.round(balance * pct / 100 * 100) / 100)) });
                    }
                  }}
                />
                <div className="slider-marks">
                  <button type="button" className="slider-mark" onClick={() => {
                    const balance = Number(gateFuturesAccount?.available) || 0;
                    if (balance > 0) setTradeForm({ ...tradeForm, amount_usdt: String(Math.round(balance * 0.25 * 100) / 100) });
                  }}>25%</button>
                  <button type="button" className="slider-mark" onClick={() => {
                    const balance = Number(gateFuturesAccount?.available) || 0;
                    if (balance > 0) setTradeForm({ ...tradeForm, amount_usdt: String(Math.round(balance * 0.5 * 100) / 100) });
                  }}>50%</button>
                  <button type="button" className="slider-mark" onClick={() => {
                    const balance = Number(gateFuturesAccount?.available) || 0;
                    if (balance > 0) setTradeForm({ ...tradeForm, amount_usdt: String(Math.round(balance * 0.75 * 100) / 100) });
                  }}>75%</button>
                </div>
              </div>
              <div className="trade-actions">
                <button className="primary-button" type="button" onClick={executeTrade} disabled={loading}><Send size={16} /> 确认交易</button>
                <button className="ghost-button" type="button" onClick={fetchTradeAdvice} disabled={adviceLoading || loading}><BrainCircuit size={16} /> {adviceLoading ? '建议生成中...' : 'AI 交易建议'}</button>
              </div>
            </div>
          </div>
          <div className="panel history-panel">
            <div className="panel-title compact"><h3>Gate 挂单</h3></div>
            {gateOrders.length > 0 && !gateOrders[0]?.error ? (
              <table className="trade-history-table">
                <thead>
                  <tr><th>订单ID</th><th>合约</th><th>方向</th><th>数量</th><th>价格</th><th>剩余</th><th>TIF</th><th>操作</th></tr>
                </thead>
                <tbody>
                  {gateOrders.map((order) => (
                    <tr key={order.order_id}>
                      <td>{order.order_id}</td>
                      <td>{order.contract}</td>
                      <td><span className={`tag ${order.size > 0 ? 'long' : 'short'}`}>{order.size > 0 ? '多' : '空'}{order.is_close ? ' 平' : order.reduce_only ? ' 减' : ''}</span></td>
                      <td>{Math.abs(order.size)}</td>
                      <td>{order.price}</td>
                      <td>{order.left}</td>
                      <td>{order.tif}</td>
                      <td><button className="ghost-button tiny" type="button" onClick={() => cancelGateOrder(order.order_id)}>撤单</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="empty">无挂单</div>}
            <div className="panel-title compact" style={{ marginTop: '1rem' }}><h3>Gate 成交</h3></div>
            {gateTrades.length > 0 && !gateTrades[0]?.error ? (
              <table className="trade-history-table">
                <thead>
                  <tr><th>成交ID</th><th>合约</th><th>方向</th><th>数量</th><th>成交价</th><th>角色</th></tr>
                </thead>
                <tbody>
                  {gateTrades.map((trade, idx) => (
                    <tr key={trade.trade_id || idx}>
                      <td>{trade.trade_id}</td>
                      <td>{trade.contract}</td>
                      <td><span className={`tag ${(trade.size || 0) > 0 ? 'long' : 'short'}`}>{(trade.size || 0) > 0 ? '多' : '空'}</span></td>
                      <td>{Math.abs(trade.size || 0)}</td>
                      <td>{trade.fill_price || trade.price}</td>
                      <td>{trade.role}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="empty">无成交记录</div>}
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
            <button className="ghost-button" type="button" onClick={() => runSchedulerTask('daily_report')} disabled={loading}>生成日报</button>
            <button className="ghost-button" type="button" onClick={() => runGateSync(['gate_btc_contract_sync', 'market_sentiment_build'])} disabled={loading}>同步 Gate/情绪</button>
            <button className="ghost-button" type="button" onClick={() => runGateSync(['gate_square_hot_sync', 'gate_square_user_sync'])} disabled={loading}>同步 Square</button>
            <button className="ghost-button" type="button" onClick={() => runSchedulerTask('market_memory_compact')} disabled={loading}>保存市场记忆</button>
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
              <h2>Gate 数据源与账户映射</h2>
              <p>展示 Gate 同步状态与 Square 指定用户映射。</p>
            </div>
            <Database />
          </div>
          {gateStatus ? (
            <div className="settings-grid source-status-grid">
              {Object.entries(gateStatus).map(([key, value]) => {
                const item = asRecord(value);
                return (
                  <div className="setting-item" key={key}>
                    <strong>{GATE_STATUS_LABELS[key] || key}</strong>
                    <span>{formatNumber(Number(item.count || 0), 0)} 条</span>
                    <small>最新 {formatDate(String(item.latest || ''))}</small>
                  </div>
                );
              })}
            </div>
          ) : <div className="empty">暂无数据源状态。</div>}
          <div className="run-list source-account-list">
            {sourceAccounts.map((account, index) => (
              <article className="run-card" key={`${account.source_platform || 'source'}-${account.source_user_id || index}`}>
                <div className="run-head">
                  <strong>{account.display_name || account.source_user_id || '未命名账户'}</strong>
                  <span>{account.enabled ? '启用' : '停用'}</span>
                </div>
                <div className="mini-grid">
                  <span>平台 {account.source_platform || '-'}</span>
                  <span>用户 {account.source_user_id || '-'}</span>
                  <span>分析师 ID {account.analyst_id ?? '-'}</span>
                  <span>创建 {formatDate(account.created_at)}</span>
                </div>
              </article>
            ))}
            {!sourceAccounts.length && <div className="empty">暂无 Square 指定用户映射。</div>}
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
          {selectedVerification.report?.data?.context_snapshot && (
            <div className="context-snapshot">
              <h3>验证环境快照</h3>
              <div className="mini-grid">
                <span>Gate 上下文 {Object.keys(asRecord(selectedVerification.report.data.context_snapshot.gate_context)).length} 项</span>
                <span>市场摘要 {Object.keys(asRecord(selectedVerification.report.data.context_snapshot.market_summary)).length} 项</span>
              </div>
              <pre>{JSON.stringify(selectedVerification.report.data.context_snapshot, null, 2)}</pre>
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
              <h3>验证结果</h3>
              <pre>{JSON.stringify({ verification_result: predictionReplay.verification_result, verification_report: predictionReplay.verification_report }, null, 2)}</pre>
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
              <p>节点数 {agentReplay.nodes?.length || 0}</p>
            </div>
            <button className="ghost-button" type="button" onClick={() => setAgentReplay(null)}>关闭</button>
          </div>
          <div className="detail-grid">
            <div>
              <h3>Evidence</h3>
              <div className="mini-grid">
                <span>证据引用 {(agentReplay.evidence?.evidence_refs || []).length} 条</span>
                <span>节点快照 {(agentReplay.evidence?.node_runs || []).length} 个</span>
              </div>
              <pre>{JSON.stringify(agentReplay.evidence || {}, null, 2)}</pre>
            </div>
            <div>
              <h3>Reflection</h3>
              <div className="mini-grid">
                <span>充分性 {String(asRecord(agentReplay.reflection?.reflection).is_adequate ?? '-')}</span>
                <span>节点 {(agentReplay.reflection?.node_runs || []).length} 个</span>
              </div>
              <pre>{JSON.stringify(agentReplay.reflection || {}, null, 2)}</pre>
            </div>
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

    </main>
  );
}
