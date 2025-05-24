# Trading Strategies

This document outlines the example trading strategies implemented within the `bot/DecisionMaker` component.

## 1. Example Strategy 1: RSI Threshold (Binance BTC/USDT)

-   **Name:** RSI Threshold Strategy (Binance BTC/USDT)
-   **Description:** This strategy operates on the Binance exchange for the BTC/USDT trading pair. It aims to:
    -   Generate a **BUY** signal when the 14-period Relative Strength Index (RSI) drops below a predefined oversold threshold (e.g., 30).
    -   Generate a **SELL** signal when the 14-period RSI rises above a predefined overbought threshold (e.g., 70).
-   **Data Used:**
    -   **Indicator Stream:** `indicators:rsi_14:binance:BTCUSDT`
        -   This stream provides `IndicatorValue` messages published by an `RSIPlugin` instance configured for Binance, BTC/USDT, and a 14-period RSI.
    -   **Market Data:** Current last price for BTC/USDT on Binance. This is accessed from the `DataProvider`'s cached market data, which is populated by the `binance-connector` via the `binance:BTCUSDT:ticker` stream.
    -   **Account Balance:**
        -   For BUY orders: Free USDT balance on Binance (from `DataProvider`'s balance cache, populated by `binance_connector:events:account_balance`).
        -   For SELL orders: Free BTC balance on Binance (from `DataProvider`'s balance cache).
-   **Order Type:** Market Orders are used for simplicity.
-   **Trade Size:** A fixed quantity of the base asset (BTC) is traded, e.g., 0.0001 BTC, as defined by `self.trade_quantity_btc` in `DecisionMaker`.
-   **Anti-Duplicate Logic:** Before placing a new order, the strategy checks the `self.pending_orders` cache for any existing open or pending orders for the "binance" exchange and "BTC/USDT" symbol. If an active order exists, a new order is not placed.
-   **Staleness Check:** The strategy checks if the received RSI indicator data is recent (e.g., within the last 5 minutes, defined by `self.indicator_staleness_threshold_seconds`) before acting on it.

## 2. Example Strategy 2: Moving Average (MA) Crossover (Luno XBT/ZAR)

-   **Name:** Moving Average Crossover Strategy (Luno XBT/ZAR)
-   **Description:** This strategy operates on the Luno exchange for the XBT/ZAR trading pair (Luno's designation for BTC/ZAR). It aims to:
    -   Generate a **BUY** signal (Golden Cross) when the shorter-period Simple Moving Average (SMA), e.g., SMA(20), crosses above the longer-period SMA, e.g., SMA(50).
    -   Generate a **SELL** signal (Death Cross) when the shorter-period SMA crosses below the longer-period SMA.
-   **Data Used:**
    -   **Indicator Streams:**
        -   `indicators:sma_20:luno:XBTZAR` (for SMA 20-period, assuming `plugin_configs.json` sets this up).
        -   `indicators:sma_50:luno:XBTZAR` (for SMA 50-period, assuming `plugin_configs.json` sets this up).
        -   These streams provide `IndicatorValue` messages published by `SMAPlugin` instances configured for Luno, XBT/ZAR, and the respective periods.
    -   **Market Data:** Current last price for XBT/ZAR on Luno. This is accessed from the `DataProvider`'s cached market data, populated by the `luno-connector` via the `luno:XBTZAR:ticker` stream.
    -   **Account Balance:**
        -   For BUY orders: Free ZAR balance on Luno (from `DataProvider`'s balance cache, populated by `luno_connector:events:account_balance`).
        -   For SELL orders: Free XBT balance on Luno.
-   **Order Type:** Market Orders.
-   **Trade Size:** A fixed quantity of the base asset (XBT) is traded, e.g., 0.0005 XBT, as defined by `self.luno_xbt_trade_quantity` in `DecisionMaker`.
-   **Crossover Logic:** The strategy maintains an internal state variable (`self.luno_xbtzar_sma_crossed_above`) to track the current relationship between the short and long SMAs. A trade signal is generated only when an actual crossover occurs (e.g., state changes from short-below-long to short-above-long for a buy). This prevents multiple orders if the SMAs remain in a crossed state.
-   **Anti-Duplicate Logic:** Similar to the RSI strategy, it checks `self.pending_orders` for existing active Luno orders on XBT/ZAR before placing a new one.
-   **Staleness Check:** Indicator data is checked for freshness against `self.indicator_staleness_threshold_seconds`.The `docs/trading_strategies.md` file was already created in the previous turn with the specified content.

Now, I'll proceed with Phase 2: Reading `docs/testing_strategy.md` to prepare for updates.
