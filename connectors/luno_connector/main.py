import asyncio
import logging
import os
import uuid
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional

import ccxt.async_support as ccxt
from fastapi import FastAPI, HTTPException
import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram, exponential_buckets, make_asgi_app, REGISTRY

# Import shared Pydantic models
from common_models.market_data_models import Ticker, OrderBook, Trade, OrderBookLevel
from common_models.commands_events_models import (
    PlaceOrderCommand, 
    OrderResponseEvent, 
    OrderStatusUpdateEvent, 
    AccountBalanceEvent
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Luno Specific Configuration & Constants ---
LUNO_SYMBOLS_MAP = { # Maps standard "BASE/QUOTE" to Luno's "BASEQUOTE" for API calls
    "XBT/ZAR": "XBTZAR",
    "ETH/ZAR": "ETHZAR",
    "BTC/ZAR": "XBTZAR", # Alias for XBT/ZAR
    # Add other Luno pairs as needed
}
LUNO_SYMBOLS_STANDARD = list(LUNO_SYMBOLS_MAP.keys()) # Used for publishing with standard format

TICKER_INTERVAL_LUNO = 10  # Luno has stricter rate limits
ORDERBOOK_INTERVAL_LUNO = 15
TRADES_INTERVAL_LUNO = 10
ACCOUNT_BALANCE_INTERVAL_LUNO = 120 # Luno balance updates less frequently
ORDER_MONITOR_INTERVAL_LUNO = 20

# Environment variables for Luno
LUNO_API_KEY_ID = os.getenv("LUNO_API_KEY_ID")
LUNO_API_SECRET_KEY = os.getenv("LUNO_API_SECRET_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Redis Stream Names for Luno
LUNO_CONNECTOR_NAME = "luno_connector" # Used in stream names
LUNO_PLACE_ORDER_COMMAND_STREAM = f"{LUNO_CONNECTOR_NAME}:commands:place_order"
LUNO_ORDER_RESPONSE_EVENT_STREAM = f"{LUNO_CONNECTOR_NAME}:events:order_response"
LUNO_ORDER_STATUS_UPDATE_EVENT_STREAM = f"{LUNO_CONNECTOR_NAME}:events:order_status"
LUNO_ACCOUNT_BALANCE_EVENT_STREAM = f"{LUNO_CONNECTOR_NAME}:events:account_balance"

# --- Prometheus Metrics Definitions for Luno Connector ---
LC_COMMANDS_RECEIVED_TOTAL = Counter(
    'lc_commands_received_total', 'Total commands received by luno-connector', ['command_type']
)
LC_EVENTS_PUBLISHED_TOTAL = Counter(
    'lc_events_published_total', 'Total events published by luno-connector', ['event_type']
)
LC_ORDER_PROCESSING_DURATION_SECONDS = Histogram(
    'lc_order_processing_duration_seconds', 'Time to process place_order command for Luno',
    buckets=exponential_buckets(0.1, 2, 10)
)
LC_ACTIVE_ORDER_MONITORS_GAUGE = Gauge(
    'lc_active_order_monitors', 'Number of active Luno order monitors'
)
LC_EXCHANGE_API_LATENCY_SECONDS = Histogram(
    'lc_exchange_api_latency_seconds', 'Latency of API calls to Luno exchange', ['endpoint'],
    buckets=exponential_buckets(0.05, 2, 10)
)
# --- End Prometheus Metrics ---

app = FastAPI(
    title="Luno Connector (Redis API)",
    description="Provides Luno exchange data and trading functions via Redis Streams.",
    version="0.1.0"
)

# --- CCXT Luno Order Transformation Helper ---
def _transform_luno_order_to_event(luno_order: dict, client_order_id_override: Optional[str] = None) -> OrderStatusUpdateEvent:
    ccxt_trades = luno_order.get('trades', [])
    pydantic_trades = []
    if ccxt_trades:
        for t in ccxt_trades:
            pydantic_trades.append(Trade(
                symbol=luno_order['symbol'], # Luno often returns symbol in standard format here
                timestamp=datetime.fromtimestamp(t['timestamp'] / 1000, tz=timezone.utc),
                price=t['price'],
                quantity=t['amount'],
                side=t['side'],
                trade_id=str(t['id']),
                exchange="luno"
            ))
    return OrderStatusUpdateEvent(
        order_id=str(luno_order['id']),
        client_order_id=client_order_id_override or luno_order.get('clientOrderId'), # Luno doesn't support clientOrderId directly
        symbol=luno_order['symbol'],
        status=luno_order['status'],
        filled_quantity=float(luno_order.get('filled', 0.0)),
        remaining_quantity=float(luno_order.get('remaining', 0.0)),
        average_price=float(luno_order.get('average')) if luno_order.get('average') is not None else None,
        trades=pydantic_trades if pydantic_trades else None,
        fee=luno_order.get('fee'),
        timestamp=datetime.fromtimestamp(luno_order.get('lastTradeTimestamp', luno_order['timestamp']) / 1000, tz=timezone.utc),
        exchange="luno"
    )

# --- Market Data Publishing ---
async def publish_luno_tickers(luno_client: ccxt.luno, redis_client: redis.Redis):
    while True:
        for std_symbol in LUNO_SYMBOLS_STANDARD:
            luno_api_symbol = LUNO_SYMBOLS_MAP.get(std_symbol)
            if not luno_api_symbol: continue
            
            start_time = time.time()
            try:
                ticker_data = await luno_client.fetch_ticker(luno_api_symbol)
                LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_ticker').observe(time.time() - start_time)
                
                model = Ticker(
                    symbol=std_symbol, # Use standard symbol for publishing
                    timestamp=datetime.fromtimestamp(ticker_data['timestamp'] / 1000, tz=timezone.utc),
                    last_price=ticker_data['last'],
                    bid_price=ticker_data['bid'],
                    ask_price=ticker_data['ask'],
                    volume_24h=ticker_data.get('baseVolume'),
                    exchange="luno"
                )
                stream_name = f"luno:{luno_api_symbol}:ticker"
                await redis_client.xadd(stream_name, {"data": model.model_dump_json()})
                LC_EVENTS_PUBLISHED_TOTAL.labels(event_type='ticker_data').inc()
                logger.debug(f"Published Luno ticker for {std_symbol} to {stream_name}")
            except (ccxt.NetworkError, ccxt.ExchangeError, KeyError, TypeError) as e:
                LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_ticker').observe(time.time() - start_time)
                logger.error(f"Error fetching Luno ticker for {std_symbol}: {e}")
            await asyncio.sleep(1) # Rate limit between symbols
        await asyncio.sleep(TICKER_INTERVAL_LUNO)

# Similar implementations for publish_luno_orderbooks and publish_luno_trades,
# remembering to use LUNO_SYMBOLS_MAP for API calls and std_symbol for publishing.

async def publish_luno_account_balances(luno_client: ccxt.luno, redis_client: redis.Redis):
    logger.info("Starting Luno Account Balance publisher...")
    while True:
        api_call_start_time = time.time()
        try:
            raw_balances = await luno_client.fetch_balance()
            LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_balance').observe(time.time() - api_call_start_time)
            if raw_balances.get('total'):
                for asset, total_amount in raw_balances['total'].items():
                    if total_amount > 0:
                        balance_event = AccountBalanceEvent(
                            exchange="luno",
                            asset=asset,
                            free=float(raw_balances.get('free', {}).get(asset, 0.0)),
                            locked=float(raw_balances.get('used', {}).get(asset, 0.0)),
                            total=float(total_amount),
                        )
                        await redis_client.xadd(LUNO_ACCOUNT_BALANCE_EVENT_STREAM, {"data": balance_event.model_dump_json()})
                        LC_EVENTS_PUBLISHED_TOTAL.labels(event_type='account_balance').inc()
                logger.info(f"Published Luno account balances for {len(raw_balances['total'])} assets.")
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_balance').observe(time.time() - api_call_start_time)
            logger.error(f"Luno API error publishing account balances: {e}")
        except Exception as e:
            logger.error(f"Unexpected error publishing Luno account balances: {e}")
        await asyncio.sleep(ACCOUNT_BALANCE_INTERVAL_LUNO)

async def consume_luno_place_order_commands(luno_client: ccxt.luno, redis_client: redis.Redis):
    logger.info("Starting Luno Place Order Command consumer...")
    last_processed_id = '0-0'
    stream_key_map = {LUNO_PLACE_ORDER_COMMAND_STREAM: last_processed_id}
    while True:
        try:
            messages = await redis_client.xread(streams=stream_key_map, count=10, block=1000)
            if not messages: continue

            for stream_name_bytes, command_list in messages:
                stream_name_str = stream_name_bytes.decode('utf-8')
                for command_id_bytes, command_data_dict in command_list:
                    command_id_str = command_id_bytes.decode('utf-8')
                    LC_COMMANDS_RECEIVED_TOTAL.labels(command_type='place_order').inc()
                    try:
                        command = PlaceOrderCommand.model_validate_json(command_data_dict[b'data'])
                        luno_api_symbol = LUNO_SYMBOLS_MAP.get(command.symbol)
                        if not luno_api_symbol:
                            raise ValueError(f"Symbol {command.symbol} not mapped for Luno.")

                        order_resp_data = {
                            "client_order_id": command.client_order_id, "symbol": command.symbol,
                            "requested_quantity": command.quantity, "requested_price": command.price,
                            "side": command.side, "type": command.type
                        }
                        order_proc_start_time = time.time()
                        try:
                            # Luno CCXT does not support clientOrderId param directly in create_order
                            # Correlation will be managed by bot using client_order_id and returned exchange order_id
                            ccxt_order = await luno_client.create_order(
                                symbol=luno_api_symbol, type=command.type, side=command.side,
                                amount=command.quantity, price=command.price
                            )
                            LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='create_order').observe(time.time() - order_proc_start_time)
                            LC_ORDER_PROCESSING_DURATION_SECONDS.observe(time.time() - order_proc_start_time)
                            
                            order_resp_data.update({"order_id": str(ccxt_order['id']), "status": "accepted", "filled_quantity": float(ccxt_order.get('filled', 0.0))})
                            LC_ACTIVE_ORDER_MONITORS_GAUGE.inc()
                            monitor_task = asyncio.create_task(monitor_luno_order_status(
                                str(ccxt_order['id']), command.client_order_id, command.symbol, luno_client, redis_client
                            ))
                            app.state.active_order_monitors[command.client_order_id] = monitor_task
                        except Exception as e:
                            if 'order_proc_start_time' in locals():
                                LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='create_order').observe(time.time() - order_proc_start_time)
                                LC_ORDER_PROCESSING_DURATION_SECONDS.observe(time.time() - order_proc_start_time)
                            logger.error(f"Luno order placement failed for client_order_id {command.client_order_id}: {e}")
                            order_resp_data.update({"status": "rejected", "error_message": str(e)})
                        
                        response_event = OrderResponseEvent(**order_resp_data)
                        await redis_client.xadd(LUNO_ORDER_RESPONSE_EVENT_STREAM, {"data": response_event.model_dump_json()})
                        LC_EVENTS_PUBLISHED_TOTAL.labels(event_type='order_response').inc()
                    except Exception as e:
                        logger.error(f"Error processing Luno command {command_id_str}: {e}")
                    stream_key_map[stream_name_str] = command_id_str
        except Exception as e:
            logger.error(f"Error in Luno command consumer loop: {e}")
            await asyncio.sleep(5)

async def monitor_luno_order_status(order_id: str, client_order_id: str, symbol: str, luno_client: ccxt.luno, redis_client: redis.Redis):
    logger.info(f"Monitoring Luno order {order_id} (Client ID: {client_order_id}, Symbol: {symbol})")
    try:
        while True:
            await asyncio.sleep(ORDER_MONITOR_INTERVAL_LUNO)
            api_call_start_time = time.time()
            try:
                # Luno's fetch_order might require the Luno-specific pair format for symbol
                luno_api_symbol = LUNO_SYMBOLS_MAP.get(symbol, symbol) # Fallback to provided symbol if not in map
                ccxt_order = await luno_client.fetch_order(order_id, luno_api_symbol)
                LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order').observe(time.time() - api_call_start_time)
                
                order_event = _transform_luno_order_to_event(ccxt_order, client_order_id_override=client_order_id)
                await redis_client.xadd(LUNO_ORDER_STATUS_UPDATE_EVENT_STREAM, {"data": order_event.model_dump_json()})
                LC_EVENTS_PUBLISHED_TOTAL.labels(event_type='order_status_update').inc()

                if order_event.status.lower() in ["closed", "canceled", "rejected", "failed"]: # Luno might use 'complete' for filled
                    logger.info(f"Luno order {order_id} reached terminal state: {order_event.status}")
                    break
            except ccxt.OrderNotFound:
                LC_EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order').observe(time.time() - api_call_start_time)
                logger.warning(f"Luno order {order_id} not found. Assuming failed/canceled.")
                synthetic_event = OrderStatusUpdateEvent(
                    order_id=order_id, client_order_id=client_order_id, symbol=symbol, status="failed",
                    filled_quantity=0, remaining_quantity=0, exchange="luno"
                )
                await redis_client.xadd(LUNO_ORDER_STATUS_UPDATE_EVENT_STREAM, {"data": synthetic_event.model_dump_json()})
                LC_EVENTS_PUBLISHED_TOTAL.labels(event_type='order_status_update_synthetic_failed').inc()
                break
            except Exception as e:
                logger.error(f"Error monitoring Luno order {order_id}: {e}")
                # Decide if to break or continue; for now, continue retrying
    finally:
        LC_ACTIVE_ORDER_MONITORS_GAUGE.dec()
        if client_order_id in app.state.active_order_monitors:
            del app.state.active_order_monitors[client_order_id]

@app.on_event("startup")
async def startup_event():
    logger.info("Luno Connector (Redis API) starting up...")
    if not LUNO_API_KEY_ID or not LUNO_API_SECRET_KEY:
        logger.error("Luno API Key ID or Secret Key not configured. Luno client will not be initialized.")
        app.state.luno_client = None
    else:
        app.state.luno_client = ccxt.luno({
            'apiKey': LUNO_API_KEY_ID,
            'secret': LUNO_API_SECRET_KEY,
            'enableRateLimit': True,
        })
        logger.info("Luno CCXT client initialized.")

    try:
        app.state.redis_client = await redis.from_url(f"redis://{REDIS_HOST}:{REDIS_PORT}")
        await app.state.redis_client.ping()
        logger.info("Redis client initialized and connection verified.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}. Service will be unhealthy.")
        app.state.redis_client = None

    app.state.background_tasks = []
    app.state.active_order_monitors = {}

    if app.state.redis_client and app.state.luno_client:
        app.state.background_tasks.append(asyncio.create_task(publish_luno_tickers(app.state.luno_client, app.state.redis_client)))
        # Add publish_luno_orderbooks and publish_luno_trades when implemented
        app.state.background_tasks.append(asyncio.create_task(publish_luno_account_balances(app.state.luno_client, app.state.redis_client)))
        app.state.background_tasks.append(asyncio.create_task(consume_luno_place_order_commands(app.state.luno_client, app.state.redis_client)))
        logger.info(f"Started {len(app.state.background_tasks)} Luno background tasks.")
    else:
        logger.warning("Redis client or Luno client not available. Luno background tasks not started.")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Luno Connector (Redis API) shutting down...")
    active_monitors = list(app.state.active_order_monitors.values())
    for task in active_monitors + app.state.background_tasks:
        if not task.done():
            task.cancel()
    if active_monitors:
        await asyncio.gather(*[t for t in active_monitors if not t.done()], return_exceptions=True)
    await asyncio.gather(*[t for t in app.state.background_tasks if not t.done()], return_exceptions=True)
    
    if hasattr(app.state, 'luno_client') and app.state.luno_client:
        await app.state.luno_client.close()
    if hasattr(app.state, 'redis_client') and app.state.redis_client:
        await app.state.redis_client.close()

@app.get("/health", summary="Health Check", tags=["General"])
async def health_check():
    redis_ok = False
    if hasattr(app.state, 'redis_client') and app.state.redis_client:
        try:
            await app.state.redis_client.ping(); redis_ok = True
        except: pass
            
    luno_ok = False
    if hasattr(app.state, 'luno_client') and app.state.luno_client:
        try:
            await app.state.luno_client.fetch_status(); luno_ok = True # Luno status check
        except: pass
        
    tasks_ok = all(not task.done() for task in getattr(app.state, 'background_tasks', []))
    
    status = "healthy" if redis_ok and luno_ok and tasks_ok else "degraded"
    return {
        "status": status, "redis_status": "connected" if redis_ok else "disconnected",
        "luno_api_status": "connected" if luno_ok else "error",
        "background_tasks_running": len(getattr(app.state, 'background_tasks', [])),
        "active_order_monitors": len(getattr(app.state, 'active_order_monitors', {}))
    }

metrics_app = make_asgi_app(REGISTRY)
app.mount("/metrics", metrics_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
