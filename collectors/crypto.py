"""
МОДУЛЬ 1b. Сборщик крипто-данных через публичный REST API Binance Futures.

Ключи НЕ нужны: эндпоинты /fapi/v1/ticker/24hr и /fapi/v1/klines публичные.
Собираем: текущую цену, изменение за 24ч, объём, funding rate и свечи 4h
(свечи пойдут в промпт LLM для теханализа в Модуле 3).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from config import (
    BINANCE_FAPI_BASE,
    CRYPTO_SYMBOLS,
    HTTP_RETRIES,
    HTTP_RETRY_DELAY,
    HTTP_TIMEOUT,
    KLINES_INTERVAL,
    KLINES_LIMIT,
)
from utils.logger import get_logger

log = get_logger("collectors.crypto")

_session = requests.Session()
_session.headers.update({"User-Agent": "crypto-agent-sandbox/1.0"})


@dataclass
class Candle:
    open_time: int      # unix ms
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class CryptoTicker:
    symbol: str
    last_price: float
    change_24h_pct: float
    volume_24h_usdt: float
    funding_rate_pct: float | None
    candles: list[Candle] = field(default_factory=list)
    ok: bool = True
    error: str | None = None

    def to_dict(self) -> dict:
        """Компактное представление для промпта LLM: последние 30 свечей close/high/low."""
        return {
            "symbol": self.symbol,
            "last_price": self.last_price,
            "change_24h_pct": self.change_24h_pct,
            "volume_24h_usdt": self.volume_24h_usdt,
            "funding_rate_pct": self.funding_rate_pct,
            "candles_4h_tail": [
                {"c": c.close, "h": c.high, "l": c.low} for c in self.candles[-30:]
            ],
        }


@dataclass
class CryptoSnapshot:
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    tickers: list[CryptoTicker] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "tickers": [t.to_dict() for t in self.tickers if t.ok],
            "failed": [t.symbol for t in self.tickers if not t.ok],
        }


def _get(path: str, params: dict) -> dict | list:
    """GET с ретраями и обработкой rate-limit (HTTP 429/418)."""
    url = f"{BINANCE_FAPI_BASE}{path}"
    last_err = ""
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=HTTP_TIMEOUT)
            if resp.status_code in (418, 429):
                wait = int(resp.headers.get("Retry-After", HTTP_RETRY_DELAY * attempt))
                log.warning("Rate limit от Binance (%d), пауза %d c", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            last_err = str(e)
            log.warning("GET %s попытка %d/%d: %s", path, attempt, HTTP_RETRIES, e)
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_DELAY)
    raise ConnectionError(f"Binance недоступен после {HTTP_RETRIES} попыток: {last_err}")


def _fetch_symbol(symbol: str) -> CryptoTicker:
    try:
        # 1. Тикер за 24ч
        t24 = _get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        last_price = float(t24["lastPrice"])
        if last_price <= 0:
            raise ValueError(f"некорректная цена {last_price}")

        # 2. Funding rate (не критично — при ошибке просто None)
        funding: float | None = None
        try:
            prem = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
            funding = round(float(prem["lastFundingRate"]) * 100, 4)
        except Exception as e:  # noqa: BLE001
            log.warning("[%s] funding rate недоступен: %s", symbol, e)

        # 3. Свечи 4h
        raw = _get("/fapi/v1/klines", {
            "symbol": symbol, "interval": KLINES_INTERVAL, "limit": KLINES_LIMIT,
        })
        candles = [
            Candle(
                open_time=int(k[0]),
                open=float(k[1]), high=float(k[2]),
                low=float(k[3]), close=float(k[4]),
                volume=float(k[5]),
            )
            for k in raw
        ]
        if not candles:
            raise ValueError("Binance вернул пустой список свечей")

        return CryptoTicker(
            symbol=symbol,
            last_price=last_price,
            change_24h_pct=round(float(t24["priceChangePercent"]), 2),
            volume_24h_usdt=round(float(t24["quoteVolume"]), 0),
            funding_rate_pct=funding,
            candles=candles,
        )
    except Exception as e:  # noqa: BLE001
        log.error("[%s] сбор не удался: %s", symbol, e)
        return CryptoTicker(symbol=symbol, last_price=0.0, change_24h_pct=0.0,
                            volume_24h_usdt=0.0, funding_rate_pct=None,
                            ok=False, error=str(e))


def fetch_last_price(symbol: str) -> float:
    """Лёгкий запрос одной цены (для мониторинга SL/TP). Бросает ConnectionError."""
    data = _get("/fapi/v1/ticker/price", {"symbol": symbol})
    price = float(data["price"])
    if price <= 0:
        raise ValueError(f"[{symbol}] некорректная цена {price}")
    return price


def fetch_crypto_snapshot() -> CryptoSnapshot:
    """Главная функция модуля: собирает все символы из config.CRYPTO_SYMBOLS."""
    log.info("Сбор крипто-данных: %s", ", ".join(CRYPTO_SYMBOLS))
    snapshot = CryptoSnapshot()
    for symbol in CRYPTO_SYMBOLS:
        snapshot.tickers.append(_fetch_symbol(symbol))
        time.sleep(0.3)   # вежливая пауза, чтобы не упереться в rate limit

    ok_count = sum(1 for t in snapshot.tickers if t.ok)
    log.info("Крипто-снапшот готов: %d/%d символов ОК", ok_count, len(snapshot.tickers))
    return snapshot


if __name__ == "__main__":
    snap = fetch_crypto_snapshot()
    for t in snap.tickers:
        status = "OK " if t.ok else "ERR"
        fr = f"{t.funding_rate_pct:+.4f}%" if t.funding_rate_pct is not None else "n/a"
        print(f"[{status}] {t.symbol:<9} {t.last_price:>12,.2f}  24h: {t.change_24h_pct:+.2f}%  "
              f"funding: {fr}  свечей: {len(t.candles)}")
