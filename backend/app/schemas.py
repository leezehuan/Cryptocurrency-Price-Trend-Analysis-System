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


class ApiMessage(BaseModel):
    message: str
