CREATE USER writer WITH PASSWORD 'password';
CREATE USER reader WITH PASSWORD 'password';

CREATE DATABASE tradedb;

\connect tradedb;

CREATE SCHEMA trade AUTHORIZATION writer;

CREATE TABLE IF NOT EXISTS trade.order_book (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    side VARCHAR(4) NOT NULL CHECK (side IN ('buy', 'sell')),
    price NUMERIC(10, 2) NOT NULL,
    quantity NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade.active_trades (
    trade_id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    trade_price NUMERIC(10, 2) NOT NULL,
    trade_quantity NUMERIC(10, 2) NOT NULL,
    trade_time TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade.ticks (
    tick_id SERIAL PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    tick_price NUMERIC(10, 2) NOT NULL,
    tick_time TIMESTAMPTZ DEFAULT NOW()
);

GRANT USAGE ON SCHEMA trade TO writer;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA trade TO writer;

GRANT USAGE ON SCHEMA trade TO reader;
GRANT SELECT ON ALL TABLES IN SCHEMA trade TO reader;

ALTER DEFAULT PRIVILEGES IN SCHEMA trade GRANT SELECT ON TABLES TO reader;
