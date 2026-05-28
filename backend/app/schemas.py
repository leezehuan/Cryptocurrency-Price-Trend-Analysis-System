from __future__ import annotations

from pydantic import BaseModel, Field


class OpinionCreate(BaseModel):
    analyst_name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=2, max_length=4000)
    source_url: str | None = None
    published_at: str | None = None


class AgentRunCreate(BaseModel):
    trigger: str = "manual"


class ReviewConfirmCreate(BaseModel):
    analyst_name: str | None = Field(default=None, min_length=1, max_length=80)
    source_url: str | None = None
    published_at: str | None = None
    predictions: list[dict[str, object]] = Field(default_factory=list)


class ReviewRejectCreate(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class SettingUpdate(BaseModel):
    value: object


class PredictionManualUpdate(BaseModel):
    direction: str | None = None
    target_price: float | None = None
    horizon: str | None = None
    verification_time: str | None = None
    status: str | None = None
    confidence: str | None = None
    summary: str | None = Field(default=None, max_length=1200)


class MarketIntervalsSyncCreate(BaseModel):
    intervals: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    symbol: str = "BTCUSDT"
    market_type: str = "perpetual"
    limit: int = Field(default=500, ge=1, le=1000)
    replace: bool = False


class MarketHistorySyncCreate(BaseModel):
    intervals: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    symbol: str = "BTCUSDT"
    market_type: str = "perpetual"
    days: int = Field(default=30, ge=1, le=365)
    replace: bool = False


class GateSyncCreate(BaseModel):
    tasks: list[str] = Field(
        default_factory=lambda: ["gate_btc_contract_sync"],
        description="要执行的同步任务列表",
    )


class MarketMemoryCreate(BaseModel):
    memory_type: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    symbol: str = "BTCUSDT"
    importance: float = Field(default=0.5, ge=0, le=1)
    valid_from: str | None = None
    valid_until: str | None = None


class ApiMessage(BaseModel):
    message: str
