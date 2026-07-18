"""
МОДУЛЬ 3a. Технические индикаторы: EMA20/50, RSI14, MACD(12,26,9).

Принцип: математику считает Python, а не LLM. Модель получает уже готовые
цифры и занимается интерпретацией. Никаких галлюцинаций в расчётах.

Вход — список свечей из collectors/crypto.py (Candle с полем close).
Чистый Python без pandas: 100 свечей — это копейки для цикла.
"""

from __future__ import annotations

from dataclasses import dataclass


def ema(values: list[float], period: int) -> list[float]:
    """Экспоненциальная скользящая. Первое значение — SMA за period."""
    if len(values) < period:
        raise ValueError(f"Нужно минимум {period} значений, есть {len(values)}")
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values: list[float], period: int = 14) -> float:
    """RSI по Уайлдеру (сглаженные средние). Возвращает последнее значение."""
    if len(values) < period + 1:
        raise ValueError(f"Нужно минимум {period + 1} значений, есть {len(values)}")
    gains, losses = [], []
    for prev, cur in zip(values[:-1], values[1:]):
        diff = cur - prev
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def macd(values: list[float]) -> tuple[float, float, float]:
    """MACD(12,26,9): (линия MACD, сигнальная, гистограмма) — последние значения."""
    if len(values) < 26 + 9:
        raise ValueError(f"Нужно минимум 35 значений, есть {len(values)}")
    ema12 = ema(values, 12)
    ema26 = ema(values, 26)
    # выравниваем: ema12 длиннее, обрезаем начало
    macd_line = [a - b for a, b in zip(ema12[-len(ema26):], ema26)]
    signal = ema(macd_line, 9)
    m, s = macd_line[-1], signal[-1]
    return round(m, 4), round(s, 4), round(m - s, 4)


@dataclass
class TechSummary:
    symbol: str
    price: float
    ema20: float
    ema50: float
    rsi14: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    volume_change_pct: float      # объём последней свечи к среднему за 20
    score: float                  # -1.0 (bear) ... +1.0 (bull) — для CIO
    label: str                    # BULLISH / NEUTRAL / BEARISH

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "price": self.price,
            "ema20": self.ema20, "ema50": self.ema50, "rsi14": self.rsi14,
            "macd_hist": self.macd_hist,
            "volume_change_pct": self.volume_change_pct,
            "tech_label": self.label,
        }


MACD_DEADZONE_PCT = 0.05   # |hist| < 0.05% цены = шум, не сигнал


def _tech_score(price: float, e20: float, e50: float, rsi_v: float, hist: float) -> float:
    """
    Прозрачный скоринг. Веса: тренд 0.5 (доминирует), RSI 0.25 (контр-сигнал),
    MACD 0.25 (моментум, с мёртвой зоной). Урок из тестов: на равномерном
    тренде MACD-гистограмма ~0 (шум), а контр-трендовый RSI не должен
    перевешивать сам тренд.
    """
    s = 0.0
    # 1) Тренд по EMA — главный фактор
    if price > e20 > e50:
        s += 0.50
    elif price < e20 < e50:
        s -= 0.50
    # 2) RSI: экстремумы как контр-сигнал, умеренные зоны — слабое подтверждение
    if rsi_v < 30:
        s += 0.25
    elif rsi_v > 70:
        s -= 0.25
    elif rsi_v > 55:
        s += 0.10
    elif rsi_v < 45:
        s -= 0.10
    # 3) MACD-гистограмма, нормированная на цену, с мёртвой зоной
    hist_pct = hist / price * 100 if price > 0 else 0.0
    if hist_pct > MACD_DEADZONE_PCT:
        s += 0.25
    elif hist_pct < -MACD_DEADZONE_PCT:
        s -= 0.25
    return round(max(-1.0, min(1.0, s)), 2)


def analyze_candles(symbol: str, candles: list) -> TechSummary:
    """Главная функция: свечи (Candle из Модуля 1) -> сводка индикаторов."""
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]
    if len(closes) < 51:
        raise ValueError(f"[{symbol}] мало свечей для EMA50: {len(closes)}")

    e20 = ema(closes, 20)[-1]
    e50 = ema(closes, 50)[-1]
    r = rsi(closes, 14)
    m_line, m_sig, m_hist = macd(closes)

    avg_vol = sum(volumes[-21:-1]) / 20
    vol_chg = round((volumes[-1] / avg_vol - 1) * 100, 1) if avg_vol > 0 else 0.0

    price = closes[-1]
    score = _tech_score(price, e20, e50, r, m_hist)
    label = "BULLISH" if score > 0.2 else "BEARISH" if score < -0.2 else "NEUTRAL"

    return TechSummary(
        symbol=symbol, price=round(price, 2),
        ema20=round(e20, 2), ema50=round(e50, 2), rsi14=r,
        macd_line=m_line, macd_signal=m_sig, macd_hist=m_hist,
        volume_change_pct=vol_chg, score=score, label=label,
    )
