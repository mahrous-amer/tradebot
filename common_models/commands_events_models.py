from pydantic import BaseModel, Field, validator
from typing import List, Dict, Optional
from datetime import datetime
import uuid

# Assuming market_data_models.Trade will be imported if needed,
# or define Trade here again if it's a dependency for OrderStatusUpdateEvent.
# For simplicity and to avoid circular deps if this file is imported by market_data_models,
# it's often better to keep them separate or have a base_models.py.
# However, as per architecture, OrderStatusUpdateEvent contains List[Trade].
# We will import it from .market_data_models
from .market_data_models import Trade


# --- Command Models (Published by Bot to Connectors) ---
# Based on architecture/connectors_redis_api.md

class PlaceOrderCommand(BaseModel):
    client_order_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str # e.g., "BTC/USDT"
    type: str  # "limit", "market"
    side: str  # "buy", "sell"
    quantity: float
    price: Optional[float] = None # Required for limit orders

    @validator('price', always=True)
    def price_required_for_limit(cls, v, values):
        if values.get('type') == 'limit' and v is None:
            raise ValueError('Price is required for limit orders')
        return v

class RequestBalanceCommand(BaseModel):
    client_request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    exchange: str # e.g., "binance" (used if one service handles multiple exchanges, or for routing)
    # Specific assets can be requested if needed, otherwise all are returned
    assets: Optional[List[str]] = None


# --- Event Models (Published by Connectors to Bot) ---
# Based on architecture/connectors_redis_api.md

class OrderResponseEvent(BaseModel):
    client_order_id: str # Correlates with PlaceOrderCommand
    order_id: Optional[str] = None # Exchange-assigned order ID, if successful
    symbol: str
    requested_quantity: float
    requested_price: Optional[float] = None
    side: str
    type: str
    status: str # e.g., "accepted", "rejected", "failed_to_place"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(datetime.utcnow().tzinfo or datetime.timezone.utc)) # Ensure UTC
    error_message: Optional[str] = None
    # Optional: include initial filled quantity if known immediately
    filled_quantity: Optional[float] = 0.0


class OrderStatusUpdateEvent(BaseModel):
    order_id: str # Exchange-assigned order ID
    client_order_id: Optional[str] = None # If available from initial placement
    symbol: str
    status: str # e.g., "open", "partially_filled", "filled", "canceled", "expired", "failed"
    filled_quantity: float
    remaining_quantity: float
    average_price: Optional[float] = None
    trades: Optional[List[Trade]] = None # List of trades associated with this update
    fee: Optional[Dict] = None # e.g., {"currency": "USDT", "cost": 0.1}
    timestamp: datetime = Field(default_factory=lambda: datetime.now(datetime.utcnow().tzinfo or datetime.timezone.utc)) # Ensure UTC
    exchange: str


class AccountBalanceEvent(BaseModel):
    exchange: str
    asset: str # e.g., "BTC", "USDT"
    free: float
    locked: float # Amount used in open orders
    total: float # free + locked
    timestamp: datetime = Field(default_factory=lambda: datetime.now(datetime.utcnow().tzinfo or datetime.timezone.utc)) # Ensure UTC
