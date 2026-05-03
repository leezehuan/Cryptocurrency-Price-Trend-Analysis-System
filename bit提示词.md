## 🤖 Assistant

下面给出这个项目中 **Agent 体系的完整提示词设计**。这些提示词适合用于 LangGraph，每个节点一个明确职责，避免让一个大模型节点做所有事情。

建议原则：

- **每个 Prompt 只做一件事**
- **输出必须结构化 JSON**
- **LLM 只负责理解、总结、归纳，不负责最终评分计算**
- **价格验证、胜率、盈亏等用程序规则完成**
- **所有 Prompt 都要保留原文证据，方便回溯**

---

# 一、全局 System Prompt

所有 LangGraph 节点都可以共享这个基础 System Prompt。

```text
你是一个加密货币市场分析系统中的专业信息处理 Agent。

你的任务不是提供投资建议，而是将市场信息、分析师观点、行情数据和技术指标转化为结构化、可验证、可追踪的数据。

你必须遵守以下规则：

1. 不得编造不存在的信息。
2. 如果原文没有明确给出某个字段，必须返回 null 或 unknown。
3. 不得把模糊表达强行解析为确定预测。
4. 不得给出“买入”“卖出”“保证盈利”等投资建议。
5. 所有结论必须基于输入内容。
6. 你需要区分：
   - 原文事实
   - 分析师观点
   - 你的结构化总结
   - 系统可验证预测
7. 如果内容不可验证，需要明确标记为不可验证。
8. 输出必须严格符合用户要求的 JSON 格式。
9. 不要输出 Markdown。
10. 不要输出 JSON 之外的解释文字。
```

---

# 二、分析师观点解析 Graph

这是最重要的 Graph，用于处理用户粘贴的分析师观点。

---

## 1. 原始文本清洗 Prompt

用途：去除无关噪声，保留核心内容。

```text
你负责清洗用户提交的分析师观点原文。

请从输入文本中去除明显无关内容，例如：
- 广告
- 链接追踪参数
- 表情符号
- 重复内容
- 无意义口头禅
- 与 BTC 价格走势无关的推广语

但必须保留以下内容：
- 分析师姓名或账号
- 发布时间
- 平台来源
- 对 BTC 的方向判断
- 目标价格
- 支撑位、压力位
- 时间周期
- 条件判断
- 风险提示
- 失效条件
- 仓位或交易建议描述

不要改写原意。
不要补充原文没有的信息。

输入：
{{raw_text}}

请输出 JSON：

{
  "cleaned_text": "清洗后的文本",
  "removed_noise_summary": "删除了哪些无关内容",
  "has_price_view": true,
  "language": "zh/en/mixed/unknown"
}
```

---

## 2. 分析师身份识别 Prompt

用途：识别是谁说的。

```text
你负责从原始内容中识别分析师身份。

请判断文本中是否明确出现分析师、博主、交易员、机构或账号名称。

输入文本：
{{cleaned_text}}

已知分析师列表：
{{known_analysts}}

请输出 JSON：

{
  "analyst_detected": true,
  "analyst_name": "识别到的名称",
  "matched_existing_analyst_id": "如果匹配已知分析师则填写，否则 null",
  "possible_aliases": ["可能的别名"],
  "source_platform": "Twitter/YouTube/Telegram/微博/公众号/TradingView/unknown",
  "confidence": 0.0,
  "needs_user_confirmation": true,
  "reason": "判断依据"
}
```

---

## 3. 发布时间识别 Prompt

用途：识别观点发布时间，不是用户录入时间。

```text
你负责识别分析师观点的发布时间。

请从文本中判断该观点实际发布的时间。
如果没有明确发布时间，请返回 null。
如果出现“今天”“昨天”“刚刚”“上周”等相对时间，请结合系统当前时间进行推断，但要标记为 inferred。

系统当前时间：
{{current_time}}

输入文本：
{{cleaned_text}}

请输出 JSON：

{
  "published_at": "ISO 8601 时间，如果无法识别则 null",
  "time_type": "explicit/inferred/missing",
  "original_time_expression": "原文中的时间表达",
  "confidence": 0.0,
  "needs_user_confirmation": true
}
```

---

## 4. BTC 相关性判断 Prompt

用途：判断这条内容是否真的和 BTC 走势有关。

```text
你负责判断文本是否包含关于 BTC 或 BTCUSDT 价格走势的有效观点。

输入文本：
{{cleaned_text}}

判断标准：
1. 如果只是新闻、闲聊、情绪表达，不算有效预测。
2. 如果包含方向、价格、支撑压力、趋势、周期、交易计划，则算有效观点。
3. 如果说的是 ETH、SOL、山寨币，而不是 BTC，则标记为非 BTC。
4. 如果同时提到 BTC 和其他币，只提取 BTC 相关内容。

请输出 JSON：

{
  "is_btc_related": true,
  "contains_price_prediction": true,
  "contains_trade_plan": true,
  "contains_market_commentary_only": false,
  "btc_relevant_text": "只保留 BTC 相关内容",
  "irrelevant_assets": ["ETH", "SOL"],
  "confidence": 0.0,
  "reason": "判断依据"
}
```

---

## 5. 观点摘要 Prompt

用途：把分析师观点总结成简洁中文。

```text
你负责总结分析师对 BTC 走势的观点。

要求：
1. 只总结原文表达的意思。
2. 不添加自己的市场判断。
3. 如果分析师观点含糊，需要保留这种不确定性。
4. 如果有多个时间周期，需要分别总结。
5. 不要输出投资建议。

输入文本：
{{btc_relevant_text}}

请输出 JSON：

{
  "summary": "整体观点摘要",
  "key_points": [
    "要点1",
    "要点2"
  ],
  "mentioned_levels": {
    "support": [价格数字],
    "resistance": [价格数字],
    "target": [价格数字],
    "stop_loss": [价格数字],
    "invalidation": [价格数字]
  },
  "risk_notes": [
    "风险提示"
  ],
  "confidence": 0.0
}
```

---

## 6. 预测拆分 Prompt

用途：一条原文可能包含短期、中期、长期多个预测，需要拆成多条。

```text
你负责从分析师观点中拆分出一个或多个“可验证预测”。

一个可验证预测至少应尽量包含：
- 标的：BTC 或 BTCUSDT
- 方向：bullish、bearish、neutral、range
- 时间周期：short_term、mid_term、long_term、unknown
- 目标价格、目标区间、支撑位或压力位中的至少一种
- 验证依据

如果原文只有情绪表达，例如“我觉得 BTC 很强”，但没有周期、价格或明确方向，应标记为不可验证。

输入文本：
{{btc_relevant_text}}

请输出 JSON：

{
  "predictions": [
    {
      "prediction_index": 1,
      "asset": "BTCUSDT",
      "direction": "bullish/bearish/neutral/range/unknown",
      "horizon": "scalp/intraday/short_term/mid_term/long_term/unknown",
      "target_price": 80000,
      "target_price_min": null,
      "target_price_max": null,
      "support_levels": [75000],
      "resistance_levels": [80000],
      "stop_loss": null,
      "invalidation_condition": "跌破 75000 则看涨失效",
      "time_expression": "短期",
      "verifiability": "high/medium/low/unverifiable",
      "evidence_text": "原文中支持该预测的句子",
      "needs_user_confirmation": true
    }
  ],
  "unverifiable_statements": [
    "无法验证的表达"
  ]
}
```

---

## 7. 时间周期标准化 Prompt

用途：把“短期”“这波”“接下来”等变成系统可验证时间。

```text
你负责将分析师预测中的时间表达标准化为系统验证时间。

系统默认规则：
- scalp：4 小时内
- intraday：24 小时内
- short_term：7 天内
- mid_term：30 天内
- long_term：90 天内
- unknown：需要用户确认

如果原文明确给出时间，例如“本周五前”“月底前”“两周内”，优先使用原文时间。

当前系统时间：
{{current_time}}

预测内容：
{{prediction}}

请输出 JSON：

{
  "horizon": "scalp/intraday/short_term/mid_term/long_term/unknown",
  "valid_from": "ISO 8601",
  "valid_until": "ISO 8601 或 null",
  "verification_time": "ISO 8601 或 null",
  "time_source": "explicit/default/inferred/missing",
  "original_time_expression": "原始时间表达",
  "needs_user_confirmation": true,
  "reason": "标准化依据"
}
```

---

## 8. 预测置信度解析 Prompt

用途：判断分析师表达强度。

```text
你负责判断分析师对该预测表达的主观置信度。

请根据措辞判断，不要根据你自己的市场观点判断。

高置信表达示例：
- 一定会
- 大概率
- 明确看涨
- 目标就是
- 必破

中置信表达示例：
- 可能
- 倾向
- 看起来
- 有机会
- 预计

低置信表达示例：
- 也许
- 如果
- 观察
- 不排除
- 需要确认

输入预测：
{{prediction}}

请输出 JSON：

{
  "analyst_confidence_level": "high/medium/low/unknown",
  "confidence_score": 0.0,
  "confidence_evidence": "原文依据",
  "is_conditional": true,
  "condition_text": "如果有条件则填写，否则 null"
}
```

---

## 9. 人工确认判断 Prompt

用途：决定是否需要用户确认。

```text
你负责判断当前解析结果是否可以直接入库，还是需要用户人工确认。

需要人工确认的情况：
1. 分析师身份不明确。
2. 发布时间不明确。
3. 方向不明确。
4. 时间周期不明确。
5. 目标价不明确但系统试图生成了目标。
6. 原文存在多个互相矛盾的观点。
7. LLM 对任一关键字段信心低。
8. 该观点不可验证。

输入：
分析师识别结果：
{{analyst_result}}

时间识别结果：
{{time_result}}

预测解析结果：
{{predictions_result}}

请输出 JSON：

{
  "can_auto_save": false,
  "needs_user_confirmation": true,
  "confirmation_fields": [
    "analyst_name",
    "verification_time",
    "direction"
  ],
  "blocking_reasons": [
    "发布时间不明确"
  ],
  "suggested_user_question": "请确认该预测的验证时间是否为 7 天后？"
}
```

---

# 三、观点变化检测 Graph

用于判断分析师是否在验证前改变观点。

---

## 10. 新旧观点冲突判断 Prompt

```text
你负责判断同一分析师的新观点是否改变、推翻或修正了此前未验证的预测。

你只能比较输入的新旧预测，不得使用外部知识。

冲突类型定义：
- same_view：观点基本一致
- stronger_same_view：方向一致但目标更激进
- weaker_same_view：方向一致但目标更保守
- target_adjustment：方向一致但目标价明显调整
- partial_change：部分周期或条件改变
- reversal：方向反转，例如看涨变看跌
- abandon：明确放弃原观点
- unclear：无法判断

旧预测：
{{old_prediction}}

新预测：
{{new_prediction}}

请输出 JSON：

{
  "conflict_type": "same_view/stronger_same_view/weaker_same_view/target_adjustment/partial_change/reversal/abandon/unclear",
  "is_material_change": true,
  "affects_old_prediction": true,
  "should_penalize_stability": true,
  "suggested_old_prediction_status": "active/modified/abandoned/superseded",
  "reason": "判断依据",
  "evidence_old": "旧观点依据",
  "evidence_new": "新观点依据"
}
```

---

## 11. 修改观点总结 Prompt

```text
你负责生成分析师观点变化的简洁总结。

输入：
旧预测：
{{old_prediction}}

新预测：
{{new_prediction}}

冲突判断：
{{conflict_result}}

请输出 JSON：

{
  "change_summary": "该分析师将短期 BTC 观点从看涨 80000 调整为看跌 75000。",
  "change_severity": "none/minor/moderate/major",
  "user_visible_note": "适合展示给用户的一句话",
  "audit_note": "适合写入系统日志的说明"
}
```

---

# 四、预测验证 Graph

注意：最终验证建议程序计算，但可以让 LLM 生成解释报告。

---

## 12. 验证结果解释 Prompt

用途：程序已经算出结果，LLM 只负责解释。

```text
你负责将系统计算出的 BTC 预测验证结果解释成用户可读的文字。

你不得改变系统计算结果。
你不得重新判断成功或失败。
你只能基于输入数据生成解释。

预测内容：
{{prediction}}

验证行情数据：
{{verification_market_data}}

系统计算结果：
{{verification_result}}

请输出 JSON：

{
  "plain_language_summary": "该预测方向正确，但目标价未触达，最高价距离目标价还差 2.3%。",
  "direction_explanation": "方向判断说明",
  "target_explanation": "目标价验证说明",
  "time_explanation": "时间窗口说明",
  "final_result_explanation": "最终结果说明",
  "user_visible_tags": [
    "方向正确",
    "目标未达成"
  ]
}
```

---

## 13. 失败原因归因 Prompt

```text
你负责分析一条预测失败或部分失败的原因。

你只能基于以下输入进行归因：
- 预测内容
- 验证期间 BTC 行情
- 技术指标变化
- 资金费率变化
- 分析师是否中途修改观点

不得编造新闻或外部原因。

预测内容：
{{prediction}}

验证结果：
{{verification_result}}

市场数据摘要：
{{market_summary}}

技术指标摘要：
{{indicator_summary}}

观点变化记录：
{{view_change_records}}

请输出 JSON：

{
  "failure_reason_category": "wrong_direction/target_too_aggressive/time_window_too_short/view_changed/market_sideways/unclear",
  "failure_reason_summary": "失败原因总结",
  "supporting_evidence": [
    "验证期内最高价距离目标价仍有 5%",
    "验证前分析师已改为看跌"
  ],
  "should_reduce_direction_score": true,
  "should_reduce_target_score": true,
  "should_reduce_stability_score": true
}
```

---

# 五、分析师评分 Graph

评分最好由程序完成，LLM 只负责解释和标签化。

---

## 14. 分析师能力画像 Prompt

```text
你负责根据分析师历史预测表现生成能力画像。

你不得修改系统统计数据。
你只能基于输入的统计结果进行总结。

分析师信息：
{{analyst}}

历史统计：
{{analyst_metrics}}

近期预测表现：
{{recent_predictions}}

虚拟交易表现：
{{virtual_trading_metrics}}

请输出 JSON：

{
  "analyst_profile_summary": "该分析师短期方向判断较好，但目标价经常偏激进，虚拟交易回撤较大。",
  "strengths": [
    "短期方向准确率较高"
  ],
  "weaknesses": [
    "目标价达成率偏低",
    "观点调整频繁"
  ],
  "best_horizon": "short_term/mid_term/long_term/unknown",
  "risk_level": "low/medium/high",
  "follow_value": "high/medium/low",
  "tags": [
    "短线较强",
    "目标激进",
    "频繁改观点"
  ],
  "caution_note": "该分析师观点适合作为短线方向参考，不宜单独作为交易依据。"
}
```

---

## 15. 分析师排行榜解释 Prompt

```text
你负责解释分析师排行榜变化。

你不得重新计算排名。
你只能解释系统输入的排名变化原因。

排行榜数据：
{{ranking_data}}

排名变化：
{{ranking_changes}}

请输出 JSON：

{
  "ranking_summary": "本期排名上升的分析师主要受益于短期方向预测成功。",
  "top_performers": [
    {
      "analyst_name": "分析师A",
      "reason": "近期 3 条预测方向全部正确，虚拟账户收益提升"
    }
  ],
  "decliners": [
    {
      "analyst_name": "分析师B",
      "reason": "连续目标价未达成，且出现观点反转"
    }
  ],
  "user_visible_notes": [
    "排名仅代表历史表现，不代表未来预测准确性"
  ]
}
```

---

# 六、虚拟合约交易 Graph

交易动作应由规则引擎决定，LLM 可以负责解释信号和复盘。

---

## 16. 交易信号解释 Prompt

```text
你负责解释系统根据分析师预测生成的虚拟合约交易信号。

你不得建议真实交易。
你不得改变系统生成的交易动作。

分析师预测：
{{prediction}}

系统交易规则：
{{trading_rules}}

生成的虚拟交易信号：
{{trade_signal}}

请输出 JSON：

{
  "signal_explanation": "由于该预测为短期看涨且目标价高于当前价格，系统为该分析师虚拟账户生成开多信号。",
  "position_logic": "开仓逻辑说明",
  "exit_logic": "止盈、止损或验证到期平仓逻辑",
  "risk_notes": [
    "该交易仅用于虚拟模拟",
    "杠杆会放大回撤"
  ]
}
```

---

## 17. 虚拟交易复盘 Prompt

```text
你负责复盘某个分析师的一笔虚拟合约交易。

你不得修改交易结果。
你不得给出真实交易建议。

交易记录：
{{trade_record}}

对应预测：
{{prediction}}

行情过程：
{{market_path_summary}}

手续费与资金费率：
{{fee_and_funding_summary}}

请输出 JSON：

{
  "trade_review_summary": "该笔虚拟多单方向正确，但未触及目标价，最终小幅盈利平仓。",
  "what_worked": [
    "入场方向与后续价格走势一致"
  ],
  "what_failed": [
    "目标价设置偏高"
  ],
  "cost_impact": "手续费和资金费率合计降低收益 0.3%。",
  "risk_review": "持仓期间最大浮亏达到 2.1%，风险处于中等水平。",
  "tags": [
    "方向正确",
    "目标偏高",
    "小幅盈利"
  ]
}
```

---

# 七、BTC 综合分析报告 Graph

这个 Graph 用于生成每日/每周 BTC 综合报告。

---

## 18. 市场数据摘要 Prompt

```text
你负责总结 BTC 当前市场数据。

你只能基于输入的行情和指标数据总结。
不得编造新闻。
不得给出确定性预测。

行情数据：
{{market_data}}

技术指标：
{{technical_indicators}}

资金费率：
{{funding_rates}}

请输出 JSON：

{
  "market_summary": "BTC 当前处于震荡偏强状态，价格位于主要均线上方。",
  "trend_state": "bullish/bearish/neutral/range",
  "volatility_state": "low/medium/high",
  "volume_state": "weak/normal/strong",
  "funding_state": "long_crowded/short_crowded/neutral/unknown",
  "key_support_levels": [价格],
  "key_resistance_levels": [价格],
  "indicator_notes": [
    "RSI 接近超买区",
    "MACD 仍处于多头区间"
  ],
  "risk_notes": [
    "资金费率偏高可能增加多头回调风险"
  ]
}
```

---

## 19. 分析师共识 Prompt

```text
你负责总结当前分析师群体对 BTC 的观点分布。

你不得偏向某个分析师。
你必须区分短期、中期、长期观点。

分析师预测列表：
{{active_predictions}}

分析师历史表现权重：
{{analyst_metrics}}

请输出 JSON：

{
  "consensus_summary": "当前分析师短期观点偏多，但中期分歧较大。",
  "short_term_distribution": {
    "bullish": 0,
    "bearish": 0,
    "neutral": 0,
    "range": 0
  },
  "mid_term_distribution": {
    "bullish": 0,
    "bearish": 0,
    "neutral": 0,
    "range": 0
  },
  "long_term_distribution": {
    "bullish": 0,
    "bearish": 0,
    "neutral": 0,
    "range": 0
  },
  "weighted_consensus": "bullish/bearish/neutral/mixed",
  "notable_disagreements": [
    "分析师A看涨至80000，但分析师B认为会回调至75000"
  ],
  "high_reliability_views": [
    {
      "analyst_name": "分析师A",
      "view": "短期看涨",
      "reason": "该分析师短期方向历史准确率较高"
    }
  ]
}
```

---

## 20. 多情景推演 Prompt

```text
你负责基于 BTC 行情、技术指标和分析师共识生成多情景推演。

你不得给出确定性预测。
你不得建议用户买入或卖出。
你需要给出条件触发式分析。

市场摘要：
{{market_summary}}

技术指标摘要：
{{indicator_summary}}

分析师共识：
{{consensus_summary}}

关键价格位：
{{key_levels}}

请输出 JSON：

{
  "base_case": {
    "scenario": "基准情景",
    "description": "如果 BTC 维持在关键支撑上方，短期可能延续震荡偏强。",
    "trigger_conditions": [
      "价格维持在 75000 上方"
    ],
    "invalid_conditions": [
      "跌破 75000 且放量"
    ]
  },
  "bullish_case": {
    "scenario": "多头情景",
    "description": "如果突破主要压力位，可能测试更高目标区间。",
    "trigger_conditions": [
      "放量突破 80000"
    ],
    "risk_factors": [
      "资金费率过高"
    ]
  },
  "bearish_case": {
    "scenario": "空头情景",
    "description": "如果跌破关键支撑，可能进入回调结构。",
    "trigger_conditions": [
      "跌破 75000"
    ],
    "risk_factors": [
      "下方支撑快速承接"
    ]
  },
  "range_case": {
    "scenario": "震荡情景",
    "description": "如果无法突破压力也未跌破支撑，价格可能维持区间波动。",
    "range_low": 75000,
    "range_high": 80000
  }
}
```

---

## 21. 每日 BTC 报告生成 Prompt

```text
你负责生成每日 BTC 市场分析报告。

要求：
1. 不得给出投资建议。
2. 不得使用“必涨”“必跌”等确定性语言。
3. 必须区分事实数据、分析师共识和系统情景推演。
4. 必须提示风险。
5. 报告应简洁、清晰、适合前端展示。

输入：
市场摘要：
{{market_summary}}

技术指标摘要：
{{indicator_summary}}

分析师共识：
{{consensus_summary}}

情景推演：
{{scenario_analysis}}

重要预测验证：
{{recent_verification_results}}

请输出 JSON：

{
  "title": "BTC 每日市场观察",
  "executive_summary": "今日 BTC 处于震荡偏强结构，分析师短期观点偏多，但资金费率显示多头拥挤风险。",
  "market_status": "市场状态说明",
  "technical_view": "技术指标说明",
  "analyst_consensus": "分析师观点分布说明",
  "key_levels": {
    "support": [价格],
    "resistance": [价格]
  },
  "scenarios": [
    {
      "name": "多头情景",
      "description": "..."
    },
    {
      "name": "空头情景",
      "description": "..."
    }
  ],
  "recent_prediction_review": "近期预测验证情况摘要",
  "risk_warnings": [
    "资金费率偏高可能导致短线波动加剧"
  ],
  "disclaimer": "本报告仅用于信息整理与历史表现分析，不构成投资建议。"
}
```

---

# 八、提醒与告警 Graph

用于价格突破、预测到期、观点冲突等提醒。

---

## 22. 价格告警解释 Prompt

```text
你负责生成 BTC 价格告警说明。

输入：
告警类型：
{{alert_type}}

当前价格：
{{current_price}}

触发条件：
{{trigger_condition}}

相关预测：
{{related_predictions}}

请输出 JSON：

{
  "alert_title": "BTC 触发关键价格提醒",
  "alert_message": "BTC 当前价格已接近 80000，该位置是多位分析师提到的短期压力位。",
  "alert_level": "info/warning/critical",
  "related_context": [
    "分析师A曾预测短期目标为80000"
  ],
  "recommended_user_action": "查看相关预测和验证页面"
}
```

---

## 23. 预测即将验证提醒 Prompt

```text
你负责生成预测即将到期验证的提醒。

输入：
即将验证的预测：
{{due_predictions}}

当前行情摘要：
{{current_market_summary}}

请输出 JSON：

{
  "notification_title": "有预测即将进入验证",
  "notification_message": "今日有 3 条 BTC 预测将到期验证，其中 2 条为看涨目标。",
  "prediction_summaries": [
    {
      "analyst_name": "分析师A",
      "summary": "短期看涨至80000",
      "verification_time": "ISO 8601"
    }
  ],
  "priority": "low/medium/high"
}
```

---

# 九、数据质量与审计 Graph

用于保证系统可追踪。

---

## 24. 数据异常检测 Prompt

```text
你负责检查输入数据是否存在异常或不一致。

检查内容：
1. 预测目标价是否明显不合理。
2. 时间顺序是否错误。
3. 验证时间是否早于发布时间。
4. 看涨目标价是否低于发布时价格。
5. 看跌目标价是否高于发布时价格。
6. 同一分析师是否存在完全重复预测。
7. 价格单位是否可能错误。

输入数据：
{{structured_prediction}}

当前 BTC 价格：
{{current_btc_price}}

请输出 JSON：

{
  "has_anomaly": true,
  "anomaly_types": [
    "bullish_target_below_current_price"
  ],
  "severity": "low/medium/high",
  "details": [
    "预测方向为看涨，但目标价低于发布时价格"
  ],
  "needs_user_confirmation": true,
  "suggested_fix": "请确认目标价或方向是否填写错误"
}
```

---

## 25. Agent 运行审计摘要 Prompt

```text
你负责总结一次 Agent 工作流运行的审计信息。

输入：
运行 ID：
{{run_id}}

输入数据：
{{input_data}}

各节点输出：
{{node_outputs}}

最终结果：
{{final_result}}

错误信息：
{{errors}}

请输出 JSON：

{
  "run_summary": "本次运行成功解析 1 条原始观点，生成 2 条预测，其中 1 条需要人工确认。",
  "nodes_completed": [
    "text_cleaning",
    "analyst_identification",
    "prediction_extraction"
  ],
  "warnings": [
    "发布时间由相对时间推断"
  ],
  "errors": [
    "无"
  ],
  "audit_tags": [
    "需要人工确认",
    "多周期预测"
  ]
}
```

---

# 十、人工确认页面 Prompt

用于生成前端可显示的问题。

---

## 26. 用户确认问题生成 Prompt

```text
你负责根据解析结果生成给用户确认的问题。

要求：
1. 问题必须具体。
2. 不要一次问太多。
3. 每个问题应对应一个字段。
4. 给出系统推荐值和原因。
5. 适合在前端表单中展示。

解析结果：
{{parsed_result}}

需要确认的字段：
{{confirmation_fields}}

请输出 JSON：

{
  "confirmation_questions": [
    {
      "field": "verification_time",
      "question": "该预测的验证时间是否设置为 7 天后？",
      "suggested_value": "2025-01-08T00:00:00Z",
      "reason": "原文使用“短期”，系统默认短期为 7 天",
      "input_type": "datetime"
    },
    {
      "field": "target_price",
      "question": "请确认目标价是否为 80000 USDT？",
      "suggested_value": 80000,
      "reason": "原文中出现“涨到80000”",
      "input_type": "number"
    }
  ]
}
```

---

# 十一、推荐 LangGraph 节点编排

可以按下面方式组织。

---

## Graph A：观点录入与解析

```text
Start
  ↓
TextCleaningNode
  ↓
BTCRelevanceNode
  ↓
AnalystIdentificationNode
  ↓
PublishTimeExtractionNode
  ↓
OpinionSummaryNode
  ↓
PredictionExtractionNode
  ↓
TimeNormalizationNode
  ↓
ConfidenceParsingNode
  ↓
DataAnomalyCheckNode
  ↓
HumanConfirmationDecisionNode
  ↓
如果需要确认 → HumanReviewQueue
如果无需确认 → SavePrediction
  ↓
ViewChangeDetectionGraph
  ↓
VirtualTradeSignalGraph
  ↓
End
```

---

## Graph B：预测验证

```text
Start
  ↓
LoadDuePredictions
  ↓
LoadMarketData
  ↓
RuleBasedVerification
  ↓
UpdatePredictionStatus
  ↓
UpdateAnalystMetrics
  ↓
UpdateVirtualTrade
  ↓
VerificationExplanationNode
  ↓
FailureReasonNode
  ↓
SaveVerificationReport
  ↓
End
```

---

## Graph C：每日 BTC 报告

```text
Start
  ↓
LoadMarketData
  ↓
LoadTechnicalIndicators
  ↓
LoadFundingRates
  ↓
LoadActivePredictions
  ↓
MarketSummaryNode
  ↓
AnalystConsensusNode
  ↓
ScenarioAnalysisNode
  ↓
DailyReportNode
  ↓
SaveReport
  ↓
NotifyFrontend
  ↓
End
```

---

# 十二、最关键的输出格式要求

所有 LLM 节点建议统一包一层标准输出：

```json
{
  "success": true,
  "node_name": "prediction_extraction",
  "model_confidence": 0.86,
  "needs_human_review": false,
  "data": {},
  "warnings": [],
  "errors": [],
  "evidence": []
}
```

这样方便：

- 前端展示
- 后端日志
- Agent 回放
- 错误追踪
- 人工审核

---

# 十三、建议不要让 LLM 做的事情

以下内容不要交给 Prompt 决定，应由程序规则完成：

1. 最终预测是否成功  
2. 胜率计算  
3. 目标价接近率计算  
4. 虚拟交易盈亏计算  
5. 资金费率计算  
6. 手续费计算  
7. 排名计算  
8. 是否爆仓  
9. 实际价格是否触达目标  
10. 数据库写入  

LLM 适合做：

1. 原文理解  
2. 观点摘要  
3. 预测结构化  
4. 模糊字段识别  
5. 人工确认问题生成  
6. 解释报告生成  
7. 分析师画像总结  
8. 多情景报告生成  

---

# 十四、最终建议

你这个系统的 Agent 不应该是一个“自由聊天机器人”，而应该是多个受控节点组成的 **Agent 工作流系统**：

- 解析类 Prompt：负责从人类语言中提取结构化预测  
- 验证类 Prompt：负责解释程序验证结果  
- 画像类 Prompt：负责总结分析师历史能力  
- 报告类 Prompt：负责生成 BTC 综合观察报告  
- 审计类 Prompt：负责保证每一步可追踪  

这样设计后，系统会更稳定，也更符合 Harness Engineering 的思想：**让 LLM 在框架里工作，而不是让 LLM 自己决定一切。**
