import asyncio
import logging
import os
import uuid 
import json 
import time # Added for timing
from datetime import datetime, timezone
from typing import List, Dict, Optional

import ccxt.async_support as ccxt
from fastapi import FastAPI, HTTPException 
from pydantic import BaseModel, Field, validator 
import redis.asyncio as redis
from prometheus_client import Counter, Gauge, Histogram, exponential_buckets, make_asgi_app # Added

# Import shared Pydantic models
from common_models.market_data_models import Ticker, OrderBook, Trade, OrderBookLevel
from common_models.commands_events_models import (
    PlaceOrderCommand, 
    OrderResponseEvent, 
    OrderStatusUpdateEvent, 
    AccountBalanceEvent
    # RequestBalanceCommand # Defined in common_models but not actively used here yet
)

# Configure logging
# Ensure logging is configured before first use, if not already by Uvicorn/FastAPI
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global constants for market data publishing
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
TICKER_INTERVAL = 5
ORDERBOOK_INTERVAL = 10
TRADES_INTERVAL = 5
ACCOUNT_BALANCE_INTERVAL = 60 # Interval for publishing account balances
ORDER_MONITOR_INTERVAL = 15 # Interval for checking order status in monitor_order_status

# Environment variables
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

# Redis Stream Names
PLACE_ORDER_COMMAND_STREAM = "binance_connector:commands:place_order"
ORDER_RESPONSE_EVENT_STREAM = "binance_connector:events:order_response"
ORDER_STATUS_UPDATE_EVENT_STREAM = "binance_connector:events:order_status"
ACCOUNT_BALANCE_EVENT_STREAM = "binance_connector:events:account_balance"
# REQUEST_BALANCE_COMMAND_STREAM = "binance_connector:commands:request_balance" # Placeholder

# --- Prometheus Metrics Definitions ---
COMMANDS_RECEIVED_TOTAL = Counter(
    'binc_commands_received_total', 
    'Total commands received by binance-connector', 
    ['command_type']
)
EVENTS_PUBLISHED_TOTAL = Counter(
    'binc_events_published_total', 
    'Total events published by binance-connector', 
    ['event_type']
)
ORDER_PROCESSING_DURATION_SECONDS = Histogram(
    'binc_order_processing_duration_seconds', 
    'Time taken to process place_order command and get initial exchange response',
    buckets=exponential_buckets(0.1, 2, 10) # 0.1s to ~50s
)
ACTIVE_ORDER_MONITORS_GAUGE = Gauge(
    'binc_active_order_monitors', 
    'Number of currently active order monitors'
)
EXCHANGE_API_LATENCY_SECONDS = Histogram(
    'binc_exchange_api_latency_seconds', 
    'Latency of API calls to Binance exchange', 
    ['endpoint'],
    buckets=exponential_buckets(0.05, 2, 10) # 0.05s to ~25s
)
# --- End Prometheus Metrics Definitions ---

app = FastAPI(
    title="Binance Connector (Redis API)",
    description="Provides Binance exchange data and trading functions via Redis Streams.",
    version="0.2.0" # Version bump
)

# --- Pydantic Models are now imported from common_models ---


# --- Market Data Fetching Helpers (publish_tickers, etc. remain unchanged from previous step) ---
async def get_ticker_data(ccxt_client: ccxt.Exchange, symbol: str) -> Optional[Ticker]:
    start_time = time.time()
    try:
        ticker_data = await ccxt_client.fetch_ticker(symbol)
        EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_ticker').observe(time.time() - start_time)
        return Ticker(
            symbol=ticker_data['symbol'],
            timestamp=datetime.fromtimestamp(ticker_data['timestamp'] / 1000, tz=timezone.utc),
            last_price=ticker_data['last'],
            bid_price=ticker_data['bid'],
            ask_price=ticker_data['ask'],
            volume_24h=ticker_data.get('baseVolume'),
            exchange="binance"
        )
    except (ccxt.NetworkError, ccxt.ExchangeError, KeyError, TypeError) as e:
        EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_ticker').observe(time.time() - start_time) # Observe even on error
        logger.error(f"Error fetching ticker for {symbol}: {e}")
        return None

async def get_orderbook_data(ccxt_client: ccxt.Exchange, symbol: str) -> Optional[OrderBook]:
    start_time = time.time()
    try:
        orderbook_data = await ccxt_client.fetch_order_book(symbol)
        EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order_book').observe(time.time() - start_time)
        return OrderBook(
            symbol=orderbook_data['symbol'],
            timestamp=datetime.fromtimestamp(orderbook_data['timestamp'] / 1000, tz=timezone.utc),
            bids=[OrderBookLevel(price=b[0], quantity=b[1]) for b in orderbook_data['bids']],
            asks=[OrderBookLevel(price=a[0], quantity=a[1]) for a in orderbook_data['asks']],
            exchange="binance"
        )
    except (ccxt.NetworkError, ccxt.ExchangeError, KeyError, TypeError) as e:
        EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order_book').observe(time.time() - start_time)
        logger.error(f"Error fetching order book for {symbol}: {e}")
        return None

async def get_trades_data(ccxt_client: ccxt.Exchange, symbol: str) -> Optional[List[Trade]]:
    start_time = time.time()
    try:
        trades_data = await ccxt_client.fetch_trades(symbol)
        EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_trades').observe(time.time() - start_time)
        return [
            Trade(
                symbol=trade['symbol'],
                timestamp=datetime.fromtimestamp(trade['timestamp'] / 1000, tz=timezone.utc),
                price=trade['price'],
                quantity=trade['amount'],
                side=trade['side'],
                trade_id=str(trade['id']),
                exchange="binance"
            ) for trade in trades_data
        ]
    except (ccxt.NetworkError, ccxt.ExchangeError, KeyError, TypeError) as e:
        EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_trades').observe(time.time() - start_time)
        logger.error(f"Error fetching trades for {symbol}: {e}")
        return None

# --- Market Data Publishing Loops (publish_tickers, etc. remain unchanged) ---
async def publish_tickers(binance_client: ccxt.Exchange, redis_client: redis.Redis):
    while True:
        for symbol in SYMBOLS:
            ticker = await get_ticker_data(binance_client, symbol) # Latency recorded in helper
            if ticker:
                stream_name = f"binance:{symbol.replace('/', '')}:ticker"
                try:
                    await redis_client.xadd(stream_name, {"data": ticker.model_dump_json()})
                    EVENTS_PUBLISHED_TOTAL.labels(event_type='ticker_data').inc()
                    logger.debug(f"Published ticker for {symbol} to {stream_name}") 
                except Exception as e:
                    logger.error(f"Error publishing ticker for {symbol} to Redis: {e}")
            await asyncio.sleep(0.1)
        await asyncio.sleep(TICKER_INTERVAL)

async def publish_orderbooks(binance_client: ccxt.Exchange, redis_client: redis.Redis):
    while True:
        for symbol in SYMBOLS:
            orderbook = await get_orderbook_data(binance_client, symbol) # Latency recorded in helper
            if orderbook:
                stream_name = f"binance:{symbol.replace('/', '')}:orderbook"
                try:
                    await redis_client.xadd(stream_name, {"data": orderbook.model_dump_json()})
                    EVENTS_PUBLISHED_TOTAL.labels(event_type='orderbook_data').inc()
                    logger.debug(f"Published order book for {symbol} to {stream_name}") 
                except Exception as e:
                    logger.error(f"Error publishing order book for {symbol} to Redis: {e}")
            await asyncio.sleep(0.1)
        await asyncio.sleep(ORDERBOOK_INTERVAL)

async def publish_trades(binance_client: ccxt.Exchange, redis_client: redis.Redis):
    last_trade_ids = {symbol: None for symbol in SYMBOLS}
    while True:
        for symbol in SYMBOLS:
            trades = await get_trades_data(binance_client, symbol) # Latency recorded in helper
            if trades:
                stream_name = f"binance:{symbol.replace('/', '')}:trades"
                new_trades_published_count = 0
                for trade in sorted(trades, key=lambda t: t.timestamp): 
                    is_new_trade = True
                    if trade.trade_id and last_trade_ids[symbol]:
                        try:
                            if int(trade.trade_id) <= int(last_trade_ids[symbol]):
                                is_new_trade = False
                        except ValueError: 
                             if trade.trade_id <= last_trade_ids[symbol]: 
                                is_new_trade = False
                    elif not trade.trade_id : 
                        pass

                    if is_new_trade:
                        try:
                            await redis_client.xadd(stream_name, {"data": trade.model_dump_json()})
                            EVENTS_PUBLISHED_TOTAL.labels(event_type='trade_data').inc()
                            new_trades_published_count +=1
                            if trade.trade_id :
                                last_trade_ids[symbol] = trade.trade_id
                        except Exception as e:
                            logger.error(f"Error publishing trade {trade.trade_id or 'N/A'} for {symbol} to Redis: {e}")
                if new_trades_published_count > 0:
                     logger.info(f"Published {new_trades_published_count} new trades for {symbol} to {stream_name}")
            await asyncio.sleep(0.1)
        await asyncio.sleep(TRADES_INTERVAL)
# --- End Market Data Publishing Loops ---

# --- Order Transformation Helper ---
def _transform_ccxt_order_to_event(ccxt_order: dict, client_order_id_override: Optional[str] = None) -> OrderStatusUpdateEvent:
    """Transforms a CCXT order dictionary to an OrderStatusUpdateEvent Pydantic model."""
    # Extract trades if available and transform them
    ccxt_trades = ccxt_order.get('trades', [])
    pydantic_trades = []
    if ccxt_trades:
        for t in ccxt_trades:
            pydantic_trades.append(Trade(
                symbol=t['symbol'],
                timestamp=datetime.fromtimestamp(t['timestamp'] / 1000, tz=timezone.utc),
                price=t['price'],
                quantity=t['amount'],
                side=t['side'],
                trade_id=str(t['id']),
                exchange="binance" # Assuming binance
            ))

    return OrderStatusUpdateEvent(
        order_id=str(ccxt_order['id']),
        client_order_id=client_order_id_override or ccxt_order.get('clientOrderId'),
        symbol=ccxt_order['symbol'],
        status=ccxt_order['status'],
        filled_quantity=float(ccxt_order.get('filled', 0.0)),
        remaining_quantity=float(ccxt_order.get('remaining', 0.0)),
        average_price=float(ccxt_order.get('average')) if ccxt_order.get('average') is not None else None,
        trades=pydantic_trades if pydantic_trades else None,
        fee=ccxt_order.get('fee'), # CCXT fee structure: {'currency': 'USDT', 'cost': 0.1, 'rate': 0.001}
        timestamp=datetime.fromtimestamp(ccxt_order.get('lastTradeTimestamp', ccxt_order['timestamp']) / 1000, tz=timezone.utc),
        exchange="binance"
    )

# --- Command Consumers and Event Publishers ---
async def consume_place_order_commands(binance_client: ccxt.Exchange, redis_client: redis.Redis):
    logger.info("Starting Place Order Command consumer...")
    # Using '0-0' to process all historical messages if service restarts and didn't ack.
    # For production, a persistent consumer group is better.
    last_processed_id = '0-0' 
    stream_key_map = {PLACE_ORDER_COMMAND_STREAM: last_processed_id}

    while True:
        try:
            # XREAD instead of XREADGROUP for simplicity here. Consumer groups are more robust.
            messages = await redis_client.xread(
                streams=stream_key_map,
                count=10, # Process up to 10 commands at a time
                block=1000  # Block for 1 second
            )
            if not messages:
                continue

            for stream_name, command_list in messages:
                for command_id_bytes, command_data_dict in command_list:
                    command_id = command_id_bytes.decode('utf-8')
                    logger.info(f"Received command {command_id} from {stream_name.decode('utf-8')}")
                    try:
                        data_json = command_data_dict[b'data'].decode('utf-8')
                        command = PlaceOrderCommand.model_validate_json(data_json)
                        COMMANDS_RECEIVED_TOTAL.labels(command_type='place_order').inc()

                        order_response_event_data = {
                            "client_order_id": command.client_order_id,
                            "symbol": command.symbol,
                            "requested_quantity": command.quantity,
                            "requested_price": command.price,
                            "side": command.side,
                            "type": command.type,
                        }

                        try:
                            order_proc_start_time = time.time()
                            ccxt_order_response = await binance_client.create_order(
                                symbol=command.symbol,
                                type=command.type,
                                side=command.side,
                                amount=command.quantity,
                                price=command.price,
                                params={'newClientOrderId': command.client_order_id}
                            )
                            EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='create_order').observe(time.time() - order_proc_start_time)
                            ORDER_PROCESSING_DURATION_SECONDS.observe(time.time() - order_proc_start_time)
                            
                            logger.info(f"Order placed via CCXT: {ccxt_order_response['id']} for client_order_id: {command.client_order_id}")
                            order_response_event_data["order_id"] = str(ccxt_order_response['id'])
                            order_response_event_data["status"] = "accepted"
                            order_response_event_data["filled_quantity"] = float(ccxt_order_response.get('filled', 0.0))
                            
                            ACTIVE_ORDER_MONITORS_GAUGE.inc()
                            monitor_task = asyncio.create_task(
                                monitor_order_status(
                                    order_id=str(ccxt_order_response['id']),
                                    client_order_id=command.client_order_id,
                                    symbol=command.symbol,
                                    binance_client=binance_client,
                                    redis_client=redis_client
                                )
                            )
                            app.state.active_order_monitors[command.client_order_id] = monitor_task
                            logger.info(f"Started monitoring task for order {ccxt_order_response['id']} (client_order_id: {command.client_order_id})")

                        except (ccxt.NetworkError, ccxt.ExchangeError, ccxt.InsufficientFunds, ccxt.InvalidOrder) as e:
                            if 'order_proc_start_time' in locals(): # Check if timer started
                                EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='create_order').observe(time.time() - order_proc_start_time)
                                ORDER_PROCESSING_DURATION_SECONDS.observe(time.time() - order_proc_start_time)
                            logger.error(f"Failed to place order for client_order_id {command.client_order_id}: {e}")
                            order_response_event_data["status"] = "rejected" 
                            order_response_event_data["error_message"] = str(e)
                        except Exception as e:
                            if 'order_proc_start_time' in locals(): # Check if timer started
                                EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='create_order').observe(time.time() - order_proc_start_time)
                                ORDER_PROCESSING_DURATION_SECONDS.observe(time.time() - order_proc_start_time)
                            logger.error(f"Unexpected error placing order for client_order_id {command.client_order_id}: {e}")
                            order_response_event_data["status"] = "failed_to_place"
                            order_response_event_data["error_message"] = str(e)

                        response_event = OrderResponseEvent(**order_response_event_data)
                        await redis_client.xadd(ORDER_RESPONSE_EVENT_STREAM, {"data": response_event.model_dump_json()})
                        EVENTS_PUBLISHED_TOTAL.labels(event_type='order_response').inc()
                        logger.info(f"Published OrderResponseEvent for client_order_id: {command.client_order_id}, Status: {response_event.status}")
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error for command {command_id}: {e}")
                        COMMANDS_RECEIVED_TOTAL.labels(command_type='place_order_invalid_json').inc() # Or a general error label
                    except Exception as e: 
                        logger.error(f"Error processing command {command_id}: {e}")
                        COMMANDS_RECEIVED_TOTAL.labels(command_type='place_order_processing_error').inc() 
                    
                    # Update last processed ID for this stream
                    stream_key_map[stream_name] = command_id 
        
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Redis connection error in command consumer: {e}. Reconnecting...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"Unexpected error in command consumer loop: {e}")
            await asyncio.sleep(5)


async def monitor_order_status(order_id: str, client_order_id: str, symbol: str, binance_client: ccxt.Exchange, redis_client: redis.Redis):
    logger.info(f"Monitoring order {order_id} (Client ID: {client_order_id}, Symbol: {symbol})")
    terminal_statuses = ["closed", "canceled", "rejected", "expired", "failed"] 
    try:
        while True:
            await asyncio.sleep(ORDER_MONITOR_INTERVAL)
            api_call_start_time = time.time()
            try:
                ccxt_order = await binance_client.fetch_order(order_id, symbol)
                EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order').observe(time.time() - api_call_start_time)
                order_status_event = _transform_ccxt_order_to_event(ccxt_order, client_order_id_override=client_order_id)
                
                await redis_client.xadd(ORDER_STATUS_UPDATE_EVENT_STREAM, {"data": order_status_event.model_dump_json()})
                EVENTS_PUBLISHED_TOTAL.labels(event_type='order_status_update').inc()
                logger.info(f"Published OrderStatusUpdateEvent for order {order_id}, Status: {order_status_event.status}")

                if order_status_event.status.lower() in terminal_statuses:
                    logger.info(f"Order {order_id} reached terminal state: {order_status_event.status}. Stopping monitor.")
                    break
            
            except ccxt.OrderNotFound:
                EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order').observe(time.time() - api_call_start_time) # Observe even on specific errors
                logger.warning(f"Order {order_id} (Symbol: {symbol}) not found during monitoring. Assuming it was canceled or never fully placed. Stopping monitor.")
                synthetic_event = OrderStatusUpdateEvent(
                    order_id=order_id, client_order_id=client_order_id, symbol=symbol,
                    status="failed", 
                    filled_quantity=0, remaining_quantity=0, 
                    exchange="binance"
                )
                await redis_client.xadd(ORDER_STATUS_UPDATE_EVENT_STREAM, {"data": synthetic_event.model_dump_json()})
                EVENTS_PUBLISHED_TOTAL.labels(event_type='order_status_update_synthetic_failed').inc()
                break
            except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_order').observe(time.time() - api_call_start_time)
                logger.error(f"API error monitoring order {order_id}: {e}. Will retry.")
            except Exception as e:
                logger.error(f"Unexpected error monitoring order {order_id}: {e}. Stopping monitor.")
                break 
    finally:
        ACTIVE_ORDER_MONITORS_GAUGE.dec()
        if client_order_id in app.state.active_order_monitors:
            del app.state.active_order_monitors[client_order_id]
            logger.info(f"Removed monitor for order {order_id} (Client ID: {client_order_id}) from active list.")

async def publish_account_balances(binance_client: ccxt.Exchange, redis_client: redis.Redis):
    logger.info("Starting Account Balance publisher...")
    while True:
        api_call_start_time = time.time()
        try:
            raw_balances = await binance_client.fetch_balance()
            EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_balance').observe(time.time() - api_call_start_time)
            if raw_balances.get('total'):
                for asset, total_amount_str in raw_balances['total'].items():
                    total_amount = float(total_amount_str)
                    if total_amount > 0: 
                        free_amount = float(raw_balances.get('free', {}).get(asset, "0"))
                        locked_amount = float(raw_balances.get('used', {}).get(asset, "0"))
                        
                        balance_event = AccountBalanceEvent(
                            asset=asset,
                            free=free_amount,
                            locked=locked_amount,
                            total=total_amount 
                        )
                        await redis_client.xadd(ACCOUNT_BALANCE_EVENT_STREAM, {"data": balance_event.model_dump_json()})
                        EVENTS_PUBLISHED_TOTAL.labels(event_type='account_balance').inc()
                        logger.debug(f"Published AccountBalanceEvent for {asset}")
                logger.info(f"Published account balances for {len(raw_balances['total'])} assets.")
        except (ccxt.NetworkError, ccxt.ExchangeError) as e:
            EXCHANGE_API_LATENCY_SECONDS.labels(endpoint='fetch_balance').observe(time.time() - api_call_start_time)
            logger.error(f"API error publishing account balances: {e}")
        except Exception as e:
            logger.error(f"Unexpected error publishing account balances: {e}")
        await asyncio.sleep(ACCOUNT_BALANCE_INTERVAL)

# --- FastAPI Lifecycle Events ---
@app.on_event("startup")
async def startup_event():
    logger.info("Binance Connector (Redis API) starting up...")
    app.state.binance_client = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_API_SECRET,
        'enableRateLimit': True,
    })
    logger.info("Binance CCXT client initialized.")

    try:
        app.state.redis_client = await redis.from_url(f"redis://{REDIS_HOST}:{REDIS_PORT}")
        await app.state.redis_client.ping()
        logger.info("Redis client initialized and connection verified.")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}. Service will be unhealthy.")
        app.state.redis_client = None # Ensure it's None if connection failed

    app.state.background_tasks = []
    app.state.active_order_monitors = {} # For tracking dynamic monitor tasks

    if app.state.redis_client:
        # Market data publishing tasks
        app.state.background_tasks.append(
            asyncio.create_task(publish_tickers(app.state.binance_client, app.state.redis_client))
        )
        app.state.background_tasks.append(
            asyncio.create_task(publish_orderbooks(app.state.binance_client, app.state.redis_client))
        )
        app.state.background_tasks.append(
            asyncio.create_task(publish_trades(app.state.binance_client, app.state.redis_client))
        )
        # Command consumer and other event publisher tasks
        app.state.background_tasks.append(
            asyncio.create_task(consume_place_order_commands(app.state.binance_client, app.state.redis_client))
        )
        app.state.background_tasks.append(
            asyncio.create_task(publish_account_balances(app.state.binance_client, app.state.redis_client))
        )
        logger.info(f"Started {len(app.state.background_tasks)} background tasks.")
    else:
        logger.warning("Redis client not available. Background tasks (including command processing and data publishing) will not be started.")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Binance Connector (Redis API) shutting down...")
    
    # Cancel dynamically created order monitor tasks
    active_monitors = list(app.state.active_order_monitors.values()) # Get a list before iterating dict
    for task in active_monitors:
        if not task.done():
            task.cancel()
    if active_monitors:
        await asyncio.gather(*[task for task in active_monitors if not task.done()], return_exceptions=True)
        logger.info(f"Cancelled and awaited {len(active_monitors)} active order monitor tasks.")

    # Cancel main background tasks
    if hasattr(app.state, 'background_tasks'):
        for task in app.state.background_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*[task for task in app.state.background_tasks if not task.done()], return_exceptions=True)
        logger.info(f"Cancelled and awaited {len(app.state.background_tasks)} background tasks.")

    if hasattr(app.state, 'binance_client') and app.state.binance_client:
        await app.state.binance_client.close()
        logger.info("Binance CCXT client closed.")
    if hasattr(app.state, 'redis_client') and app.state.redis_client:
        await app.state.redis_client.close()
        logger.info("Redis client closed.")

# --- Health Check Endpoint (Remains) ---
@app.get("/health", summary="Health Check", tags=["General"])
async def health_check():
    redis_connected = False
    if hasattr(app.state, 'redis_client') and app.state.redis_client:
        try:
            await app.state.redis_client.ping()
            redis_connected = True
        except Exception:
            redis_connected = False
            
    binance_api_status = "unknown"
    if hasattr(app.state, 'binance_client') and app.state.binance_client:
        try:
            server_time = await app.state.binance_client.fetch_time()
            binance_api_status = "connected" if server_time else "error_fetching_time"
        except Exception as e:
            logger.error(f"Health check: Binance API error: {e}")
            binance_api_status = "error"
    else:
        binance_api_status = "client_not_initialized"
        
    # Check status of background tasks
    task_statuses = {}
    all_tasks_healthy = True
    if hasattr(app.state, 'background_tasks'):
        for i, task in enumerate(app.state.background_tasks):
            task_name = f"task_{i}" # Generic name, can be improved if tasks are named
            if hasattr(task, 'get_name'): task_name = task.get_name()

            if task.done():
                try:
                    task.result() # Access result to raise exception if task failed
                    task_statuses[task_name] = "completed_unexpectedly"
                    all_tasks_healthy = False
                except asyncio.CancelledError:
                    task_statuses[task_name] = "cancelled" # Should only happen at shutdown
                except Exception as e:
                    task_statuses[task_name] = f"failed: {str(e)}"
                    all_tasks_healthy = False
            else:
                task_statuses[task_name] = "running"
    
    overall_status = "healthy"
    if not redis_connected or binance_api_status != "connected" or not all_tasks_healthy:
        overall_status = "degraded"
        
    return {
        "status": overall_status,
        "redis_status": "connected" if redis_connected else "disconnected",
        "binance_api_status": binance_api_status,
        "background_tasks": task_statuses,
        "active_order_monitors": len(app.state.active_order_monitors if hasattr(app.state, 'active_order_monitors') else {})
    }


# Removed HTTP action endpoints: /account/balance, /orders, /orders/{order_id}

# Mount the Prometheus ASGI app on /metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
