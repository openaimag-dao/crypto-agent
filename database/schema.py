"""
МОДУЛЬ 2a. Схема базы данных SQLite.

Три таблицы:
  account      — виртуальный баланс (одна строка, id=1)
  trades       — журнал сделок песочницы (открытие/закрытие, PnL)
  signals_log  — ВСЕ ответы LLM: и принятые, и отклонённые цензором,
                 с причиной отказа. Это "чёрный ящик" системы.

Принцип: в trades пишет ТОЛЬКО repository.open_trade(), который вызывается
ТОЛЬКО после validator'а (Модуль 3). Прямых INSERT в trades из других мест нет.
"""

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS account (
    id              INTEGER PRIMARY KEY CHECK (id = 1),   -- ровно одна строка
    balance_usdt    REAL    NOT NULL CHECK (balance_usdt >= 0),
    initial_deposit REAL    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT    NOT NULL,                      -- BTCUSDT ...
    direction       TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    leverage        INTEGER NOT NULL CHECK (leverage IN (3, 5)),   -- жесткий дубль лимита из config
    entry_price     REAL    NOT NULL CHECK (entry_price > 0),
    stop_loss       REAL    NOT NULL CHECK (stop_loss > 0),
    take_profit     REAL    NOT NULL CHECK (take_profit > 0),
    margin_usdt     REAL    NOT NULL CHECK (margin_usdt > 0),      -- собственные средства в сделке
    qty             REAL    NOT NULL CHECK (qty > 0),              -- размер позиции в монетах
    risk_pct        REAL    NOT NULL CHECK (risk_pct BETWEEN 0 AND 5),
    status          TEXT    NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN', 'CLOSED')),
    opened_at       TEXT    NOT NULL,
    closed_at       TEXT,
    close_price     REAL,
    close_reason    TEXT    CHECK (close_reason IN ('TP', 'SL', 'MANUAL', 'LIQUIDATION') OR close_reason IS NULL),
    pnl_usdt        REAL,
    llm_rationale   TEXT                                            -- краткое обоснование от ИИ
);

CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);

CREATE TABLE IF NOT EXISTS signals_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL,
    symbol          TEXT,
    raw_response    TEXT    NOT NULL,                      -- сырой JSON от LLM как есть
    verdict         TEXT    NOT NULL CHECK (verdict IN ('ACCEPTED', 'REJECTED', 'NO_TRADE')),
    reject_reason   TEXT,                                  -- почему цензор отклонил
    trade_id        INTEGER REFERENCES trades (id)         -- заполняется, если сделка открыта
);

CREATE INDEX IF NOT EXISTS idx_signals_verdict ON signals_log (verdict);
"""
