import asyncio
import logging
import json
import ast
import configparser
import os
import uuid 
import time # Added for timing E2E latency
# Removed: import aiohttp 
import ccxt.async_support as ccxt # Keep for Luno
from storage import Storage
from common_models.commands_events_models import ( 
    PlaceOrderCommand, 
    OrderResponseEvent, 
    OrderStatusUpdateEvent
)
from prometheus_client import Counter, Gauge, Histogram, exponential_buckets # Added

logger = logging.getLogger(__name__)

CHECK_TIMEOUT = 15  # Timeout for Luno order status (if used)
SLEEP_DURATION = 60  # Duration to sleep in the main method

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

    def __init__(self, check_timeout: int = CHECK_TIMEOUT): # check_timeout is legacy for Luno direct
        """
        Initializes the DecisionMaker.
        """
        self.check_timeout = check_timeout 
        self.redis = Storage() 
        # self.luno_exchange = self._initialize_luno_exchange() # Removed direct Luno client
        self.offset = {'XBT': 0.005, 'XRP': 6.0, 'MYR': 0.0} # Legacy offset logic, might need review
        
        self.luno_pairs = ["XBT/ZAR", "ETH/ZAR", "BTC/ZAR"] # Configurable list of Luno pairs

        self.pending_orders = {} # Generalized: client_order_id -> {exchange, command, status, submission_time, exchange_order_id}
        
        self.order_event_stream_ids = {
            BINANCE_ORDER_RESPONSE_EVENT_STREAM: '$',
            BINANCE_ORDER_STATUS_UPDATE_EVENT_STREAM: '$',
            LUNO_ORDER_RESPONSE_EVENT_STREAM: '$',
            LUNO_ORDER_STATUS_UPDATE_EVENT_STREAM: '$'
        }
        self.order_event_consumer_task = asyncio.create_task(self.consume_order_events())
        logger.info("DecisionMaker initialized. Generic order event consumer started for Binance and Luno.")

    # Removed: _initialize_luno_exchange method

    async def close_resources(self): 
        """Closes resources and cancels consumer task."""
        if self.order_event_consumer_task and not self.order_event_consumer_task.done():
            self.order_event_consumer_task.cancel()
            try:
                await self.order_event_consumer_task
            except asyncio.CancelledError:
                logger.info("Order event consumer task cancelled.")
            except Exception as e:
                logger.error(f"Error during order event consumer task cancellation: {e}")
        
        # Removed: self.luno_exchange.close()
        logger.info("DecisionMaker resources (consumer task) closed.")


    async def construct(self) -> dict:
        """Reads and parses data from Redis for trading decisions.

        :return: Parsed input data from Redis stream.
        :rtype: dict
        :raises Exception: If an error occurs while reading from Redis.
        """
        try:
            stream_name = "Plugins"
            message_data = await self.redis.read_from_stream(stream_name, 1, 0)
            input_data = {}
            for key in message_data:
                plugin_data = message_data[key]
                input_data.update(ast.literal_eval(plugin_data))
            # logger.info('Data from Redis: %s', json.dumps(input_data, sort_keys=True, indent=4))
            return input_data
        except Exception as e:
            logger.error('Error reading from Redis Storage: %s', e)
            return {}

    async def post(self, pair: str, action: str, order_type: str, volume: float, price: float) -> None:
        """Places an order based on given parameters.

        :param pair: Trading pair (e.g., 'BTC/USD').
        :type pair: str
        :param action: Action to be performed ('BID' or 'ASK').
        :type action: str
        :param order_type: Type of order ('Market' or 'Limit').
        :type order_type: str
        :param volume: Volume of the trade.
        :type volume: float
        :param price: Price for limit orders.
        :type price: float
        :raises Exception: If an error occurs while placing the order.
        """
        order_side = 'buy' if action == 'BID' else 'sell'
        client_order_id = str(uuid.uuid4())
        command = PlaceOrderCommand(
            client_order_id=client_order_id,
            symbol=pair,
            type=order_type.lower(),
            side=order_side,
            quantity=float(volume),
            price=float(price) if order_type.lower() == 'limit' else None
        )

        target_stream = None
        exchange_name_for_metric = None

        if pair.upper() in self.luno_pairs or any(lp_part in pair.upper() for lp_part in ["XBT", "ZAR", "ETH"] if len(pair.upper()) > 3): # Basic Luno pair check
            target_stream = LUNO_PLACE_ORDER_COMMAND_STREAM
            exchange_name_for_metric = "luno"
            logger.info(f"Routing order for {pair} to Luno connector.")
        elif "USDT" in pair.upper(): # Default to Binance for USDT pairs
            target_stream = BINANCE_PLACE_ORDER_COMMAND_STREAM
            exchange_name_for_metric = "binance"
            logger.info(f"Routing order for {pair} to Binance connector.")
        else:
            logger.warning(f"No connector configured to handle order for pair: {pair}.")
            return

        if target_stream and exchange_name_for_metric:
            try:
                self.pending_orders[client_order_id] = {
                    "exchange": exchange_name_for_metric,
                    "command": command.model_dump(),
                    "status": "pending_submission",
                    "exchange_order_id": None,
                    "submission_time": time.time()
                }
                await self.redis.storage.xadd(target_stream, {"data": command.model_dump_json()})
                DM_ORDER_COMMANDS_SENT_TOTAL.labels(exchange=exchange_name_for_metric, pair=pair).inc()
                DM_PENDING_ORDERS_GAUGE.labels(exchange=exchange_name_for_metric).inc()
                logger.info(f"Published PlaceOrderCommand to {target_stream} for {exchange_name_for_metric}: client_order_id {client_order_id}, {pair}, {order_side}, vol {volume}")
            except Exception as e:
                logger.error(f"Error publishing PlaceOrderCommand to {target_stream} (client_order_id {client_order_id}): {e}")
                if client_order_id in self.pending_orders:
                    del self.pending_orders[client_order_id]
        # Removed direct Luno CCXT client logic


    async def decide(self, input_data: dict) -> None:
        """Makes trading decisions based on the input data.

        :param input_data: Data used to make trading decisions.
        :type input_data: dict
        :raises Exception: If an error occurs during decision making.
        """
        try:
            pair = input_data.get('Pair')
            if not pair:
                logger.info('No trading pair specified.')
                return

            firstpart, secondpart = pair[:len(pair)//2], pair[len(pair)//2:]
            action = input_data.get('Action')
            if action == 'None':
                logger.info('No Action')
                return

            balance_key = f'{secondpart} Balance' if action == 'BID' else f'{firstpart} Balance'
            required_balance = self.offset.get(firstpart, 0.0) - (float(input_data.get('Trade Volume', 0)) * float(input_data.get('Last Trade Price', 0))) if action == 'BID' else self.offset.get(firstpart, 0.0) + float(input_data.get('Trade Volume', 0))
            if float(input_data.get(balance_key, 0)) >= required_balance:
                await self.post(pair, action, input_data.get('Order Type', 'Market'), input_data.get('Trade Volume', 0), input_data.get('Last Trade Price', 0))
            else:
                logger.info('Insufficient balance for action %s', action)
        except Exception as e:
            logger.error('Error in decision making: %s', e)

    async def consume_order_events(self): # Renamed from consume_binance_order_events
        logger.info("Starting generic order event consumer loop for Binance and Luno...")
        # Uses self.order_event_stream_ids which includes streams for both exchanges
        
        while True:
            try:
                messages = await self.redis.storage.xread(
                    streams=self.order_event_stream_ids, # Now includes Luno streams
                    count=10,
                    block=1000 
                )
                if not messages:
                    continue

                for stream_name_bytes, message_list in messages:
                    stream_name_str = stream_name_bytes.decode('utf-8')
                    last_id_processed_for_stream = self.order_event_stream_ids[stream_name_str]
                    
                    exchange_name = "unknown"
                    if BINANCE_CONNECTOR_NAME in stream_name_str:
                        exchange_name = "binance"
                    elif LUNO_CONNECTOR_NAME in stream_name_str:
                        exchange_name = "luno"

                    for message_id_bytes, message_data_dict in message_list:
                        message_id_str = message_id_bytes.decode('utf-8')
                        try:
                            data_json = message_data_dict[b'data'].decode('utf-8')
                            
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

                            elif "order_status" in stream_name_str: # Covers both ...:events:order_status
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
                            
                            last_id_processed_for_stream = message_id_str
                        except json.JSONDecodeError as e:
                            logger.error(f"JSON decode error for message ID {message_id_str} in stream {stream_name_str}: {e}")
                        except Exception as e: 
                            logger.error(f"Error processing message ID {message_id_str} from stream {stream_name_str}: {e}")
                    
                    self.order_event_stream_ids[stream_name_str] = last_id_processed_for_stream
            
            except Exception as e: 
                logger.error(f"Error in consume_order_events loop: {e}")
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

