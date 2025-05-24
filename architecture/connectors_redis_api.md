# Exchange Connector Redis API and Message Design

This document outlines the standardized Pydantic message models and Redis Stream conventions for asynchronous communication between the main bot application and exchange connector microservices (e.g., `binance-connector`).

## 1. Communication Principles

-   **Primary Transport:** Redis Streams are used for sending commands to connectors and for connectors to publish events (market data, responses, status updates).
-   **Message Serialization:** Messages published to Redis Streams will be JSON-serialized Pydantic models. Each stream entry will have a field named `data` containing the JSON string.
-   **Correlation:** Commands that expect a response will include a `client_order_id` or `client_request_id` for correlation with response events.

## 2. Pydantic Message Models

These models are defined in the `common_models/` shared library, specifically in `common_models/market_data_models.py` and `common_models/commands_events_models.py`. They are used by both the `bot` service and connector services (e.g., `binance-connector`).

```python
# Note: The actual implementation resides in the common_models/ directory.
# This is a representation based on that implementation.

from pydantic import BaseModel, Field, validator
from typing import List, Dict, Optional
from datetime import datetime
import uuid

# --- Market Data Models (Published by Connectors) ---
# These are also used by the Market Data Redis Streams (e.g., binance:BTCUSD:ticker)

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

# --- Command Models (Published by Bot to Connectors) ---

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
    exchange: str # e.g., "binance" 
    assets: Optional[List[str]] = None
    # Note: As of the current implementation, the binance-connector publishes balances
    # periodically via AccountBalanceEvent and does not actively listen for this command.
    # This command is a placeholder for a potential future pull-based mechanism.


# --- Event Models (Published by Connectors to Bot) ---

class OrderResponseEvent(BaseModel):
    client_order_id: str 
    order_id: Optional[str] = None 
    symbol: str
    requested_quantity: float
    requested_price: Optional[float] = None
    side: str
    type: str
    status: str 
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # Standardized to UTC
    error_message: Optional[str] = None
    filled_quantity: Optional[float] = 0.0


class OrderStatusUpdateEvent(BaseModel):
    order_id: str 
    client_order_id: Optional[str] = None 
    symbol: str
    status: str 
    filled_quantity: float
    remaining_quantity: float
    average_price: Optional[float] = None
    trades: Optional[List[Trade]] = None 
    fee: Optional[Dict] = None 
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # Standardized to UTC
    exchange: str


class AccountBalanceEvent(BaseModel):
    exchange: str
    asset: str 
    free: float
    locked: float 
    total: float 
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc)) # Standardized to UTC


# --- Indicator Value Model (Published by Plugins) ---
# Defined in common_models.indicator_models.py

class IndicatorValue(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    value: float
    indicator_name: str  # e.g., "rsi_14", "sma_50", "ema_20"
    symbol: str          # e.g., "BTC/USDT" (standard format with slash)
    exchange: str        # e.g., "binance", "luno"
    # timeframe: Optional[str] = None # e.g., "1h", "4h", "1d" (if needed)

```

**Note on Pydantic Model Usage for Multiple Exchanges:**
The Pydantic models defined (e.g., `Ticker`, `PlaceOrderCommand`, `AccountBalanceEvent`) are intended for use across different exchange connectors.
-   The `exchange` field within each model (where present) should be populated with the name of the respective exchange (e.g., `"binance"`, `"luno"`).
-   The `symbol` field in market data models (like `Ticker`, `OrderBook`, `Trade`) should generally use a standardized format with a slash separator (e.g., "BTC/USDT", "XBT/ZAR"). The connector service is responsible for mapping this to the specific format required by its exchange API if it differs (e.g., "BTCUSDT" for Binance API, "XBTZAR" for Luno API). Stream names, however, use the concatenated format (e.g., `BTCUSDT`, `XBTZAR`).

## 3. Redis Stream Naming Conventions

#### 3.1 Market Data Streams (Connector -> Bot)
-   Pattern: `<exchange_name>:<standard_pair_symbol>:<data_type>`
-   `standard_pair_symbol`: e.g., `BTCUSDT`, `ETHUSDT`, `XBTZAR` (uppercase, no separators)
-   `data_type`: `ticker`, `orderbook`, `trades`
-   **Binance Examples:**
    -   `binance:BTCUSDT:ticker`
    -   `binance:ETHUSDT:orderbook`
-   **Luno Examples:**
    -   `luno:XBTZAR:ticker`
    -   `luno:ETHZAR:orderbook`
    -   (Note: Luno uses pair formats like XBTZAR, ETHZAR. The `standard_pair_symbol` in the stream name reflects this.)

#### 3.2 Command Streams (Bot -> Connector)
-   Pattern: `<target_connector_name>:commands:<command_name>`
-   `target_connector_name`: e.g., `binance_connector`, `luno_connector` (matches service name if possible)
-   `command_name`: e.g., `place_order`, `request_balance`
-   **Binance Connector Examples:**
    -   `binance_connector:commands:place_order`
    -   `binance_connector:commands:request_balance`
-   **Luno Connector Examples:**
    -   `luno_connector:commands:place_order`
    -   `luno_connector:commands:request_balance`

#### 3.3 Event Streams (Connector -> Bot)
-   Pattern: `<source_connector_name>:events:<event_name>`
-   `source_connector_name`: e.g., `binance_connector`, `luno_connector`
-   `event_name`: e.g., `order_response`, `order_status`, `account_balance`
-   **Binance Connector Examples:**
    -   `binance_connector:events:order_response` 
    -   `binance_connector:events:order_status` 
    -   `binance_connector:events:account_balance`
-   **Luno Connector Examples:**
    -   `luno_connector:events:order_response`
    -   `luno_connector:events:order_status`
    -   `luno_connector:events:account_balance`

#### 3.4 Indicator Event Streams (Plugin -> Bot/DecisionMaker)
-   **Pattern:** `indicators:<indicator_full_name>:<exchange_name>:<pair_symbol_concatenated>`
    -   `<indicator_full_name>`: e.g., `rsi_14` (indicator name + main parameter), `sma_50`, `ema_20_close` (if based on close prices).
    -   `<exchange_name>`: e.g., `binance`, `luno`.
    -   `<pair_symbol_concatenated>`: e.g., `BTCUSDT`, `XBTZAR` (uppercase, no separators).
-   **Examples:**
    -   `indicators:rsi_14:binance:BTCUSDT`
    -   `indicators:sma_50:luno:XBTZAR`
-   **Message Model:**
    -   The `data` field of messages on these streams will contain a JSON-serialized `IndicatorValue` Pydantic model (defined in `common_models.indicator_models`).


## 4. Message Content on Streams
-   Each message on a Redis Stream will be a single entry.
-   The entry will have one field named `data`.
-   The value of the `data` field will be the JSON string representation of the corresponding Pydantic model.

Example publishing to `binance_connector:commands:place_order`:
`XADD binance_connector:commands:place_order * data '{"client_order_id": "...", "symbol": "BTC/USDT", ...}'`

Example message on `binance:BTCUSDT:ticker`:
`XADD binance:BTCUSDT:ticker * data '{"symbol": "BTC/USDT", "timestamp": ..., "last_price": ...}'`

```

## 5. Connector-Specific Configuration Notes

### Binance (`binance-connector`)
-   **API Keys:** Requires `BINANCE_API_KEY` and `BINANCE_API_SECRET` environment variables.

### Luno (`luno_connector`)
-   **API Keys:** Luno typically requires an API Key ID and an API Secret Key. If a `luno-connector` service were to be implemented, these would ideally be provided via environment variables, for example:
    -   `LUNO_API_KEY_ID`
    -   `LUNO_API_SECRET_KEY`
    -   (Note: The current `bot` service's Luno integration reads these from `config.cfg` if environment variables are not set.)
