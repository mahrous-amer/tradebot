import asyncio
import logging
import json
import ast
import configparser
import ccxt.async_support as ccxt
from storage import Storage

logger = logging.getLogger(__name__)

CHECK_TIMEOUT = 15  # Timeout in seconds for checking order status
SLEEP_DURATION = 60  # Duration to sleep in the main method

class DecisionMaker:
    """Handles trading decisions and order placements based on input data."""

    def __init__(self, check_timeout: int = CHECK_TIMEOUT):
        """
        Initializes the DecisionMaker with the given timeout and config settings.

        :param check_timeout: Timeout in seconds for checking order status.
        :type check_timeout: int
        """
        self.check_timeout = check_timeout
        self.redis = Storage()
        self.exchange = self._initialize_exchange()
        self.offset = {'XBT': 0.005, 'XRP': 6.0, 'MYR': 0.0}

    def _initialize_exchange(self) -> ccxt.luno:
        """Initializes the Luno exchange client with API keys from the config file.

        :return: Initialized Luno exchange client.
        :rtype: ccxt.luno
        """
        config = configparser.ConfigParser()
        config.read("config.cfg")
        luno_key = config.get('LUNO', 'LUNO_KEY')
        luno_secret = config.get('LUNO', 'LUNO_SECRET')
        return ccxt.luno({'apiKey': luno_key, 'secret': luno_secret})

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
        orderside = 'buy' if action == 'BID' else 'sell'
        order_params = {
            'market': {'type': 'market', 'amount': float(volume * price) * 1.01 if action == 'BID' else volume},
            'limit': {'type': 'limit', 'amount': float(volume), 'price': price, 'params': {'stop_price': price, 'stop_direction': 'ABOVE' if action == 'BID' else 'BELOW'}}
        }

        try:
            order_params = order_params.get(order_type.lower())
            # order = await self.exchange.create_order(symbol=pair, **order_params)
            # await self._wait_order_complete(order['id'])
            logger.info('Successfully placed a %s %s %s order %s for %.2f', order_type, pair, action, order['id'], volume)
        except Exception as e:
            logger.error('Error placing %s order: %s', order_type, e)

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

    async def _wait_order_complete(self, order_id: str) -> None:
        """Waits for the order to be completed and logs the status.

        :param order_id: ID of the order to wait for.
        :type order_id: str
        :raises Exception: If an error occurs while fetching the order status.
        """
        status = 'open'
        while status == 'open':
            await asyncio.sleep(self.check_timeout)
            order = await self.exchange.fetch_order(order_id)
            status = order['status']

        logger.info('Finished order %s with %s status', order_id, status)
        if status == 'canceled':
            logger.info('Trade has been canceled')

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

