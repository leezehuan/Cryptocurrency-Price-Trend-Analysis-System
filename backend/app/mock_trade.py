"""模拟交易模块：封装 Gate Testnet API 下单、本地账户/持仓/交易记录管理。

通过项目自带的 gateapi-python SDK 访问 Gate Testnet。
Base URL: https://api-testnet.gateapi.io/api/v4
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config_loader import load_model_api_config
from .database import row_to_dict, rows_to_dicts, utc_now
from .llm_client import call_llm_node, standard_node_output
from .services import build_agent_analysis_signal, get_setting_value, live_market_price

logger = logging.getLogger(__name__)

# 将项目根目录下的 gateapi-python 加入 Python 路径
_GATEAPI_DIR = str(Path(__file__).resolve().parents[2] / "gateapi-python")
if _GATEAPI_DIR not in sys.path:
    sys.path.insert(0, _GATEAPI_DIR)

try:
    from gate_api import ApiClient, Configuration, FuturesApi, FuturesOrder
    from gate_api.exceptions import GateApiException
except ImportError as _exc:  # pragma: no cover
    logger.warning("gate_api SDK 未找到: %s", _exc)
    ApiClient = None  # type: ignore
    Configuration = None  # type: ignore
    FuturesApi = None  # type: ignore
    FuturesOrder = None  # type: ignore
    GateApiException = Exception  # type: ignore

TESTNET_HOST = "https://api-testnet.gateapi.io/api/v4"
DEFAULT_SYMBOL = "BTCUSDT"
DEFAULT_CONTRACT = "BTC_USDT"
DEFAULT_SETTLE = "usdt"


class GateTestnetTradeClient:
    """轻量 Gate Testnet 交易客户端，基于 gateapi-python SDK。"""

    def __init__(self, api_key: str, api_secret: str, host: str = TESTNET_HOST) -> None:
        if Configuration is None or ApiClient is None or FuturesApi is None:
            raise RuntimeError("gate_api SDK 不可用")
        self.config = Configuration(key=api_key, secret=api_secret, host=host)
        self.client = ApiClient(self.config)
        self.futures_api = FuturesApi(self.client)

    def get_futures_account(self, settle: str = DEFAULT_SETTLE) -> dict[str, Any]:
        """GET /futures/{settle}/accounts"""
        try:
            account = self.futures_api.list_futures_accounts(settle)
            return {"currency": account.currency, "available": str(account.available), "total": str(account.total), "position_margin": str(account.position_margin)}
        except GateApiException as exc:
            logger.warning("Testnet account query failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def get_position(self, settle: str = DEFAULT_SETTLE, contract: str = DEFAULT_CONTRACT) -> dict[str, Any]:
        """GET /futures/{settle}/positions/{contract}"""
        try:
            pos = self.futures_api.get_position(settle, contract)
            return {
                "contract": pos.contract,
                "size": int(pos.size),
                "entry_price": str(pos.entry_price),
                "mark_price": str(pos.mark_price),
                "unrealised_pnl": str(pos.unrealised_pnl),
                "leverage": str(pos.leverage),
            }
        except GateApiException as exc:
            if getattr(exc, "label", "") == "POSITION_NOT_FOUND":
                return {"contract": contract, "size": 0, "entry_price": "0", "mark_price": "0", "unrealised_pnl": "0"}
            logger.warning("Testnet position query failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def list_positions(self, settle: str = DEFAULT_SETTLE, holding: bool = True) -> list[dict[str, Any]]:
        """GET /futures/{settle}/positions — 列出所有持仓"""
        try:
            positions = self.futures_api.list_positions(settle, holding=holding)
            return [
                {
                    "contract": p.contract,
                    "size": int(p.size),
                    "leverage": str(p.leverage),
                    "entry_price": str(p.entry_price),
                    "mark_price": str(p.mark_price),
                    "liq_price": str(p.liq_price) if hasattr(p, "liq_price") else "",
                    "unrealised_pnl": str(p.unrealised_pnl),
                    "realised_pnl": str(p.realised_pnl) if hasattr(p, "realised_pnl") else "0",
                    "margin": str(p.margin) if hasattr(p, "margin") else "0",
                    "value": str(p.value) if hasattr(p, "value") else "0",
                    "mode": str(p.mode) if hasattr(p, "mode") else "",
                }
                for p in positions
                if int(p.size) != 0
            ]
        except GateApiException as exc:
            logger.warning("Testnet list positions failed: %s", exc)
            return [{"error": str(exc), "label": getattr(exc, "label", "unknown")}]

    def create_futures_order(self, settle: str, order: Any) -> dict[str, Any]:
        """POST /futures/{settle}/orders"""
        try:
            resp = self.futures_api.create_futures_order(settle, order)
            return {
                "order_id": str(resp.id),
                "status": str(resp.status),
                "contract": str(resp.contract),
                "size": int(resp.size),
                "price": str(resp.price),
                "left": int(resp.left),
                "fill_price": str(resp.fill_price) if hasattr(resp, "fill_price") else "0",
                "text": str(resp.text) if hasattr(resp, "text") else "",
                "tif": str(resp.tif) if hasattr(resp, "tif") else "",
                "create_time": float(resp.create_time) if hasattr(resp, "create_time") and resp.create_time else 0,
            }
        except GateApiException as exc:
            logger.error("Testnet order failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def list_futures_orders(self, settle: str = DEFAULT_SETTLE, status: str = "open", contract: str = DEFAULT_CONTRACT, limit: int = 50) -> list[dict[str, Any]]:
        """GET /futures/{settle}/orders — 列出订单"""
        try:
            orders = self.futures_api.list_futures_orders(settle, status, contract=contract, limit=limit)
            return [
                {
                    "order_id": str(o.id),
                    "status": str(o.status),
                    "contract": str(o.contract),
                    "size": int(o.size),
                    "price": str(o.price),
                    "left": int(o.left),
                    "fill_price": str(o.fill_price) if hasattr(o, "fill_price") else "0",
                    "text": str(o.text) if hasattr(o, "text") else "",
                    "tif": str(o.tif) if hasattr(o, "tif") else "",
                    "create_time": float(o.create_time) if hasattr(o, "create_time") and o.create_time else 0,
                    "is_close": bool(o.is_close) if hasattr(o, "is_close") else False,
                    "reduce_only": bool(o.reduce_only) if hasattr(o, "reduce_only") else False,
                }
                for o in orders
            ]
        except GateApiException as exc:
            logger.warning("Testnet list orders failed: %s", exc)
            return [{"error": str(exc), "label": getattr(exc, "label", "unknown")}]

    def get_futures_order(self, settle: str, order_id: str) -> dict[str, Any]:
        """GET /futures/{settle}/orders/{order_id} — 查询单个订单"""
        try:
            o = self.futures_api.get_futures_order(settle, order_id)
            return {
                "order_id": str(o.id),
                "status": str(o.status),
                "contract": str(o.contract),
                "size": int(o.size),
                "price": str(o.price),
                "left": int(o.left),
                "fill_price": str(o.fill_price) if hasattr(o, "fill_price") else "0",
                "text": str(o.text) if hasattr(o, "text") else "",
                "tif": str(o.tif) if hasattr(o, "tif") else "",
                "create_time": float(o.create_time) if hasattr(o, "create_time") and o.create_time else 0,
                "finish_time": float(o.finish_time) if hasattr(o, "finish_time") and o.finish_time else 0,
                "finish_as": str(o.finish_as) if hasattr(o, "finish_as") else "",
            }
        except GateApiException as exc:
            logger.warning("Testnet get order failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def cancel_futures_order(self, settle: str, order_id: str) -> dict[str, Any]:
        """DELETE /futures/{settle}/orders/{order_id} — 撤销订单"""
        try:
            o = self.futures_api.cancel_futures_order(settle, order_id)
            return {
                "order_id": str(o.id),
                "status": str(o.status),
                "contract": str(o.contract),
                "size": int(o.size),
                "price": str(o.price),
                "left": int(o.left),
            }
        except GateApiException as exc:
            logger.error("Testnet cancel order failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def cancel_all_futures_orders(self, settle: str = DEFAULT_SETTLE, contract: str = DEFAULT_CONTRACT) -> list[dict[str, Any]]:
        """DELETE /futures/{settle}/orders — 撤销所有订单"""
        try:
            orders = self.futures_api.cancel_all_futures_order(settle, contract=contract)
            return [
                {"order_id": str(o.id), "status": str(o.status), "contract": str(o.contract)}
                for o in orders
            ]
        except GateApiException as exc:
            logger.error("Testnet cancel all orders failed: %s", exc)
            return [{"error": str(exc), "label": getattr(exc, "label", "unknown")}]

    def update_position_leverage(self, settle: str = DEFAULT_SETTLE, contract: str = DEFAULT_CONTRACT, leverage: str = "10", cross_leverage_limit: str = "") -> dict[str, Any]:
        """POST /futures/{settle}/positions/{contract}/leverage — 调整杠杆"""
        try:
            kwargs: dict[str, Any] = {}
            if cross_leverage_limit:
                kwargs["cross_leverage_limit"] = cross_leverage_limit
            pos = self.futures_api.update_position_leverage(settle, contract, leverage, **kwargs)
            return {
                "contract": pos.contract,
                "leverage": str(pos.leverage),
                "mode": str(pos.mode) if hasattr(pos, "mode") else "",
            }
        except GateApiException as exc:
            logger.error("Testnet update leverage failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def update_position_margin(self, settle: str = DEFAULT_SETTLE, contract: str = DEFAULT_CONTRACT, change: str = "0") -> dict[str, Any]:
        """POST /futures/{settle}/positions/{contract}/margin — 调整保证金"""
        try:
            pos = self.futures_api.update_position_margin(settle, contract, change)
            return {
                "contract": pos.contract,
                "margin": str(pos.margin) if hasattr(pos, "margin") else "0",
                "entry_price": str(pos.entry_price),
                "leverage": str(pos.leverage),
            }
        except GateApiException as exc:
            logger.error("Testnet update margin failed: %s", exc)
            return {"error": str(exc), "label": getattr(exc, "label", "unknown")}

    def get_my_trades(self, settle: str = DEFAULT_SETTLE, contract: str = DEFAULT_CONTRACT, limit: int = 50) -> list[dict[str, Any]]:
        """GET /futures/{settle}/my_trades — 查询成交记录"""
        try:
            trades = self.futures_api.get_my_trades(settle, contract=contract, limit=limit)
            return [
                {
                    "trade_id": str(t.id) if hasattr(t, "id") else "",
                    "order_id": str(t.order_id) if hasattr(t, "order_id") else "",
                    "contract": str(t.contract) if hasattr(t, "contract") else contract,
                    "size": int(t.size) if hasattr(t, "size") else 0,
                    "price": str(t.price) if hasattr(t, "price") else "0",
                    "fill_price": str(t.fill_price) if hasattr(t, "fill_price") else "0",
                    "role": str(t.role) if hasattr(t, "role") else "",
                    "create_time": float(t.create_time) if hasattr(t, "create_time") and t.create_time else 0,
                }
                for t in trades
            ]
        except GateApiException as exc:
            logger.warning("Testnet get my trades failed: %s", exc)
            return [{"error": str(exc), "label": getattr(exc, "label", "unknown")}]


def _get_testnet_client(conn: sqlite3.Connection) -> GateTestnetTradeClient | None:
    """从 model_api.json 的 gate_testnet 配置创建 Testnet 客户端。"""
    try:
        config = load_model_api_config(mask_secrets=False)
    except Exception:
        return None
    provider = config.get("providers", {}).get("gate_testnet", {})
    api_key = str(provider.get("api_key") or "")
    api_secret = str(provider.get("api_secret") or "")
    host = str(provider.get("host") or TESTNET_HOST)
    if not api_key or not api_secret:
        return None
    try:
        return GateTestnetTradeClient(api_key, api_secret, host=host)
    except RuntimeError:
        return None


def execute_mock_trade(conn: sqlite3.Connection, direction: str, size: int = 1, price_type: str = "market", amount_usdt: float = 0.0, price: float = 0.0) -> dict[str, Any]:
    """直接走 Gate Testnet API 下单，不再维护本地 mock 数据。

    优先使用 amount_usdt：当 amount_usdt > 0 时，按当前市价换算为张数（size）。
    """
    # 风控 1：检查 API Key
    client = _get_testnet_client(conn)
    if client is None:
        return {"success": False, "error": "未配置 Gate Testnet API Key，请在 model_api.json 中配置"}

    # 风控 2：检查余额
    remote_account = client.get_futures_account(DEFAULT_SETTLE)
    if "error" in remote_account:
        return {"success": False, "error": f"无法查询 Testnet 账户: {remote_account.get('error')}"}
    available_remote = float(remote_account.get("available", 0))
    if available_remote <= 0:
        return {"success": False, "error": "Testnet 账户可用余额为 0，请先在测试网领取体验金"}

    # 获取当前市价，用于 amount_usdt 换算
    try:
        live_price = live_market_price(conn)["price"]
    except Exception:
        live_price = 0

    # 如果传入 amount_usdt，按当前价格换算为张数（向上取整，至少 1）
    if amount_usdt > 0 and live_price > 0:
        import math
        size = max(1, int(math.ceil(amount_usdt / live_price)))

    # 风控 3：检查反向持仓
    reverse_direction = "short" if direction == "long" else "long"
    positions = client.list_positions()
    for pos in positions:
        if not pos.get("error") and pos.get("contract") == DEFAULT_CONTRACT:
            if (reverse_direction == "long" and int(pos.get("size", 0)) > 0) or (reverse_direction == "short" and int(pos.get("size", 0)) < 0):
                return {"success": False, "error": f"已持有反向持仓（{reverse_direction}），请先平仓后再开新方向"}

    # 风控 4：合约最小单量检查
    try:
        contract_info = client.futures_api.get_futures_contract(DEFAULT_SETTLE, DEFAULT_CONTRACT)
        min_size = int(getattr(contract_info, "order_size_min", 1))
        if size < min_size:
            return {"success": False, "error": f"下单数量不能小于合约最小单量 {min_size}"}
    except Exception:
        min_size = 1

    # 风控 5：余额是否足够（粗略估算）
    margin_needed = (size * live_price * 0.001) if live_price else 0
    if available_remote < margin_needed * 1.1:
        return {"success": False, "error": f"Testnet 可用余额不足（当前 {available_remote}，预估需 {margin_needed:.2f} USDT）"}

    # 下单
    order_size = size if direction == "long" else -size
    order_price = "0" if price_type == "market" else str(price)
    order = FuturesOrder(contract=DEFAULT_CONTRACT, size=order_size, price=order_price, tif="ioc")
    result = client.create_futures_order(DEFAULT_SETTLE, order)

    if "error" in result:
        return {"success": False, "error": f"Testnet 下单失败: {result.get('error')}"}

    fill_price = float(result.get("fill_price") or result.get("price") or 0)
    if fill_price <= 0:
        fill_price = live_price or 0
    fee = fill_price * abs(size) * 0.0005

    return {
        "success": True,
        "order_id": result.get("order_id"),
        "status": result.get("status"),
        "direction": direction,
        "size": abs(size),
        "fill_price": fill_price,
        "fee": fee,
        "balance": available_remote,
    }


def generate_trade_advice(conn: sqlite3.Connection) -> dict[str, Any]:
    """调用 LLM 生成交易建议，综合当前价格、Agent 分析信号和 Gate 真实账户状态。"""
    try:
        price_data = live_market_price(conn)
        current_price = float(price_data.get("price") or 0)
    except Exception:
        current_price = 0

    try:
        signal = build_agent_analysis_signal(conn)
    except Exception:
        signal = {}

    # 从 Gate Testnet 获取真实账户和持仓
    client = _get_testnet_client(conn)
    if client is None:
        # 未配置 API Key 时，仅基于 Agent 信号生成建议
        return _rule_trade_advice(signal, current_price, 0, [], output.get("errors", ["未配置 Gate Testnet API Key，使用规则建议"]))

    remote_account = client.get_futures_account(DEFAULT_SETTLE)
    positions = client.list_positions()
    available_balance = float(remote_account.get("available", 0)) if "error" not in remote_account else 0

    position_summary = []
    long_pos = 0
    short_pos = 0
    for pos in positions:
        if pos.get("error") or pos.get("contract") != DEFAULT_CONTRACT:
            continue
        p_size = int(pos.get("size", 0))
        p_dir = "long" if p_size > 0 else "short"
        position_summary.append(f"{p_dir} {abs(p_size)} 张，入场价 {pos.get('entry_price')}")
        if p_size > 0:
            long_pos = abs(p_size)
        else:
            short_pos = abs(p_size)

    # 精简分析信号
    slim_signal = {
        "decision": signal.get("decision", "neutral"),
        "direction": signal.get("direction", "neutral"),
        "confidence": signal.get("confidence", "medium"),
        "risk_level": signal.get("risk_level", "medium"),
        "summary": signal.get("summary", ""),
    }

    total_equity = float(remote_account.get("total", 0)) if "error" not in remote_account else 0
    position_margin = float(remote_account.get("position_margin", 0)) if "error" not in remote_account else 0

    values = {
        "current_price": current_price,
        "analysis_signal": json.dumps(slim_signal, ensure_ascii=False, default=str),
        "available_balance": available_balance,
        "total_equity": total_equity,
        "position_margin": position_margin,
        "position_summary": "；".join(position_summary) if position_summary else "当前无持仓",
        "contract": DEFAULT_CONTRACT,
    }

    try:
        output = call_llm_node("mock_trade", "advice", values, provider_name="mock_trade")
    except Exception as exc:
        output = standard_node_output("advice", success=False, errors=[str(exc)])

    if output.get("success"):
        data = output.get("data", {})
        return {
            "success": True,
            "suggested_direction": data.get("suggested_direction", "hold"),
            "suggested_size": data.get("suggested_size", 0),
            "suggested_price_type": data.get("suggested_price_type", "market"),
            "reason": data.get("reason", ""),
        }

    # LLM 被拦截或失败时，使用规则兜底生成建议
    return _rule_trade_advice(signal, current_price, available_balance, {"long": long_pos, "short": short_pos}, output.get("errors", ["LLM 被拦截，使用规则建议"]))


def _rule_trade_advice(
    signal: dict[str, Any],
    current_price: float,
    balance: float,
    positions: dict[str, int],
    llm_errors: list[str],
) -> dict[str, Any]:
    """基于 Agent 分析信号和账户状态的规则建议兜底。

    positions: {"long": int, "short": int}
    """
    decision = signal.get("decision", "neutral")
    direction = signal.get("direction", "neutral")
    confidence = signal.get("confidence", "medium")
    risk_level = signal.get("risk_level", "medium")

    long_pos = int(positions.get("long", 0))
    short_pos = int(positions.get("short", 0))

    reasons: list[str] = []
    suggested_direction = "hold"
    suggested_size = 0

    if confidence in ("low", "very_low") or decision in ("neutral", "observe"):
        suggested_direction = "hold"
        reasons.append("Agent 信号方向不明确或置信度较低，建议观望。")
    elif decision == "bullish":
        if short_pos > 0:
            suggested_direction = "hold"
            reasons.append(f"当前持有空头持仓（{short_pos} 张），建议先平仓后再开多。")
        else:
            suggested_direction = "long"
            if confidence == "high":
                suggested_size = round(balance * 0.10, 2)
            else:
                suggested_size = round(balance * 0.05, 2)
            reasons.append(f"Agent 信号看多（置信度 {confidence}），建议开多 {suggested_size} USDT。")
    elif decision == "bearish":
        if long_pos > 0:
            suggested_direction = "hold"
            reasons.append(f"当前持有多头持仓（{long_pos} 张），建议先平仓后再开空。")
        else:
            suggested_direction = "short"
            if confidence == "high":
                suggested_size = round(balance * 0.10, 2)
            else:
                suggested_size = round(balance * 0.05, 2)
            reasons.append(f"Agent 信号看空（置信度 {confidence}），建议开空 {suggested_size} USDT。")
    else:
        suggested_direction = "hold"
        reasons.append("市场信号中性，暂无明确方向，建议观望。")

    if risk_level == "high":
        reasons.append("当前风险等级较高，请控制仓位，建议不超过余额 5%。")
        if suggested_size > balance * 0.05:
            suggested_size = round(balance * 0.05, 2)

    if suggested_size > 0 and suggested_size < 1:
        suggested_size = 1.0

    reasons.append(f"当前 BTC 价格 {current_price} USDT，账户余额 {balance} USDT。（{llm_errors[0]}）")

    return {
        "success": True,
        "suggested_direction": suggested_direction,
        "suggested_size": suggested_size,
        "suggested_price_type": "market",
        "reason": " ".join(reasons),
    }
