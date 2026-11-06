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

# ============ РАСХОЖДЕНИЕ HOOD / BTC (brain/divergence.py) ============
DIVERGENCE_WINDOW_DAYS = 28      # окно сравнения доходностей (≈4 недели)
DIVERGENCE_ENTER_PCT = 15.0      # разрыв (п.п.), при котором включается сигнал
DIVERGENCE_EXIT_PCT = 10.0       # разрыв, ниже которого сигнал гаснет (гистерезис)
HOOD_SUPPORT = 87.0              # зона отскока августа 2026
HOOD_RESISTANCE = 110.0          # возврат выше — риск-аппетит вернулся

# ============ КИТЫ HYPERLIQUID (collectors/whales.py) ============
# Отобраны 06.11.2026 из 48 257 кошельков лидерборда: PnL > $1M, торгуют > 1 года,
# макс. просадка ≤ 40%, ≥ 60% прибыльных месяцев, реальный объём (не маркет-мейкеры).
HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
WHALE_WALLETS: dict[str, str] = {
    "0x8bae3527e5a33fa0cf184f37bc112d071463ab6d": "Кит-1 (ROI 2560%, DD 25%)",
    "0x015354106478dda69c4aae3c0cf801290b738052": "Кит-2 (ROI 315%, DD 33%)",
    "0x3fc56e944aa7b1594c85861b2d46a07f82a2c0c1": "Кит-3 (ROI 167%, DD 38%)",
    "0x67fcc46ed2a63971697dad2fc7bb0091fe89e51d": "Кит-4 (ROI 90%, DD 18%)",
    "0x856c35038594767646266bc7fd68dc26480e910d": "Кит-5 (ROI 57%, DD 37%)",
    "0x6df99d59cdada18ade521e05edd5e6a2ec91b734": "Кит-6 (ROI 28%, DD 39%)",
}
WHALE_LOOKBACK_HOURS = 24        # окно сделок для сводки
WHALE_MIN_NOTIONAL_USD = 10_000  # мельче — шум, в сводку не попадает

# ============ РИСК-МЕНЕДЖМЕНТ (ЖЕСТКИЕ ЛИМИТЫ) ============
ALLOWED_LEVERAGE: tuple[int, ...] = (3, 5)   # только 3x или 5x, иное — отказ
MAX_RISK_PER_TRADE_PCT = 5.0                  # максимум 5% депозита на сделку
MIN_RISK_PER_TRADE_PCT = 2.0

# ============ СЕТЬ ============
HTTP_TIMEOUT = 15          # секунд
HTTP_RETRIES = 3
HTTP_RETRY_DELAY = 2       # секунд между попытками
