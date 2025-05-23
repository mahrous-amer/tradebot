import asyncio
import numpy as np
from plugin import Plugin

class RSIPlugin(Plugin):
    """
    Plugin for trading based on the Relative Strength Index (RSI).
    """

    def __init__(self, data_sink, period=14, overbought=70, oversold=30):
        super().__init__(data_sink)
        self.is_running = True
        self.name = "RSIPlugin"
        self.period = period
        self.overbought = overbought
        self.oversold = oversold
        self.prices = []

    def calculate_rsi(self):
        """
        Calculate the RSI over the specified period.
        """

        if len(self.prices) < self.period + 1:
            return None  # Not enough data
        deltas = np.diff(self.prices)
        seed = deltas[:self.period]
        up = seed[seed >= 0].sum() / self.period
        down = -seed[seed < 0].sum() / self.period
        rs = up / down
        rsi = 100 - (100 / (1 + rs))
        return rsi

    async def main(self):
        """
        Main logic to generate buy/sell signals based on RSI.
        """

        await self.update()  # Update the input data from data provider
        price = self.input_data.get('price')

        if price is not None:
            self.prices.append(price)
            rsi = self.calculate_rsi()

            if rsi:
                if rsi > self.overbought:
                    signal = "sell"
                elif rsi < self.oversold:
                    signal = "buy"
                else:
                    signal = "hold"
                await self.publish({"rsi": rsi, "signal": signal})
