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


# ---------- Киты Hyperliquid ----------

def _usd(v: float) -> str:
    """Компактная сумма: $1.2M / $350K / $900."""
    a = abs(v)
    s = f"${a/1e6:.1f}M" if a >= 1e6 else f"${a/1e3:.0f}K" if a >= 1e3 else f"${a:.0f}"
    return ("−" if v < 0 else "") + s


def _coin(c: str) -> str:
    """HIP-3 рынки акций приходят как 'xyz:ORCL' — показываем как 'ORCL (акция)'."""
    return f"{c.split(':', 1)[1]} (акция)" if ":" in c else c


def split_message(text: str, limit: int = 4000) -> list[str]:
    """Режет длинную сводку по абзацам под лимит Telegram (4096 символов)."""
    parts, cur = [], ""
    for block in text.split("\n\n"):
        cand = f"{cur}\n\n{block}" if cur else block
        if len(cand) > limit and cur:
            parts.append(cur)
            cur = block
        else:
            cur = cand
    if cur:
        parts.append(cur)
    return parts


_ACTION_RU = {"Open Long": "открыл лонг", "Open Short": "открыл шорт",
              "Close Long": "закрыл лонг", "Close Short": "закрыл шорт",
              "Long > Short": "перевернул в шорт", "Short > Long": "перевернул в лонг"}


def format_whales(snaps, exposure: dict, lookback_hours: int = 24) -> str:
    """Сводка по китам Hyperliquid: сделки за окно + текущие позиции + консенсус."""
    lines = [f"🐋 <b>Киты Hyperliquid — сводка за {lookback_hours} ч</b>", ""]

    for s in snaps:
        short_addr = f"{s.address[:6]}…{s.address[-4:]}"
        link = f'<a href="https://hypurrscan.io/address/{s.address}">{short_addr}</a>'
        if not s.ok:
            lines += [f"⚠️ <b>{escape(s.label)}</b> {link}: нет данных", ""]
            continue
        head = f"<b>{escape(s.label)}</b> {link}"
        if s.account_value > 0:
            head += f" · счёт {_usd(s.account_value)} · плечо {s.gross_leverage:.1f}x"
        lines.append(head)

        if s.activity:
            pnl_emoji = "🟢" if s.realized_pnl_window >= 0 else "🔴"
            lines.append(f"   {pnl_emoji} реализовано: <b>{_usd(s.realized_pnl_window)}</b>")
            for a in s.activity[:5]:
                acts = ", ".join(_ACTION_RU.get(x, escape(x)) for x in sorted(a.actions))
                vol = []
                if a.opened_usd:
                    vol.append(f"откр. {_usd(a.opened_usd)}")
                if a.closed_usd:
                    vol.append(f"закр. {_usd(a.closed_usd)}")
                pnl = f", PnL {_usd(a.realized_pnl)}" if abs(a.realized_pnl) >= 1000 else ""
                lines.append(f"   • {escape(_coin(a.coin))}: {acts} ({' / '.join(vol)}{pnl})")
        else:
            lines.append("   😴 сделок не было")

        if s.positions:
            pos = ", ".join(f"{escape(_coin(p.coin))} {'🟢L' if p.direction == 'LONG' else '🔴S'} "
                            f"{_usd(p.notional_usd)} x{p.leverage:g}" for p in s.positions[:4])
            lines.append(f"   📌 держит: {pos}")
        else:
            lines.append("   📭 позиций нет")
        lines.append("")

    if exposure:
        lines.append("⚖️ <b>Консенсус китов (открытые позиции):</b>")
        for coin, e in list(exposure.items())[:6]:
            net = e["long"] - e["short"]
            side = "🟢 нетто-лонг" if net > 0 else "🔴 нетто-шорт"
            lines.append(f"   {escape(_coin(coin))}: {side} {_usd(abs(net))} "
                         f"(лонгов {e['longs']}, шортов {e['shorts']})")
        lines.append("")

    lines.append("<i>Только наблюдение: киты торгуют с плечом до 40x, их сделки — "
                 "контекст, а не сигнал на вход.</i>")
    return "\n".join(lines)
