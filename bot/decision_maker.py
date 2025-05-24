import asyncio
import logging
import json
# import ast # No longer used by construct
# import configparser # No longer used by _initialize_luno_exchange
import os
import uuid 
import time 
from datetime import datetime, timezone # Added
# import ccxt.async_support as ccxt # No longer used directly for trading
from storage import Storage
from data_provider import DataProvider # Added
from common_models.commands_events_models import ( 
    PlaceOrderCommand, 
    OrderResponseEvent, 
    OrderStatusUpdateEvent
)
from common_models.indicator_models import IndicatorValue
from prometheus_client import Counter, Gauge, Histogram, exponential_buckets 

logger = logging.getLogger(__name__)

# CHECK_TIMEOUT = 15  # Legacy, Luno direct order wait
DECISION_MAKER_INTERVAL = 10  # Interval for running strategy logic, in seconds

# --- Prometheus Metrics Definitions for DecisionMaker ---
DM_ORDER_COMMANDS_SENT_TOTAL = Counter(
    'dm_order_commands_sent_total', 
    'Total order commands sent by DecisionMaker', 
    ['exchange', 'pair']
)
DM_ORDER_EVENTS_RECEIVED_TOTAL = Counter(
    'dm_order_events_received_total', 
    'Total order events received by DecisionMaker', 
    ['exchange', 'event_type', 'pair']
)
DM_ORDER_E2E_DURATION_SECONDS = Histogram(
    'dm_order_e2e_duration_seconds', 
    'End-to-end time from sending PlaceOrderCommand to receiving terminal order status', 
    ['exchange', 'pair'],
    buckets=exponential_buckets(0.5, 2, 12) # 0.5s to ~1024s (adjust as needed)
)
DM_PENDING_ORDERS_GAUGE = Gauge(
    'dm_pending_orders', 
    'Number of currently pending orders being tracked by DecisionMaker', 
    ['exchange']
)
# --- End Prometheus Metrics Definitions ---

# Redis Stream Names for Connector interaction
BINANCE_CONNECTOR_NAME = "binance_connector"
LUNO_CONNECTOR_NAME = "luno_connector"

BINANCE_PLACE_ORDER_COMMAND_STREAM = f"{BINANCE_CONNECTOR_NAME}:commands:place_order"
BINANCE_ORDER_RESPONSE_EVENT_STREAM = f"{BINANCE_CONNECTOR_NAME}:events:order_response"
BINANCE_ORDER_STATUS_UPDATE_EVENT_STREAM = f"{BINANCE_CONNECTOR_NAME}:events:order_status"

LUNO_PLACE_ORDER_COMMAND_STREAM = f"{LUNO_CONNECTOR_NAME}:commands:place_order"
LUNO_ORDER_RESPONSE_EVENT_STREAM = f"{LUNO_CONNECTOR_NAME}:events:order_response"
LUNO_ORDER_STATUS_UPDATE_EVENT_STREAM = f"{LUNO_CONNECTOR_NAME}:events:order_status"


class DecisionMaker:
    """Handles trading decisions and order placements based on input data from multiple exchanges via Redis."""

    def __init__(self, data_provider: DataProvider): # Modified: Added data_provider
        """
        Initializes the DecisionMaker.
        """
        self.data_provider = data_provider # Added
        self.redis = Storage() 
        self.offset = {'XBT': 0.005, 'XRP': 6.0, 'MYR': 0.0} # Legacy, review if still needed
        
        self.luno_pairs = ["XBT/ZAR", "ETH/ZAR", "BTC/ZAR"] 

        self.pending_orders = {} 
        self.indicator_values = {} 
        
        order_event_streams = {
            BINANCE_ORDER_RESPONSE_EVENT_STREAM: '$',
            BINANCE_ORDER_STATUS_UPDATE_EVENT_STREAM: '$',
            LUNO_ORDER_RESPONSE_EVENT_STREAM: '$',
            LUNO_ORDER_STATUS_UPDATE_EVENT_STREAM: '$'
        }
        
        # TODO: Indicator streams should ideally be discovered or configured more dynamically
        # based on which plugins are active and what they publish.
        indicator_streams_to_consume = {
            "indicators:rsi_14:binance:BTCUSDT": "$",
            "indicators:rsi_14:luno:XBTZAR": "$",
            "indicators:sma_20:binance:BTCUSDT": "$",
            "indicators:sma_50:binance:BTCUSDT": "$"
        }
        
        self.event_stream_ids = {**order_event_streams, **indicator_streams_to_consume}
        
        self.event_consumer_task = asyncio.create_task(self.consume_all_events())
        logger.info(f"DecisionMaker initialized. Event consumer started for streams: {list(self.event_stream_ids.keys())}")

        # Strategy specific parameters
        self.rsi_oversold_threshold = 30
        self.rsi_overbought_threshold = 70
        self.trade_quantity_btc = 0.0001 # Example: trade 0.0001 BTC for Binance
        self.indicator_staleness_threshold_seconds = 300 # 5 minutes

        # Luno MA Crossover Strategy specific parameters
        self.luno_xbt_trade_quantity = 0.0005 # Example: trade 0.0005 XBT for Luno
        self.luno_sma_short_period = 20
        self.luno_sma_long_period = 50
        self.luno_xbtzar_sma_crossed_above: Optional[bool] = None # None = unknown, True = short over long, False = long over short

        # Update indicator_streams_to_consume for Luno SMA streams
        # Ensure these names match what SMAPlugin instances would publish
        # e.g. from plugin_configs.json if SMA_20_luno_XBTZAR and SMA_50_luno_XBTZAR are configured
        indicator_streams_to_consume.update({
            f"indicators:sma_{self.luno_sma_short_period}:luno:XBTZAR": "$",
            f"indicators:sma_{self.luno_sma_long_period}:luno:XBTZAR": "$"
        })
        # Re-assign self.event_stream_ids with the updated indicator streams
        self.event_stream_ids = {**order_event_streams, **indicator_streams_to_consume}
        logger.info(f"DecisionMaker updated event consumer streams: {list(self.event_stream_ids.keys())}")


    async def close_resources(self): 
        """Closes resources and cancels consumer task."""
        if self.event_consumer_task and not self.event_consumer_task.done(): 
            self.event_consumer_task.cancel()
            try:
                await self.event_consumer_task
            except asyncio.CancelledError:
                logger.info("Event consumer task cancelled.") 
            except Exception as e:
                logger.error(f"Error during event consumer task cancellation: {e}") 
        
        logger.info("DecisionMaker resources (consumer task) closed.")

    async def _send_order_command(self, exchange: str, command: PlaceOrderCommand): # New helper
        target_stream = None
        if exchange == "binance":
            target_stream = BINANCE_PLACE_ORDER_COMMAND_STREAM
        elif exchange == "luno":
            target_stream = LUNO_PLACE_ORDER_COMMAND_STREAM
        else:
            logger.error(f"Order command for unknown exchange: {exchange}")
            return

        try:
            self.pending_orders[command.client_order_id] = {
                "exchange": exchange,
                "command": command.model_dump(),
                "status": "pending_submission",
                "exchange_order_id": None,
                "submission_time": time.time()
            }
            await self.redis.storage.xadd(target_stream, {"data": command.model_dump_json()})
            DM_ORDER_COMMANDS_SENT_TOTAL.labels(exchange=exchange, pair=command.symbol).inc()
            DM_PENDING_ORDERS_GAUGE.labels(exchange=exchange).inc()
            logger.info(f"Published PlaceOrderCommand to {target_stream} for {exchange}: client_order_id {command.client_order_id}, {command.symbol}, {command.side}, vol {command.quantity}")
        except Exception as e:
            logger.error(f"Error publishing PlaceOrderCommand to {target_stream} (client_order_id {command.client_order_id}): {e}")
            if command.client_order_id in self.pending_orders:
                # If publish fails, remove from pending and don't inc/dec gauge beyond initial error
                DM_PENDING_ORDERS_GAUGE.labels(exchange=exchange).dec() # Decrement if it was incremented before failure
                del self.pending_orders[command.client_order_id]


    async def _strategy_rsi_binance_btcusdt(self): # New strategy method
        exchange = "binance"
        symbol = "BTC/USDT"
        indicator_key = "rsi_14"
        base_asset = "BTC"
        quote_asset = "USDT"

        logger.debug(f"Running RSI strategy for {exchange}:{symbol}...")

        # 1. Get RSI value
        rsi_event = self.indicator_values.get(exchange, {}).get(symbol, {}).get(indicator_key)
        if not rsi_event:
            logger.info(f"RSI strategy: No RSI data for {exchange}:{symbol}:{indicator_key}. Skipping.")
            return
        
        time_since_rsi = (datetime.now(timezone.utc) - rsi_event.timestamp).total_seconds()
        if time_since_rsi > self.indicator_staleness_threshold_seconds:
            logger.warning(f"RSI strategy: RSI data for {exchange}:{symbol}:{indicator_key} is stale ({time_since_rsi:.0f}s old). Skipping.")
            return
        
        rsi_value = rsi_event.value
        logger.info(f"RSI strategy: Current RSI for {exchange}:{symbol}:{indicator_key} is {rsi_value:.2f}")

        # 2. Get current market price
        market_data = self.data_provider.get_data() # This now returns {"exchange": {"symbol": {"tick": TickerModel, ...}}}
        current_price_data = market_data.get(exchange, {}).get(symbol, {}).get("tick")
        if not current_price_data or not hasattr(current_price_data, 'last_price'):
            logger.warning(f"RSI strategy: No current price (tick data) for {exchange}:{symbol}. Skipping.")
            return
        current_price = current_price_data.last_price
        if current_price is None:
            logger.warning(f"RSI strategy: Current price for {exchange}:{symbol} is None. Skipping.")
            return
        logger.info(f"RSI strategy: Current price for {exchange}:{symbol} is {current_price}")

        # 3. Check for existing open orders for this strategy/pair (simplified)
        # This check needs to be more robust for a real system (e.g. by strategy ID or specific client_order_id pattern)
        for coid, order_info in self.pending_orders.items():
            if order_info.get("exchange") == exchange and \
               order_info["command"].get("symbol") == symbol and \
               order_info["status"] not in ["filled", "canceled", "rejected", "failed", "failed_to_place"]:
                logger.info(f"RSI strategy: Existing open/pending order found for {exchange}:{symbol} (Client ID: {coid}, Status: {order_info['status']}). Skipping new order.")
                return

        # 4. Buy Logic
        if rsi_value < self.rsi_oversold_threshold:
            logger.info(f"RSI strategy: RSI ({rsi_value:.2f}) < Oversold ({self.rsi_oversold_threshold}). Considering BUY for {exchange}:{symbol}.")
            usdt_balance_event = self.data_provider.get_account_balance(exchange, quote_asset)
            if not usdt_balance_event or usdt_balance_event.free < (self.trade_quantity_btc * current_price) * 1.01: # Check for 1% buffer
                logger.info(f"RSI strategy: Insufficient {quote_asset} balance for BUY. Needed approx {(self.trade_quantity_btc * current_price):.2f}, have {usdt_balance_event.free if usdt_balance_event else 'None'}.")
                return

            logger.info(f"RSI strategy: Conditions met for BUY. Available {quote_asset}: {usdt_balance_event.free:.2f}")
            command = PlaceOrderCommand(
                symbol=symbol, type="market", side="buy", quantity=self.trade_quantity_btc
                # Price not needed for market order
            )
            await self._send_order_command(exchange, command)

        # 5. Sell Logic
        elif rsi_value > self.rsi_overbought_threshold:
            logger.info(f"RSI strategy: RSI ({rsi_value:.2f}) > Overbought ({self.rsi_overbought_threshold}). Considering SELL for {exchange}:{symbol}.")
            btc_balance_event = self.data_provider.get_account_balance(exchange, base_asset)
            if not btc_balance_event or btc_balance_event.free < self.trade_quantity_btc:
                logger.info(f"RSI strategy: Insufficient {base_asset} balance for SELL. Needed {self.trade_quantity_btc}, have {btc_balance_event.free if btc_balance_event else 'None'}.")
                return

            logger.info(f"RSI strategy: Conditions met for SELL. Available {base_asset}: {btc_balance_event.free}")
            command = PlaceOrderCommand(
                symbol=symbol, type="market", side="sell", quantity=self.trade_quantity_btc
            )
            await self._send_order_command(exchange, command)
        else:
            logger.info(f"RSI strategy: RSI ({rsi_value:.2f}) is neutral for {exchange}:{symbol}. No action.")


    # Removed: post method (functionality moved to _send_order_command)
    # Removed: decide method (functionality moved to main and specific strategy methods)
    # Removed: construct method


    async def consume_all_events(self): # Renamed from consume_order_events
        logger.info("Starting generic event consumer loop (orders and indicators)...")
        
        while True:
            try:
                messages = await self.redis.storage.xread(
                    streams=self.event_stream_ids, # Now includes order and indicator streams
                    count=10,
                    block=1000 
                )
                if not messages:
                    continue

                for stream_name_bytes, message_list in messages:
                    stream_name_str = stream_name_bytes.decode('utf-8')
                    last_id_processed_for_stream = self.event_stream_ids[stream_name_str]
                    
                    for message_id_bytes, message_data_dict in message_list:
                        message_id_str = message_id_bytes.decode('utf-8')
                        try:
                            data_json = message_data_dict[b'data'].decode('utf-8')
                            
                            if stream_name_str.startswith("indicators:"):
                                event = IndicatorValue.model_validate_json(data_json)
                                # Store the indicator value
                                self.indicator_values.setdefault(event.exchange, {})
                                self.indicator_values[event.exchange].setdefault(event.symbol, {})
                                self.indicator_values[event.exchange][event.symbol][event.indicator_name] = event
                                logger.debug(f"Cached Indicator: {event.exchange} {event.symbol} {event.indicator_name} = {event.value} @ {event.timestamp}")

                            elif ":events:order_response" in stream_name_str or ":events:order_status" in stream_name_str:
                                exchange_name = "unknown"
                                if BINANCE_CONNECTOR_NAME in stream_name_str:
                                    exchange_name = "binance"
                                elif LUNO_CONNECTOR_NAME in stream_name_str:
                                    exchange_name = "luno"

                                if "order_response" in stream_name_str:
                                    event = OrderResponseEvent.model_validate_json(data_json)
                                    DM_ORDER_EVENTS_RECEIVED_TOTAL.labels(exchange=exchange_name, event_type='order_response', pair=event.symbol).inc()
                                    logger.info(f"Received OrderResponseEvent from {exchange_name}: client_order_id {event.client_order_id}, status {event.status}, order_id {event.order_id}")
                                    if event.client_order_id in self.pending_orders:
                                        self.pending_orders[event.client_order_id]['status'] = event.status
                                        self.pending_orders[event.client_order_id]['exchange_order_id'] = event.order_id
                                        if event.error_message:
                                             self.pending_orders[event.client_order_id]['error'] = event.error_message
                                        if event.status.lower() in ["rejected", "failed_to_place"]:
                                            submission_time = self.pending_orders[event.client_order_id].get("submission_time")
                                            if submission_time:
                                                DM_ORDER_E2E_DURATION_SECONDS.labels(exchange=exchange_name, pair=event.symbol).observe(time.time() - submission_time)
                                            DM_PENDING_ORDERS_GAUGE.labels(exchange=exchange_name).dec()
                                            logger.info(f"{exchange_name.capitalize()} order (client_id: {event.client_order_id}) reached terminal state via OrderResponseEvent: {event.status}")
                                    else:
                                        logger.warning(f"Received OrderResponseEvent for unknown client_order_id: {event.client_order_id} from {exchange_name}")

                                elif "order_status" in stream_name_str:
                                    event = OrderStatusUpdateEvent.model_validate_json(data_json)
                                    DM_ORDER_EVENTS_RECEIVED_TOTAL.labels(exchange=exchange_name, event_type='order_status_update', pair=event.symbol).inc()
                                    logger.info(f"Received OrderStatusUpdateEvent from {exchange_name}: order_id {event.order_id}, client_order_id {event.client_order_id}, status {event.status}")
                                    
                                    client_id_to_update = event.client_order_id
                                    if not client_id_to_update: 
                                        for cid, order_details in self.pending_orders.items():
                                            if order_details.get('exchange_order_id') == event.order_id and order_details.get('exchange') == exchange_name:
                                                client_id_to_update = cid
                                                break
                                    
                                    if client_id_to_update and client_id_to_update in self.pending_orders:
                                        pending_order_info = self.pending_orders[client_id_to_update]
                                        pending_order_info['status'] = event.status
                                        pending_order_info['filled_quantity'] = event.filled_quantity
                                        
                                        if event.status.lower() in ["filled", "canceled", "rejected", "expired", "failed"]: 
                                            submission_time = pending_order_info.get("submission_time")
                                            if submission_time:
                                                DM_ORDER_E2E_DURATION_SECONDS.labels(exchange=exchange_name, pair=event.symbol).observe(time.time() - submission_time)
                                            DM_PENDING_ORDERS_GAUGE.labels(exchange=exchange_name).dec()
                                            logger.info(f"{exchange_name.capitalize()} order (client_id: {client_id_to_update}, ex_id: {event.order_id}) reached terminal state: {event.status}. Details: {pending_order_info}")
                                    else:
                                         logger.warning(f"Received OrderStatusUpdateEvent for unknown order from {exchange_name}: order_id {event.order_id}, client_order_id {event.client_order_id}")
                            else:
                                logger.debug(f"Skipping message from unhandled stream: {stream_name_str}")

                            last_id_processed_for_stream = message_id_str
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error for message ID {message_id_str} in stream {stream_name_str}: {e}")
                        except Exception as e: 
                            logger.error(f"Error processing message ID {message_id_str} from stream {stream_name_str}: {e}")
                    
                    self.event_stream_ids[stream_name_str] = last_id_processed_for_stream # Corrected
            
            except Exception as e: 
                logger.error(f"Error in consume_all_events loop: {e}") # Corrected
                await asyncio.sleep(5) 

    # Removed: _wait_luno_order_complete method
    # Removed: _wait_binance_order_complete method

    async def main(self) -> None:
        """Main method to handle the trading process.

        Reads data from Redis, makes decisions, and places orders.

        :raises Exception: If an error occurs during the main execution.
        """
        try:
            data = await self.construct()
            if data:
                await self.decide(data)
        except Exception as e:
            logger.error('Error in main execution: %s', e)
        await asyncio.sleep(SLEEP_DURATION)

