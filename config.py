"""
Центральная конфигурация проекта.
Все "жесткие" лимиты риска живут здесь — валидатор (Модуль 3) читает их отсюда.
"""

# ============ МАКРО-ИНДЕКСЫ (yfinance) ============
MACRO_TICKERS: dict[str, str] = {
    "NASDAQ": "^IXIC",
    "SP500": "^GSPC",
    "DXY": "DX-Y.NYB",
    "VIX": "^VIX",       # индекс страха — полезен для контекста LLM
    "GOLD": "GC=F",      # фьючерс на золото — risk-off индикатор
}

# ============ КРИПТА (Binance Futures, публичный API) ============
BINANCE_FAPI_BASE = "https://fapi.binance.com"
CRYPTO_SYMBOLS: list[str] = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Таймфрейм и глубина свечей для теханализа
KLINES_INTERVAL = "4h"
KLINES_LIMIT = 100

# ============ РИСК-МЕНЕДЖМЕНТ (ЖЕСТКИЕ ЛИМИТЫ) ============
ALLOWED_LEVERAGE: tuple[int, ...] = (3, 5)   # только 3x или 5x, иное — отказ
MAX_RISK_PER_TRADE_PCT = 5.0                  # максимум 5% депозита на сделку
MIN_RISK_PER_TRADE_PCT = 2.0

# ============ СЕТЬ ============
HTTP_TIMEOUT = 15          # секунд
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 2       # секунд между попытками
