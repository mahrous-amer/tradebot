# tradebot

## Project structure

```
/project-root
    ├── /api
    │    ├── main.py                   # FastAPI entry point
    │    ├── /routers
    │    │     ├── trading.py          # API router for trading commands (async)
    │    │     ├── backtest.py         # API router for backtesting (async)
    │    ├── /auth
    │    │     └── jwt.py              # JWT authentication logic
    │    ├── /schemas
    │    │     ├── trading.py          # Pydantic schemas for trading data
    │    │     └── backtest.py         # Pydantic schemas for backtest results
    │    ├── /utils
    │    │     ├── redis.py            # Async Redis client for pub/sub communication
    │    │     └── postgres.py         # Async PostgreSQL database setup
    │    ├── /models
    │    │     ├── __init__.py         # SQLAlchemy models for database tables
    │    ├── /backtester
    │    │     └── backtester.py       # Asynchronous backtesting engine
    │    ├── /test
    │    │     ├── 
    │    │     └── 
    │    ├── requirements.txt          # Dependencies for FastAPI and asyncpg
    │    └── Dockerfile                # FastAPI Dockerfile
    ├── /bot
    │    ├── main.py                   # Entry point for the trading bot (async)
    │    ├── /strategies               # Plugin strategies for the bot
    │    │     ├── SMAPlugin.py        # Simple Moving Average strategy (async)
    │    │     ├── EMAPlugin.py        # Exponential Moving Average strategy (async)
    │    │     └── RSIPlugin.py        # Relative Strength Index strategy (async)
    │    ├── /utils
    │    │     └── async_database.py   # Async database setup for the bot
    │    ├── /test
    │    │     ├── 
    │    │     └── 
    ├── docker-compose.yml             # Docker Compose configuration
    └── /tests
         ├── test_decision_maker.py    # Unit tests for the decision maker (async)
         ├── test_data_provider.py     # Unit tests for the data provider (async)
         └── test_plugin_handler.py    # Unit tests for plugin management (async)
```

```mermaid
classDiagram

    class EventLoop {
        - plugins_runner_loop()
        - plugins_refresh_loop()
        - plugins_health_check_loop()
        - data_provider_loop()
        - decision_maker_loop()
    }

    class PluginHandler {
        - plugin_folder: str
        - data_sink: DataProvider
        - plugins: list
        - enable(plugin: Plugin)
        - disable(plugin: Plugin)
        - invoke_callback()
    }

    class Plugin {
        <<abstract>>
        - name: str
        - data_sink: DataProvider
        - heartbeat()
        - main()
    }

    class RSIPlugin {
        - period: int
        - overbought: int
        - oversold: int
        - prices: list
        + calculate_rsi()
        + main()
    }

    class DataProvider {
        <<abstract>>
        - fetch_data()
        - broadcast()
    }

    class Luno {
        - luno_key: str
        - luno_secret: str
        - connect()
        - disconnect()
        - fetch_balance()
        - fetch_tick()
    }

    class Observer {
        <<interface>>
        - update()
    }

    class Observable {
        - observers: list
        - add_observer(observer: Observer)
        - delete_observer(observer: Observer)
        - notify_observers()
    }

    class DecisionMaker {
        - make_decision(data)
        - handle_order(order)
    }

    EventLoop --> PluginHandler : uses
    EventLoop --> DataProvider : fetches
    EventLoop --> DecisionMaker : calls
    
    PluginHandler ..|> Observable
    PluginHandler o-- Plugin : manages
    PluginHandler --> DataProvider : fetches data from
    PluginHandler ..|> Observer : registers as observer

    Plugin <|-- RSIPlugin
    DataProvider <|-- Luno
    DataProvider ..> Observer : notifies
```
