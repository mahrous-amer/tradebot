import asyncio
import numpy as np
from plugin import Plugin

class EMAPlugin(Plugin):
    """
    Plugin for trading based on Exponential Moving Average (EMA) crossover.
    """

    def __init__(self, data_sink, short_window=12, long_window=26):
        super().__init__(data_sink)
        self.is_running = True
        self.name = "EMAPlugin"
        self.short_window = short_window
        self.long_window = long_window
        self.prices = []
        self.is_running = True

    def calculate_ema(self, prices, window):
        """
        Helper function to calculate EMA.
        """
        return prices.ewm(span=window, adjust=False).mean()

    async def main(self):
        """
        Main logic to generate buy/sell signals based on EMA crossover.
        """
        await self.update()
        price = self.input_data.get('price')

        if price is not None:
            self.prices.append(price)

            if len(self.prices) >= self.long_window:
                short_ema = self.calculate_ema(np.array(self.prices), self.short_window)[-1]
                long_ema = self.calculate_ema(np.array(self.prices), self.long_window)[-1]

                if short_ema > long_ema:
                    signal = "buy"
                else:
                    signal = "sell"

                await self.publish({"short_ema": short_ema, "long_ema": long_ema, "signal": signal})
