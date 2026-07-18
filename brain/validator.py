"""
МОДУЛЬ 3e. ВАЛИДАТОР-ЦЕНЗОР. Единственная дверь между идеей LLM и БД.

Проверяет каждую trade_idea:
  1. Символ в белом списке, направление LONG/SHORT.
  2. Плечо строго из config.ALLOWED_LEVERAGE (3, 5).
  3. risk_pct в коридоре 2-5%.
  4. Цена входа адекватна рынку (±1.5% от текущей — иначе идея устарела).
  5. SL/TP по правильные стороны от входа.
  6. Дистанция до стопа в разумных пределах (0.3%-10%).
  7. Risk/Reward не хуже 1:1.
  8. МАРЖУ СЧИТАЕТ САМ (не LLM): margin = risk_amount / (leverage * sl_dist).
  9. Маржа не превышает 30% баланса (защита от узких стопов -> гигантских позиций).

Результат: ApprovedOrder (готов к repository.open_trade) или причина отказа.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import (
    ALLOWED_LEVERAGE,
    CRYPTO_SYMBOLS,
    MAX_RISK_PER_TRADE_PCT,
    MIN_RISK_PER_TRADE_PCT,
)
from utils.logger import get_logger

log = get_logger("brain.validator")

MAX_ENTRY_DEVIATION_PCT = 1.5     # вход не дальше 1.5% от рыночной цены
MIN_SL_DIST_PCT = 0.3             # стоп не ближе 0.3% (шумовая зона)
MAX_SL_DIST_PCT = 10.0            # и не дальше 10%
MIN_RISK_REWARD = 1.0             # TP-потенциал не меньше SL-риска
MAX_MARGIN_SHARE = 0.30           # маржа одной сделки <= 30% баланса


@dataclass
class ApprovedOrder:
    symbol: str
    direction: str
    leverage: int
    entry_price: float
    stop_loss: float
    take_profit: float
    margin_usdt: float
    risk_pct: float
    rationale: str


@dataclass
class Verdict:
    approved: ApprovedOrder | None
    reject_reason: str | None

    @property
    def ok(self) -> bool:
        return self.approved is not None


def _reject(reason: str) -> Verdict:
    log.warning("ОТКЛОНЕНО: %s", reason)
    return Verdict(approved=None, reject_reason=reason)


def validate_idea(idea: dict, market_price: float, balance_usdt: float) -> Verdict:
    """Полная проверка одной идеи LLM. market_price — свежая цена из Модуля 1."""

    # --- 0. Структура и типы ---
    required = ("symbol", "direction", "entry_price", "stop_loss",
                "take_profit", "leverage", "risk_pct")
    missing = [f for f in required if f not in idea]
    if missing:
        return _reject(f"Нет обязательных полей: {missing}")

    try:
        symbol = str(idea["symbol"]).upper()
        direction = str(idea["direction"]).upper()
        entry = float(idea["entry_price"])
        sl = float(idea["stop_loss"])
        tp = float(idea["take_profit"])
        leverage = int(idea["leverage"])
        risk_pct = float(idea["risk_pct"])
    except (TypeError, ValueError) as e:
        return _reject(f"Поля не приводятся к числам: {e}")

    # --- 1. Белые списки ---
    if symbol not in CRYPTO_SYMBOLS:
        return _reject(f"Символ {symbol} не в белом списке {CRYPTO_SYMBOLS}")
    if direction not in ("LONG", "SHORT"):
        return _reject(f"Направление {direction!r} не LONG/SHORT")

    # --- 2. Плечо: ядро риск-менеджмента ---
    if leverage not in ALLOWED_LEVERAGE:
        return _reject(f"Плечо {leverage}x запрещено, разрешены только {ALLOWED_LEVERAGE}")

    # --- 3. Риск на сделку ---
    if not (MIN_RISK_PER_TRADE_PCT <= risk_pct <= MAX_RISK_PER_TRADE_PCT):
        return _reject(
            f"risk_pct={risk_pct} вне коридора "
            f"{MIN_RISK_PER_TRADE_PCT}-{MAX_RISK_PER_TRADE_PCT}%"
        )

    # --- 4. Санитария цен ---
    if min(entry, sl, tp) <= 0:
        return _reject(f"Неположительные цены: entry={entry} sl={sl} tp={tp}")
    if market_price <= 0:
        return _reject("Нет свежей рыночной цены — торговля запрещена")
    deviation = abs(entry / market_price - 1) * 100
    if deviation > MAX_ENTRY_DEVIATION_PCT:
        return _reject(
            f"Вход {entry} отклоняется от рынка {market_price} на {deviation:.2f}% "
            f"(лимит {MAX_ENTRY_DEVIATION_PCT}%) — идея устарела"
        )

    # --- 5. Геометрия SL/TP ---
    if direction == "LONG" and not (sl < entry < tp):
        return _reject(f"LONG требует SL<entry<TP, получено SL={sl} entry={entry} TP={tp}")
    if direction == "SHORT" and not (tp < entry < sl):
        return _reject(f"SHORT требует TP<entry<SL, получено TP={tp} entry={entry} SL={sl}")

    # --- 6. Дистанция стопа ---
    sl_dist_pct = abs(entry - sl) / entry * 100
    if sl_dist_pct < MIN_SL_DIST_PCT:
        return _reject(f"Стоп слишком близко: {sl_dist_pct:.2f}% < {MIN_SL_DIST_PCT}%")
    if sl_dist_pct > MAX_SL_DIST_PCT:
        return _reject(f"Стоп слишком далеко: {sl_dist_pct:.2f}% > {MAX_SL_DIST_PCT}%")

    # Плечо не должно допускать ликвидацию раньше стопа:
    # ликвидация ~ 100/leverage % хода против позиции; стоп обязан быть ближе.
    liq_dist_pct = 100.0 / leverage
    if sl_dist_pct >= liq_dist_pct * 0.8:      # запас 20%
        return _reject(
            f"Стоп {sl_dist_pct:.1f}% слишком близок к зоне ликвидации "
            f"(~{liq_dist_pct:.1f}% при {leverage}x)"
        )

    # --- 7. Risk/Reward ---
    tp_dist_pct = abs(tp - entry) / entry * 100
    rr = tp_dist_pct / sl_dist_pct
    if rr < MIN_RISK_REWARD:
        return _reject(f"R/R {rr:.2f} хуже минимального {MIN_RISK_REWARD}")

    # --- 8. Расчёт маржи (кодом, не LLM) ---
    # Потеря на стопе = margin * leverage * sl_dist. Отсюда:
    risk_amount = balance_usdt * risk_pct / 100
    margin = risk_amount / (leverage * sl_dist_pct / 100)

    # --- 9. Ограничение размера позиции ---
    max_margin = balance_usdt * MAX_MARGIN_SHARE
    if margin > max_margin:
        margin = max_margin
        actual_risk = margin * leverage * sl_dist_pct / 100 / balance_usdt * 100
        log.info("Маржа урезана до %.2f (30%% баланса), фактический риск %.2f%%",
                 margin, actual_risk)
    if margin > balance_usdt:
        return _reject(f"Маржа {margin:.2f} превышает баланс {balance_usdt:.2f}")
    if margin < 5:
        return _reject(f"Маржа {margin:.2f} USDT слишком мала для учёта")

    order = ApprovedOrder(
        symbol=symbol, direction=direction, leverage=leverage,
        entry_price=entry, stop_loss=sl, take_profit=tp,
        margin_usdt=round(margin, 2), risk_pct=risk_pct,
        rationale=str(idea.get("rationale", ""))[:500],
    )
    log.info("ОДОБРЕНО: %s %s x%d, маржа %.2f, SL-дист %.2f%%, R/R %.2f",
             symbol, direction, leverage, order.margin_usdt, sl_dist_pct, rr)
    return Verdict(approved=order, reject_reason=None)
