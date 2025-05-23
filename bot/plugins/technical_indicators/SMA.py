import asyncio
import numpy as np
from plugin import Plugin

class SMAPlugin(Plugin):
    """
    Plugin for trading based on Simple Moving Average (SMA) crossover.
    """

    def __init__(self, data_sink, short_window=50, long_window=200):
        super().__init__(data_sink)
        self.is_running = True
        self.name = "SMAPlugin"
        self.short_window = short_window
        self.long_window = long_window
        self.prices = []

    def calculate_sma(self, prices, window):
        """
        Helper function to calculate SMA.
        """
        return np.convolve(prices, np.ones(window), 'valid') / window

    async def main(self):
        """
        Main logic to generate buy/sell signals based on SMA crossover.
        """

        await self.update()  # Update the input data from data provider
        price = self.input_data.get('price')

        if price is not None:
            self.prices.append(price)

            if len(self.prices) >= self.long_window:
                short_sma = self.calculate_sma(np.array(self.prices), self.short_window)[-1]
                long_sma = self.calculate_sma(np.array(self.prices), self.long_window)[-1]

                if short_sma > long_sma:
                    signal = "buy"
                else:
                    signal = "sell"

                await self.publish({"short_sma": short_sma, "long_sma": long_sma, "signal": signal})
