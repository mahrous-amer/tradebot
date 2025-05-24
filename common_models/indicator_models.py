from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional

class IndicatorValue(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    value: float
    indicator_name: str  # e.g., "rsi_14", "sma_50", "ema_20"
    symbol: str          # e.g., "BTC/USDT" (standard format with slash)
    exchange: str        # e.g., "binance", "luno"
    # Optional: Add a field for timeframe if indicators can be timeframe-specific
    # timeframe: Optional[str] = None # e.g., "1h", "4h", "1d"
