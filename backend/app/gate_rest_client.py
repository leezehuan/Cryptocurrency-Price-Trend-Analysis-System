"""Gate REST API 客户端（公开数据，无需认证）

参考 gateapi-python 项目中的调用方式，使用 httpx 直接请求 Gate 公开 REST API。
Base URL: https://api.gateio.ws/api/v4
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GATE_REST_BASE_URL = "https://api.gateio.ws/api/v4"


class GateRestClient:
    """轻量级 Gate REST API 客户端，仅封装公开只读接口。"""

    def __init__(self, base_url: str = GATE_REST_BASE_URL, timeout: float = 20.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Futures / 合约
    # ------------------------------------------------------------------

    def list_futures_tickers(self, settle: str = "usdt", contract: str | None = None) -> list[dict[str, Any]]:
        """GET /futures/{settle}/tickers"""
        params: dict[str, Any] = {}
        if contract:
            params["contract"] = contract
        return self._get(f"/futures/{settle}/tickers", params)

    def list_futures_candlesticks(
        self,
        settle: str,
        contract: str,
        interval: str,
        limit: int = 200,
        from_ts: int | None = None,
        to_ts: int | None = None,
    ) -> list[list]:
        """GET /futures/{settle}/candlesticks

        返回格式: [[time, volume, close, high, low, open], ...]
        """
        params: dict[str, Any] = {"contract": contract, "interval": interval}
        if limit:
            params["limit"] = limit
        if from_ts is not None:
            params["from"] = from_ts
        if to_ts is not None:
            params["to"] = to_ts
        return self._get(f"/futures/{settle}/candlesticks", params)

    def list_futures_funding_rate(
        self, settle: str, contract: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """GET /futures/{settle}/funding_rate

        返回格式: [{"t": timestamp, "r": rate}, ...]
        """
        params = {"contract": contract, "limit": limit}
        return self._get(f"/futures/{settle}/funding_rate", params)

    def list_futures_order_book(
        self, settle: str, contract: str, limit: int = 10
    ) -> dict[str, Any]:
        """GET /futures/{settle}/order_book"""
        params = {"contract": contract, "limit": limit}
        return self._get(f"/futures/{settle}/order_book", params)

    def list_futures_trades(
        self, settle: str, contract: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """GET /futures/{settle}/trades"""
        params = {"contract": contract, "limit": limit}
        return self._get(f"/futures/{settle}/trades", params)

    def list_futures_premium_index(
        self, settle: str, contract: str, interval: str = "1h", limit: int = 100
    ) -> list[list]:
        """GET /futures/{settle}/premium_index"""
        params = {"contract": contract, "interval": interval, "limit": limit}
        return self._get(f"/futures/{settle}/premium_index", params)

    def list_contract_stats(
        self, settle: str, contract: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """GET /futures/{settle}/contract_stats"""
        params = {"contract": contract, "limit": limit}
        return self._get(f"/futures/{settle}/contract_stats", params)

    def list_liquidated_orders(
        self, settle: str, contract: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """GET /futures/{settle}/liq_orders"""
        params = {"contract": contract, "limit": limit}
        return self._get(f"/futures/{settle}/liq_orders", params)

    # ------------------------------------------------------------------
    # Spot / 现货
    # ------------------------------------------------------------------

    def list_spot_tickers(self, currency_pair: str | None = None) -> list[dict[str, Any]]:
        """GET /spot/tickers"""
        params: dict[str, Any] = {}
        if currency_pair:
            params["currency_pair"] = currency_pair
        return self._get("/spot/tickers", params)

    def list_spot_candlesticks(
        self, currency_pair: str, interval: str, limit: int = 1000
    ) -> list[list]:
        """GET /spot/candlesticks

        返回格式: [[time, volume, close, high, low, open], ...]
        """
        params = {"currency_pair": currency_pair, "interval": interval, "limit": limit}
        return self._get("/spot/candlesticks", params)
