"""
МОДУЛЬ 3f. CIO-агрегатор: прозрачный весовой скоринг.

Веса: Technical 35%, Macro 25%, News 25%, Derivatives 15%.
Все входы в шкале -1..+1, выход — "вероятность роста" 0-100% на монету.

ВАЖНО (честность): это НЕ статистическая вероятность, а нормированный
композит сигналов. В отчёте всегда показываем слагаемые, чтобы читатель
видел, из чего сложилось число. Реальную точность покажет только
таблица predictions после месяцев сверки с фактом.
"""

from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {
    "technical": 0.35,
    "macro": 0.25,
    "news": 0.25,
    "derivatives": 0.15,
}


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def macro_score(macro_dict: dict) -> float:
    """
    Risk-on/off из макро-снапшота (MacroSnapshot.to_dict()):
      Nasdaq/S&P растут -> +, DXY растёт -> -, VIX растёт -> -, золото растёт -> лёгкий -.
    Используем change_1d_pct, нормируем на типичный дневной ход.
    """
    quotes = {q["name"]: q for q in macro_dict.get("quotes", [])}
    s, used = 0.0, 0

    def add(name: str, weight: float, scale: float, invert: bool = False) -> None:
        nonlocal s, used
        q = quotes.get(name)
        if q is None:
            return
        v = _clamp(q["change_1d_pct"] / scale)
        s += weight * (-v if invert else v)
        used += 1

    add("NASDAQ", 0.30, 1.5)
    add("SP500", 0.25, 1.2)
    add("DXY", 0.25, 0.5, invert=True)
    add("VIX", 0.15, 8.0, invert=True)
    add("GOLD", 0.05, 1.5, invert=True)

    return round(_clamp(s), 2) if used else 0.0


def derivatives_score(funding_rate_pct: float | None) -> float:
    """
    Пока единственный вход — funding (Модуль 1 его уже отдаёт).
    Сильно положительный funding (>0.05%) = перегрев лонгов = медвежий фактор.
    Сильно отрицательный = перегрев шортов = бычий (топливо для шорт-сквиза).
    Позже сюда добавим OI и ликвидации — интерфейс не изменится.
    """
    if funding_rate_pct is None:
        return 0.0
    return round(_clamp(-funding_rate_pct / 0.10), 2)


@dataclass
class CoinScore:
    symbol: str
    probability_up_pct: float          # 0-100
    components: dict                   # вклад каждого фактора (для прозрачности)
    label: str                         # BULLISH / NEUTRAL / BEARISH


def aggregate(
    symbol: str,
    tech_score: float,          # из indicators.TechSummary.score
    macro_s: float,             # из macro_score()
    news_s: float,              # news_score из ответа LLM
    deriv_s: float,             # из derivatives_score()
) -> CoinScore:
    inputs = {
        "technical": _clamp(tech_score),
        "macro": _clamp(macro_s),
        "news": _clamp(news_s),
        "derivatives": _clamp(deriv_s),
    }
    composite = sum(WEIGHTS[k] * v for k, v in inputs.items())     # -1..+1
    prob = round((composite + 1) / 2 * 100, 1)                     # -> 0-100

    label = "BULLISH" if prob >= 60 else "BEARISH" if prob <= 40 else "NEUTRAL"
    components = {
        k: {"score": inputs[k], "weight": WEIGHTS[k],
            "contribution_pct": round(WEIGHTS[k] * inputs[k] * 50, 1)}
        for k in WEIGHTS
    }
    return CoinScore(symbol=symbol, probability_up_pct=prob,
                     components=components, label=label)
