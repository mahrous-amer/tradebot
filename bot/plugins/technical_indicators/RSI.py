import asyncio
import numpy as np
from plugin import Plugin # Assuming Plugin class is in plugin.py at the same level or accessible
# If Plugin is in bot.plugin, then from bot.plugin import Plugin

class RSIPlugin(Plugin):
    """
    Plugin for calculating the Relative Strength Index (RSI) and publishing its value.
    """

    def __init__(self, data_sink, exchange_id: str, symbol: str, period=14, overbought=70, oversold=30): # Modified
        super().__init__(data_sink, exchange_id, symbol) # Modified
        self.is_running = True # Should be managed by the plugin lifecycle if needed
        self.period = int(period)
        self.overbought = int(overbought) # Though not used for publishing raw RSI
        self.oversold = int(oversold)   # Though not used for publishing raw RSI
        self.prices = [] # Stores close prices for the targeted symbol and exchange
        # Dynamically set plugin name based on its configuration
        self.name = f"RSI_{self.period}_{self.exchange_id}_{self.symbol.replace('/', '')}"
        self.description = f"Calculates RSI({self.period}) for {self.symbol} on {self.exchange_id} and publishes it."
        self.logger.info(f"Initialized {self.name} for {self.exchange_id}:{self.symbol}")

    def calculate_rsi(self):
        """
        Calculate the RSI over the specified period.
        Requires at least `period + 1` prices to calculate `period` deltas.
        """
        if len(self.prices) < self.period + 1:
            self.logger.debug(f"{self.name}: Not enough price data to calculate RSI (need {self.period + 1}, have {len(self.prices)})")
            return None  # Not enough data
        
        prices_array = np.array(self.prices, dtype=float)
        deltas = np.diff(prices_array)
        
        if len(deltas) < self.period: # Should not happen if len(self.prices) >= self.period + 1
             self.logger.debug(f"{self.name}: Not enough deltas to calculate RSI (need {self.period}, have {len(deltas)})")
             return None

        seed = deltas[:self.period] # Use the first `period` deltas for initial seed
        
        # Calculate initial average gain and loss
        gains = seed[seed >= 0].sum()
        losses = -seed[seed < 0].sum() # Losses are positive values

        if losses == 0: # Avoid division by zero if all losses are zero
            return 100.0 # RSI is 100 if all gains and no losses
        
        avg_gain = gains / self.period
        avg_loss = losses / self.period

        # Apply smoothing for subsequent values if more data is available
        # For this simplified plugin publishing only the current RSI, we use the initial calculation.
        # A more complex RSI would maintain state of avg_gain/avg_loss over time.

        if avg_loss == 0: # Avoid division by zero if avg_loss is zero
            return 100.0 # RSI is 100 if all gains and no losses
            
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    async def main(self):
        """
        Main logic to fetch price, calculate RSI, and publish the RSI value.
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
                # Keep the prices list from growing indefinitely, ensure enough for RSI calculation + buffer
                # Need at least period + 1 prices for 'period' deltas.
                # More data (e.g., 2*period) can be useful for a more stable initial RSI.
                # For simplicity, we'll keep roughly period + a small buffer.
                max_prices_to_keep = self.period + 10 
                if len(self.prices) > max_prices_to_keep:
                    self.prices = self.prices[-max_prices_to_keep:]
                
                rsi_value = self.calculate_rsi()

                if rsi_value is not None:
                    indicator_name = f"rsi_{self.period}"
                    await self.publish(indicator_name=indicator_name, value=rsi_value)
                    # self.logger.info(f"{self.name} calculated RSI: {rsi_value:.2f} for {self.exchange_id}:{self.symbol}") # Can be noisy
                else:
                    self.logger.debug(f"{self.name}: RSI could not be calculated for {self.exchange_id}:{self.symbol} (not enough data or invalid price).")
            else:
                self.logger.debug(f"{self.name}: No 'last_price' in tick data for {self.exchange_id}:{self.symbol}.")
        else:
            self.logger.debug(f"{self.name}: No tick data found for {self.exchange_id}:{self.symbol} in DataProvider output.")
        
        # Original signal logic removed as plugin now only publishes raw RSI value.
        # DecisionMaker will consume these IndicatorValue events.
