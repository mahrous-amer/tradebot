import asyncio
import numpy as np
import logging
from plugin import Plugin

logger = logging.getLogger(__name__)

class BollingerBandsPlugin(Plugin):
    """
    Plugin for trading based on Bollinger Bands.
    """

    def __init__(self, data_sink, window=20, num_std_dev=2):
        super().__init__(data_sink)
        self.is_running = True
        self.name = "BollingerBandsPlugin"
        self.window = window
        self.num_std_dev = num_std_dev
        self.pair_prices = {}  # Dictionary to store prices for each trading pair

    def calculate_bollinger_bands(self, prices):
        """
        Helper function to calculate Bollinger Bands.
        """
        sma = np.mean(prices[-self.window:])
        std_dev = np.std(prices[-self.window:])
        upper_band = sma + (self.num_std_dev * std_dev)
        lower_band = sma - (self.num_std_dev * std_dev)
        return upper_band, lower_band

    async def main(self):
        """
        Main logic to generate buy/sell signals based on Bollinger Bands.
        """
        await self.update()
        marketdata = self.input_data

        # Process each trading pair
        for pair, data in marketdata.items():
            tick_data = data.get('tick')  # Extract tick data for the pair

            if tick_data:
                # Use 'last' or 'close' price from tick data
                price = tick_data.get('last') or tick_data.get('close')

                if price is not None:
                    # Initialize prices list for the pair if it doesn't exist
                    if pair not in self.pair_prices:
                        self.pair_prices[pair] = []

                    # Append the new price for this pair
                    self.pair_prices[pair].append(price)

                    # Ensure we have enough prices to calculate Bollinger Bands
                    if len(self.pair_prices[pair]) >= self.window:
                        upper_band, lower_band = self.calculate_bollinger_bands(
                            np.array(self.pair_prices[pair])
                        )

                        # Generate trading signal
                        if price > upper_band:
                            signal = "sell"
                        elif price < lower_band:
                            signal = "buy"
                        else:
                            signal = "hold"

                        # Publish the Bollinger Bands and signal
                        bands_signal = {
                            "pair": pair,
                            "upper_band": upper_band,
                            "lower_band": lower_band,
                            "price": price,
                            "signal": signal
                        }
                        logger.info(bands_signal)
                        await self.publish(bands_signal)
