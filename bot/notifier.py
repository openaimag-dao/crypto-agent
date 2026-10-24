"""
МОДУЛЬ 4b. Нотификатор: чистые функции форматирования (HTML для Telegram).

Отделены от бота, чтобы тестировать офлайн. Никаких сетевых вызовов.
"""

from __future__ import annotations

from html import escape

from brain.divergence import (ABOVE_RESISTANCE, BELOW_SUPPORT, HOOD_STRONGER,
                              HOOD_WEAKER, DivergenceReading)
from config import HOOD_RESISTANCE, HOOD_SUPPORT
from database.repository import Trade
from services.pipeline import AnalysisResult

_EMOJI = {"BULLISH": "🟢", "NEUTRAL": "🟡", "BEARISH": "🔴"}


def _fmt_price(p: float) -> str:
    return f"{p:,.2f}".replace(",", " ")


def format_briefing(res: AnalysisResult) -> str:
    """Утренний брифинг: скоринг + разбор слагаемых + текст LLM + сделки."""
    lines: list[str] = ["📅 <b>AIMAG AI Market Report</b> (песочница)", ""]

    # Скоринг по монетам с прозрачными слагаемыми
    for cs in res.coin_scores:
        lines.append(f"{_EMOJI[cs.label]} <b>{cs.symbol}</b> — композит роста: "
                     f"<b>{cs.probability_up_pct}%</b>")
        parts = ", ".join(
            f"{name} {c['contribution_pct']:+.1f}"
            for name, c in cs.components.items()
        )
        lines.append(f"   <i>слагаемые (п.п.): {parts}</i>")
    lines.append("")

    # Текст от LLM (если был)
    if res.llm.get("macro_comment"):
        lines += [f"🌍 <b>Макро:</b> {escape(str(res.llm['macro_comment']))}", ""]
    if res.llm.get("news_summary"):
        lines += [f"📰 <b>Новости:</b> {escape(str(res.llm['news_summary']))}", ""]
    if res.llm.get("analysis"):
        lines += [f"🧠 <b>Анализ:</b> {escape(str(res.llm['analysis']))}", ""]

    # Сделки
    if res.opened_trades:
        lines.append("⚡ <b>Открыты виртуальные позиции:</b>")
        for t in res.opened_trades:
            lines.append(format_trade_opened(t, header=False))
        lines.append("")
    if res.rejected:
        lines.append(f"🛡 Цензор отклонил идей: <b>{len(res.rejected)}</b>")
        for idea, reason in res.rejected[:3]:
            sym = escape(str(idea.get("symbol", "?")))
            lines.append(f"   • {sym}: {escape(str(reason))[:120]}")
        lines.append("")
    if not res.opened_trades and not res.rejected:
        lines += ["😴 Явных сетапов нет — сегодня без сделок.", ""]

    if res.divergence:
        lines += [format_divergence(res.divergence, compact=True), ""]

    if res.errors:
        lines.append("⚠️ <i>Часть данных недоступна: " +
                     "; ".join(escape(e)[:80] for e in res.errors[:3]) + "</i>")

    lines.append("<i>Композит — не статистическая вероятность. Виртуальный учёт, "
                 "не инвестиционная рекомендация.</i>")
    return "\n".join(lines)


def format_trade_opened(t: Trade, header: bool = True) -> str:
    arrow = "📈 LONG" if t.direction == "LONG" else "📉 SHORT"
    body = (f"{arrow} <b>{t.symbol}</b> x{t.leverage} | маржа {_fmt_price(t.margin_usdt)} USDT\n"
            f"   вход {_fmt_price(t.entry_price)} | SL {_fmt_price(t.stop_loss)} | "
            f"TP {_fmt_price(t.take_profit)} | риск {t.risk_pct}%")
    if t.llm_rationale:
        body += f"\n   💬 <i>{escape(t.llm_rationale)[:200]}</i>"
    return ("⚡ <b>Открыта виртуальная позиция #%d</b>\n" % t.id + body) if header else body


def format_trade_closed(t: Trade) -> str:
    sign = "🟢 ПРИБЫЛЬ" if (t.pnl_usdt or 0) > 0 else "🔴 УБЫТОК"
    reason_ru = {"TP": "тейк-профит", "SL": "стоп-лосс",
                 "MANUAL": "вручную", "LIQUIDATION": "ликвидация"}.get(t.close_reason, "?")
    return (f"{sign} — закрыта позиция #{t.id}\n"
            f"<b>{t.symbol}</b> {t.direction} x{t.leverage} ({reason_ru})\n"
            f"вход {_fmt_price(t.entry_price)} → выход {_fmt_price(t.close_price or 0)}\n"
            f"PnL: <b>{t.pnl_usdt:+,.2f} USDT</b>".replace(",", " "))


def format_balance(summary: dict) -> str:
    ret = summary["total_return_pct"]
    emoji = "🟢" if ret >= 0 else "🔴"
    return (f"💰 <b>Виртуальный счёт</b>\n"
            f"Свободно: <b>{_fmt_price(summary['balance_usdt'])} USDT</b>\n"
            f"В позициях: {_fmt_price(summary['in_positions_usdt'])} USDT\n"
            f"Депозит: {_fmt_price(summary['initial_deposit'])} USDT\n"
            f"{emoji} Доходность: <b>{ret:+.2f}%</b>\n"
            f"Закрыто сделок: {summary['closed_trades']} | "
            f"Винрейт: {summary['winrate_pct']}% | "
            f"Σ PnL: {summary['total_pnl_usdt']:+,.2f} USDT".replace(",", " "))


def format_positions(trades: list[Trade], prices: dict[str, float]) -> str:
    if not trades:
        return "📭 Открытых позиций нет."
    lines = ["📊 <b>Открытые позиции:</b>", ""]
    for t in trades:
        price = prices.get(t.symbol)
        pnl_line = ""
        if price:
            raw = t.qty * (price - t.entry_price) if t.direction == "LONG" \
                else t.qty * (t.entry_price - price)
            pnl = max(raw, -t.margin_usdt)
            pnl_line = f"\n   тек. цена {_fmt_price(price)} | нереализ. PnL: <b>{pnl:+.2f}</b>"
        lines.append(f"#{t.id} {format_trade_opened(t, header=False)}{pnl_line}")
        lines.append("")
    return "\n".join(lines)


_DIV_TEXT = {
    HOOD_WEAKER: ("🔻", "HOOD слабее BTC — риск-аппетит уходит раньше крипты. "
                       "Исторически BTC затем догонял вниз; осторожнее с лонгами."),
    HOOD_STRONGER: ("🔺", "HOOD сильнее BTC — розница возвращается в риск. "
                         "Летом 2026 после такого разрыва BTC догнал вверх."),
}


def format_divergence(r: DivergenceReading, compact: bool = False,
                      events: list[str] | None = None) -> str:
    """Блок расхождения HOOD/BTC. compact — одна-две строки для брифинга."""
    emoji, text = _DIV_TEXT.get(r.state, ("⚪", "Разрыв в норме — HOOD и BTC идут вместе."))
    zone = {BELOW_SUPPORT: f"ниже поддержки {HOOD_SUPPORT:.0f}",
            ABOVE_RESISTANCE: f"выше сопротивления {HOOD_RESISTANCE:.0f}"}.get(
        r.zone, f"в диапазоне {HOOD_SUPPORT:.0f}–{HOOD_RESISTANCE:.0f}")
    head = (f"{emoji} <b>HOOD/BTC за {r.window_days} дн.:</b> HOOD {r.hood_ret_pct:+.1f}%, "
            f"BTC {r.btc_ret_pct:+.1f}% → разрыв <b>{r.gap_pct:+.1f} п.п.</b>")
    if compact:
        return head + f"\n   <i>HOOD {_fmt_price(r.hood_price)} ({zone}). {text}</i>"

    title = "🚨 <b>Сигнал смены фазы: HOOD/BTC</b>" if events else "📐 <b>Расхождение HOOD/BTC</b>"
    lines = [title, "", head, f"HOOD {_fmt_price(r.hood_price)} — {zone}",
             f"BTC {_fmt_price(r.btc_price)}", "", text]
    if events and "zone" in events:
        if r.zone == BELOW_SUPPORT:
            lines.append(f"⚠️ HOOD пробил поддержку {HOOD_SUPPORT:.0f} — подтверждение ухода от риска.")
        elif r.zone == ABOVE_RESISTANCE:
            lines.append(f"✅ HOOD вернулся выше {HOOD_RESISTANCE:.0f} — риск-аппетит восстанавливается.")
        else:
            lines.append("HOOD вернулся в диапазон — зона снова нейтральна.")
    lines += ["", f"<i>Данные на {r.as_of}. Подтверждающий индикатор (2–3 эпизода в истории), "
                  "не торговый сигнал.</i>"]
    return "\n".join(lines)
