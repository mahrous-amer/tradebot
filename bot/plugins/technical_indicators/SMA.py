import asyncio
import numpy as np
from plugin import Plugin 
# If Plugin is in bot.plugin, then from bot.plugin import Plugin

class SMAPlugin(Plugin):
    """
    Plugin for calculating a Simple Moving Average (SMA) and publishing its value.
    """

    def __init__(self, data_sink, exchange_id: str, symbol: str, period=20): # Modified
        super().__init__(data_sink, exchange_id, symbol) # Modified
        self.is_running = True # Should be managed by the plugin lifecycle if needed
        self.period = int(period)
        # Dynamically set plugin name based on its configuration
        self.name = f"SMA_{self.period}_{self.exchange_id}_{self.symbol.replace('/', '')}"
        self.description = f"Calculates SMA({self.period}) for {self.symbol} on {self.exchange_id} and publishes it."
        self.prices = [] # Stores close prices for the targeted symbol and exchange
        self.logger.info(f"Initialized {self.name} for {self.exchange_id}:{self.symbol} with period {self.period}")

    def calculate_sma(self): # Modified to use instance attributes
        """
        Calculate the SMA over self.period using self.prices.
        """
        if len(self.prices) < self.period:
            self.logger.debug(f"{self.name}: Not enough price data to calculate SMA (need {self.period}, have {len(self.prices)})")
            return None # Not enough data
        
        # Use the last 'period' prices for calculation
        prices_to_calculate = np.array(self.prices[-self.period:], dtype=float)
        if len(prices_to_calculate) < self.period: # Should ideally not happen if previous check is correct
             self.logger.debug(f"{self.name}: Not enough selected prices for SMA (need {self.period}, have {len(prices_to_calculate)})")
             return None
        
        sma = np.mean(prices_to_calculate)
        return sma

    async def main(self): # Modified
        """
        Main logic to fetch price, calculate SMA, and publish the SMA value.
        """
        await self.update()  # Update self.input_data from DataProvider

        # Extract data for the specific exchange and symbol this plugin instance targets
        # self.input_data is structured as: {"exchange_id": {"symbol": {"tick": {...}, ...}}}
        exchange_specific_data = self.input_data.get(self.exchange_id, {})
        symbol_specific_data = exchange_specific_data.get(self.symbol, {})
        tick_data = symbol_specific_data.get('tick') # This is an instance of Ticker model

        if tick_data and hasattr(tick_data, 'last_price'):
            current_close_price = tick_data.last_price
            if current_close_price is not None:
                self.prices.append(float(current_close_price))
                # Keep the prices list to the required length for SMA calculation
                # Only need 'period' number of prices. Keep a bit more for stability or if needed, but for basic SMA, 'period' is enough.
                max_prices_to_keep = self.period 
                if len(self.prices) > max_prices_to_keep:
                    self.prices = self.prices[-max_prices_to_keep:]
                
                sma_value = self.calculate_sma()

                if sma_value is not None:
                    indicator_name = f"sma_{self.period}"
                    await self.publish(indicator_name=indicator_name, value=sma_value)
                    # self.logger.info(f"{self.name} calculated SMA: {sma_value:.2f} for {self.exchange_id}:{self.symbol}") # Can be noisy
                else:
                    self.logger.debug(f"{self.name}: SMA could not be calculated for {self.exchange_id}:{self.symbol} (not enough data or invalid price).")
            else:
                self.logger.debug(f"{self.name}: No 'last_price' in tick data for {self.exchange_id}:{self.symbol}.")
        else:
            self.logger.debug(f"{self.name}: No tick data found for {self.exchange_id}:{self.symbol} in DataProvider output.")
        
        # Old SMA crossover and signal logic removed.
