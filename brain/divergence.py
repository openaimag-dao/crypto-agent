"""
МОДУЛЬ 5. Расхождение HOOD / BTC — индикатор смены фазы рынка.

Идея: Robinhood (HOOD) — прокси розничного риск-аппетита, корреляция с BTC ~0.6.
В 2025–2026 HOOD несколько раз разворачивался на несколько недель раньше BTC
(лето 2026: HOOD 82→113 при BTC на месте, потом BTC +25%). Поэтому сигналом
считаем не сам уровень, а РАЗРЫВ доходностей за окно (по умолчанию 4 недели):

    gap = доходность HOOD за 28 дней − доходность BTC за 28 дней (п.п.)

  gap ≤ −порог  →  HOOD_WEAKER   (риск-аппетит уходит раньше крипты, осторожно)
  gap ≥ +порог  →  HOOD_STRONGER (риск-аппетит возвращается, BTC может догнать)

Гистерезис: вход в состояние при |gap| ≥ DIVERGENCE_ENTER_PCT, выход при
|gap| < DIVERGENCE_EXIT_PCT — чтобы алерт не дёргался вокруг порога.
Дополнительно отслеживается пробой ключевых зон HOOD (поддержка/сопротивление).

Сигнал подтверждающий, а не торговый: на дневном лаге опережения нет,
эффект виден только на горизонте недель и подтверждён 2–3 эпизодами.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import timedelta

import pandas as pd
import yfinance as yf

from config import (
    DIVERGENCE_ENTER_PCT,
    DIVERGENCE_EXIT_PCT,
    DIVERGENCE_WINDOW_DAYS,
    HOOD_RESISTANCE,
    HOOD_SUPPORT,
)
from utils.logger import get_logger

log = get_logger("brain.divergence")

STATE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "divergence_state.json"
)

NORMAL, HOOD_WEAKER, HOOD_STRONGER = "NORMAL", "HOOD_WEAKER", "HOOD_STRONGER"
BELOW_SUPPORT, IN_RANGE, ABOVE_RESISTANCE = "BELOW_SUPPORT", "IN_RANGE", "ABOVE_RESISTANCE"


@dataclass
class DivergenceReading:
    hood_price: float
    btc_price: float
    hood_ret_pct: float
    btc_ret_pct: float
    gap_pct: float
    window_days: int
    state: str          # NORMAL / HOOD_WEAKER / HOOD_STRONGER
    zone: str           # BELOW_SUPPORT / IN_RANGE / ABOVE_RESISTANCE
    as_of: str          # дата последнего закрытия HOOD

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- чистые функции (тестируются офлайн) ----------

def window_return(closes: pd.Series, days: int) -> float:
    """Доходность (%) от последнего закрытия на дату ≤ (последняя дата − days)."""
    closes = closes.dropna()
    if len(closes) < 2:
        raise ValueError("мало данных для расчёта доходности")
    last_date = closes.index[-1]
    base = closes[closes.index <= last_date - timedelta(days=days)]
    if base.empty:
        raise ValueError(f"история короче окна {days} дн.")
    return (float(closes.iloc[-1]) / float(base.iloc[-1]) - 1) * 100


def classify_state(gap: float, prev_state: str = NORMAL,
                   enter: float = DIVERGENCE_ENTER_PCT,
                   exit_: float = DIVERGENCE_EXIT_PCT) -> str:
    """Состояние с гистерезисом: держим прежний сигнал, пока |gap| ≥ exit_."""
    if gap <= -enter:
        return HOOD_WEAKER
    if gap >= enter:
        return HOOD_STRONGER
    if prev_state == HOOD_WEAKER and gap <= -exit_:
        return HOOD_WEAKER
    if prev_state == HOOD_STRONGER and gap >= exit_:
        return HOOD_STRONGER
    return NORMAL


def classify_zone(price: float, support: float = HOOD_SUPPORT,
                  resistance: float = HOOD_RESISTANCE) -> str:
    if price < support:
        return BELOW_SUPPORT
    if price > resistance:
        return ABOVE_RESISTANCE
    return IN_RANGE


def compute(hood: pd.Series, btc: pd.Series, prev_state: str = NORMAL,
            window_days: int = DIVERGENCE_WINDOW_DAYS) -> DivergenceReading:
    hood, btc = hood.dropna(), btc.dropna()
    h_ret = window_return(hood, window_days)
    b_ret = window_return(btc, window_days)
    gap = h_ret - b_ret
    hood_price = float(hood.iloc[-1])
    return DivergenceReading(
        hood_price=round(hood_price, 2),
        btc_price=round(float(btc.iloc[-1]), 2),
        hood_ret_pct=round(h_ret, 1),
        btc_ret_pct=round(b_ret, 1),
        gap_pct=round(gap, 1),
        window_days=window_days,
        state=classify_state(gap, prev_state),
        zone=classify_zone(hood_price),
        as_of=hood.index[-1].strftime("%Y-%m-%d"),
    )


# ---------- состояние между проверками ----------

def load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(reading: DivergenceReading) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"state": reading.state, "zone": reading.zone,
                   "gap_pct": reading.gap_pct, "as_of": reading.as_of}, f)


# ---------- сеть ----------

def _closes(ticker: str, days: int) -> pd.Series:
    hist = yf.Ticker(ticker).history(period=f"{days + 20}d", interval="1d")
    if hist.empty:
        raise ValueError(f"{ticker}: пустая история")
    s = hist["Close"]
    s.index = s.index.tz_localize(None).normalize()
    return s


def fetch_reading(prev_state: str | None = None) -> DivergenceReading:
    if prev_state is None:
        prev_state = load_state().get("state", NORMAL)
    hood = _closes("HOOD", DIVERGENCE_WINDOW_DAYS)
    btc = _closes("BTC-USD", DIVERGENCE_WINDOW_DAYS)
    r = compute(hood, btc, prev_state)
    log.info("HOOD/BTC: HOOD %+.1f%%, BTC %+.1f%%, разрыв %+.1f п.п. → %s, зона %s",
             r.hood_ret_pct, r.btc_ret_pct, r.gap_pct, r.state, r.zone)
    return r


def check_for_alert() -> tuple[DivergenceReading, list[str]]:
    """Для планировщика: возвращает замер и список событий (смена состояния/зоны).
    При первом запуске алертим, только если сигнал уже активен."""
    saved = load_state()
    reading = fetch_reading(saved.get("state", NORMAL))
    events: list[str] = []
    if saved:
        if reading.state != saved.get("state"):
            events.append("state")
        if reading.zone != saved.get("zone"):
            events.append("zone")
    elif reading.state != NORMAL:
        events.append("state")
    save_state(reading)
    return reading, events


if __name__ == "__main__":
    r = fetch_reading()
    print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
