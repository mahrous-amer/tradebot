from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# --- Market Data Models (Published by Connectors) ---
# Based on architecture/connectors_redis_api.md

class Ticker(BaseModel):
    symbol: str  # e.g., "BTC/USDT" (standard format)
    timestamp: datetime
    last_price: float
    bid_price: float
    ask_price: float
    volume_24h: Optional[float] = None
    exchange: str # e.g., "binance"

class OrderBookLevel(BaseModel):
    price: float
    quantity: float

class OrderBook(BaseModel):
    symbol: str # e.g., "BTC/USDT"
    timestamp: datetime
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    exchange: str

class Trade(BaseModel):
    symbol: str # e.g., "BTC/USDT"
    timestamp: datetime
    price: float
    quantity: float
    side: str  # "buy" or "sell"
    trade_id: Optional[str] = None # Exchange-specific trade ID
    exchange: str
