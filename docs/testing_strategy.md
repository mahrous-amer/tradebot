# Testing Strategy

This document outlines the testing strategy for the trading bot, with a focus on integration tests for the microservice architecture.

## 1. Overview

The primary goal of integration testing is to ensure that independently developed services (e.g., `binance-connector`, `bot` service) work correctly together. Key interaction points revolve around Redis Streams for commands and events.

## 2. Test Environment

-   Integration tests should run in an environment closely resembling the `docker-compose` setup.
-   This requires instances of:
    -   `redis`
    -   `postgres` (potentially with pre-loaded test data or schema)
    -   `binance-connector` service
    -   `luno-connector` service (New)
    -   `bot` service
-   A mock exchange is NOT in scope for these integration tests; they will target a live (testnet, if available and configured) or simulated exchange environment if the connector is configured for it. For fully automated tests without live external dependencies, exchange interactions would need to be mocked at the `ccxt` layer within the connector, which is a more advanced setup. For now, tests might require careful configuration or run against a paper trading account.

## 3. Key Integration Scenarios to Test

### 3.1. Market Data Flow (Connector -> Bot -> Plugins)

-   **Objective:** Verify that market data published by connectors (e.g., `binance-connector`, `luno-connector`) is correctly consumed by `bot/DataProvider` and made available to observers (plugins).
-   **Test Steps (Binance Example):**
    1.  Start all services.
    2.  Ensure `binance-connector` is configured for a specific pair (e.g., BTC/USDT).
    3.  Monitor Redis Streams (`binance:BTCUSDT:ticker`, `binance:BTCUSDT:orderbook`, `binance:BTCUSDT:trades`) to see if `binance-connector` is publishing data.
    4.  In the `bot` service, attach a test observer/plugin to `DataProvider`.
    5.  Verify that the test observer receives updates (e.g., new ticker data for `binance:BTC/USDT`) corresponding to the data published by the Binance connector.
    6.  Verify that `DataProvider` saves this market data to PostgreSQL with `provider="binance_connector"`.
-   **Test Steps (Luno Example):**
    1.  Start all services.
    2.  Ensure `luno-connector` is configured for a specific pair (e.g., XBT/ZAR, Luno uses XBT for Bitcoin).
    3.  Monitor Redis Streams (`luno:XBTZAR:ticker`, `luno:XBTZAR:orderbook`, `luno:XBTZAR:trades`) to see if `luno-connector` is publishing data.
    4.  In the `bot` service, attach a test observer/plugin to `DataProvider`.
    5.  Verify that the test observer receives updates (e.g., new ticker data for `luno:XBT/ZAR`) corresponding to the data published by the Luno connector.
    6.  Verify that `DataProvider` saves this market data to PostgreSQL with `provider="luno_connector"`.
-   **Assertions (Common):**
    -   Data appears on relevant Redis streams from the respective connector.
    -   Test observer in the bot receives data, correctly attributed to the source exchange and symbol.
    -   Data format/schema in the observer matches `common_models`.
    -   Data is persisted in PostgreSQL by `DataProvider` with the correct `provider` field.

### 3.2. Order Placement and End-to-End Status Tracking

-   **Objective:** Verify that an order command sent from `bot/DecisionMaker` is processed by the correct connector, an order is placed (simulated or real), and status updates are correctly relayed back and processed.
-   **Test Steps (Binance Example):**
    1.  Start all services. Configure `binance-connector` with (test/paper) API keys.
    2.  Trigger `DecisionMaker` to place a 'buy limit' order for BTC/USDT.
    3.  Monitor `binance_connector:commands:place_order` Redis Stream.
    4.  Monitor `binance_connector:events:order_response`.
    5.  If accepted, monitor `binance_connector:events:order_status`.
    6.  Verify `DecisionMaker` updates its internal tracking for the Binance order.
-   **Test Steps (Luno Example):**
    1.  Start all services. Configure `luno-connector` with (test/paper) API keys.
    2.  Trigger `DecisionMaker` to place a 'buy limit' order for XBT/ZAR.
    3.  Monitor `luno_connector:commands:place_order` Redis Stream.
    4.  Monitor `luno_connector:events:order_response`.
    5.  If accepted, monitor `luno_connector:events:order_status`.
    6.  Verify `DecisionMaker` updates its internal tracking for the Luno order.
-   **Assertions (Common):**
    -   `PlaceOrderCommand` appears on the correct connector's command Redis Stream.
    -   `OrderResponseEvent` is published by the respective connector with a correlating `client_order_id`.
    -   `OrderStatusUpdateEvent`(s) are published for open orders from the respective connector.
    -   `DecisionMaker` correctly interprets these events and updates its internal state for the specific exchange order.
    -   (If using a live test exchange) An actual order appears on the respective exchange.

### 3.3. Account Balance Updates

-   **Objective:** Verify that account balance information from each connector is received and cached by `bot/DataProvider` and accessible to `DecisionMaker`.
-   **Test Steps (Binance Example):**
    1.  Start all services.
    2.  Allow `binance-connector` to run its periodic balance fetching.
    3.  Monitor `binance_connector:events:account_balance` Redis Stream.
    4.  In the `bot` service, call `DataProvider.get_account_balance(exchange="binance", asset="USDT")`.
-   **Test Steps (Luno Example):**
    1.  Start all services.
    2.  Allow `luno-connector` to run its periodic balance fetching.
    3.  Monitor `luno_connector:events:account_balance` Redis Stream.
    4.  In the `bot` service, call `DataProvider.get_account_balance(exchange="luno", asset="ZAR")`.
-   **Assertions (Common):**
    -   `AccountBalanceEvent` messages appear on the respective connector's Redis Stream.
    -   `DataProvider.get_account_balance(exchange=..., asset=...)` returns balance information consistent with what was published by the specified connector.

### 3.4. Metrics Exposure

-   **Objective:** Verify that all relevant services (`binance-connector`, `luno-connector`, `bot`) expose Prometheus metrics.
-   **Test Steps:**
    1.  Start all services.
    2.  Access `http://localhost:8001/metrics` (for `binance-connector`).
    3.  Access `http://localhost:8002/metrics` (for `luno-connector`).
    4.  Access `http://localhost:5000/metrics` (for `bot` service).
-   **Assertions:**
    -   Metrics endpoints return data in Prometheus format.
    -   Expected metrics (e.g., `binc_commands_received_total`, `lc_commands_received_total`, `dm_order_commands_sent_total`) are present.

### 3.5. Testing Implemented Strategies

This section outlines how to test the example trading strategies implemented in `bot/DecisionMaker`.

-   **Prerequisites:**
    -   Ensure `config/plugin_configs.json` is correctly configured to enable the necessary plugin instances:
        -   For RSI Strategy: An `RSIPlugin` instance for `binance` and `BTC/USDT` with `period: 14`.
        -   For MA Crossover Strategy: Two `SMAPlugin` instances for `luno` and `XBT/ZAR`, one with `period: 20` and another with `period: 50`.
    -   Ensure `bot/DecisionMaker`'s `__init__` method is configured to listen to the specific indicator streams published by these plugins (e.g., `indicators:rsi_14:binance:BTCUSDT`, `indicators:sma_20:luno:XBTZAR`, `indicators:sma_50:luno:XBTZAR`).
    -   Ensure the respective connectors (`binance-connector`, `luno-connector`) are running and configured (e.g., with API keys for a testnet or paper trading account if live orders are intended).
    -   Ensure the `bot` service, `redis`, and `postgres` are running.

-   **Test Steps (General Approach):**
    1.  **Start Services:** Use `docker-compose up --build -d` to start all services.
    2.  **Simulate Market Data:** Manually or via a script, publish a sequence of `Ticker` messages to the relevant market data streams (e.g., `binance:BTCUSDT:ticker` for the RSI strategy, `luno:XBTZAR:ticker` for the MA Crossover strategy). These messages should simulate price movements that would cause the configured indicators (RSI, SMAs) to cross their defined thresholds or perform crossovers.
        -   *Example for RSI:* To test RSI going below 30, publish a series of ticker messages with decreasing prices.
        -   *Example for MA Crossover:* To test a golden cross (SMA20 > SMA50), publish ticker messages that would first establish the SMAs and then cause the short SMA to rise above the long SMA.
    3.  **Monitor Indicator Streams:** Observe the relevant indicator streams (e.g., `indicators:rsi_14:binance:BTCUSDT`, `indicators:sma_20:luno:XBTZAR`, `indicators:sma_50:luno:XBTZAR`) using a Redis client (e.g., `redis-cli XREAD STREAMS ... BLOCK 0 ...`). Verify that the `RSIPlugin` and `SMAPlugin` instances are correctly calculating and publishing `IndicatorValue` messages in response to the simulated market data. Check the `value`, `indicator_name`, `exchange`, and `symbol` fields.
    4.  **Monitor `DecisionMaker` Logs:** Observe the logs from the `bot` service. Look for log entries from `DecisionMaker` indicating:
        -   Reception and caching of indicator values.
        -   Execution of the specific strategy methods (`_strategy_rsi_binance_btcusdt`, `_strategy_ma_luno_xbtzar`).
        -   Evaluation of strategy conditions (e.g., "RSI below threshold", "Golden Cross detected").
        -   Balance checks.
        -   Checks for existing open orders.
    5.  **Monitor Command Streams:** If a strategy condition is met and an order should be placed, monitor the appropriate command stream (e.g., `binance_connector:commands:place_order` or `luno_connector:commands:place_order`) for a new `PlaceOrderCommand` message. Verify its content (symbol, side, type, quantity).
    6.  **Monitor Event Streams:** Observe the corresponding connector event streams (e.g., `binance_connector:events:order_response` and `binance_connector:events:order_status`) for feedback on the order placement and subsequent status updates.
    7.  **Verify `DecisionMaker` State:** Check `DecisionMaker` logs or (if possible via debugging/tooling) its internal `pending_orders` cache to ensure it correctly tracks the state of sent orders based on received events.

-   **Specific Checks for RSI Strategy (Binance BTC/USDT):**
    -   Simulate price action causing RSI(14) for BTC/USDT to drop below 30. Verify a market BUY `PlaceOrderCommand` is sent to `binance_connector:commands:place_order` for BTC/USDT (fixed quantity).
    -   Simulate price action causing RSI(14) to rise above 70. Verify a market SELL `PlaceOrderCommand` is sent.
    -   Verify the anti-duplicate logic prevents new orders if a relevant order is already pending.
    -   Verify staleness check for RSI data.

-   **Specific Checks for MA Crossover Strategy (Luno XBT/ZAR):**
    -   Simulate prices leading to SMA(20) crossing above SMA(50) for XBT/ZAR. Verify a market BUY `PlaceOrderCommand` is sent to `luno_connector:commands:place_order` for XBT/ZAR (fixed quantity). Ensure this only happens once per cross due to the `self.luno_xbtzar_sma_crossed_above` state variable.
    -   Simulate prices leading to SMA(20) crossing below SMA(50). Verify a market SELL `PlaceOrderCommand` is sent. Ensure this only happens once per cross.
    -   Verify the anti-duplicate logic.
    -   Verify staleness check for SMA data.

## 4. Future Testing Considerations

-   **Automated Integration Tests:** Using a Python testing framework (e.g., `pytest`) to automate the scenarios above. This would involve:
    -   Programmatically publishing messages to Redis Streams.
    -   Programmatically reading from Redis Streams.
    -   Making HTTP calls to `/metrics` endpoints.
    -   Querying PostgreSQL.
    -   Potentially mocking `ccxt` calls within the connector for more controlled testing without live exchange dependency.
-   **Fault Tolerance Tests:** Simulating Redis unavailability or connector crashes.
-   **Performance/Load Tests.**

```
