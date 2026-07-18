"""
МОДУЛЬ 1a. Сборщик макро-индексов через yfinance.

Возвращает снапшот: последняя цена, изменение за день (%), изменение за 5 дней (%).
Каждый тикер обрабатывается независимо — падение одного не роняет остальные.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yfinance as yf

from config import HTTP_RETRIES, HTTP_RETRY_DELAY, MACRO_TICKERS
from utils.logger import get_logger

log = get_logger("collectors.macro")


@dataclass
class MacroQuote:
    name: str                 # "NASDAQ", "DXY" ...
    ticker: str               # "^IXIC" ...
    price: float
    change_1d_pct: float
    change_5d_pct: float
    ok: bool = True
    error: str | None = None


@dataclass
class MacroSnapshot:
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    quotes: list[MacroQuote] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(not q.ok for q in self.quotes)

    def to_dict(self) -> dict:
        """Плоский словарь — удобно скармливать в промпт LLM (Модуль 3)."""
        return {
            "timestamp": self.timestamp,
            "quotes": [
                {
                    "name": q.name,
                    "price": q.price,
                    "change_1d_pct": q.change_1d_pct,
                    "change_5d_pct": q.change_5d_pct,
                }
                for q in self.quotes
                if q.ok
            ],
            "failed": [q.name for q in self.quotes if not q.ok],
        }


def _fetch_one(name: str, ticker: str) -> MacroQuote:
    """Одна котировка с ретраями. История за 7 дней -> считаем 1d и 5d изменение."""
    last_err = ""
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            hist = yf.Ticker(ticker).history(period="7d", interval="1d")
            if hist.empty or len(hist) < 2:
                raise ValueError(f"пустая/короткая история ({len(hist)} строк)")

            closes = hist["Close"].dropna()
            price = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            base_5d = float(closes.iloc[0])

            if prev == 0 or base_5d == 0:
                raise ValueError("нулевая цена в истории")

            return MacroQuote(
                name=name,
                ticker=ticker,
                price=round(price, 2),
                change_1d_pct=round((price / prev - 1) * 100, 2),
                change_5d_pct=round((price / base_5d - 1) * 100, 2),
            )
        except Exception as e:  # noqa: BLE001 — логируем и ретраим любую ошибку сети/данных
            last_err = str(e)
            log.warning("[%s] попытка %d/%d не удалась: %s", name, attempt, HTTP_RETRIES, e)
            if attempt < HTTP_RETRIES:
                time.sleep(HTTP_RETRY_DELAY)

    log.error("[%s] все попытки исчерпаны: %s", name, last_err)
    return MacroQuote(name=name, ticker=ticker, price=0.0,
                      change_1d_pct=0.0, change_5d_pct=0.0,
                      ok=False, error=last_err)


def fetch_macro_snapshot() -> MacroSnapshot:
    """Главная функция модуля: собирает все индексы из config.MACRO_TICKERS."""
    log.info("Сбор макро-данных: %s", ", ".join(MACRO_TICKERS))
    snapshot = MacroSnapshot()
    for name, ticker in MACRO_TICKERS.items():
        snapshot.quotes.append(_fetch_one(name, ticker))

    ok_count = sum(1 for q in snapshot.quotes if q.ok)
    log.info("Макро-снапшот готов: %d/%d тикеров ОК", ok_count, len(snapshot.quotes))
    return snapshot


if __name__ == "__main__":
    snap = fetch_macro_snapshot()
    for q in snap.quotes:
        status = "OK " if q.ok else "ERR"
        print(f"[{status}] {q.name:<7} {q.price:>12,.2f}  1d: {q.change_1d_pct:+.2f}%  5d: {q.change_5d_pct:+.2f}%")
