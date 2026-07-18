"""
МОДУЛЬ 4a. Пайплайн-оркестратор: единственное место, где модули соединяются.

run_full_analysis():
  Модуль 1 (макро+крипта) -> индикаторы -> новости -> промпт -> LLM ->
  CIO-скоринг -> цензор -> (опц.) открытие сделок в БД -> данные для отчёта.

monitor_open_positions():
  Каждые N минут: свежая цена -> сработал SL/TP? -> закрытие в БД.
  Ограничение песочницы: проверяем по последней цене, а не по high/low
  внутри интервала — быстрый прокол уровня между проверками не поймаем.
  Для демо-учёта это приемлемо, в отчёте честно помечено.

Все функции синхронные (requests/sqlite) — бот вызывает их через asyncio.to_thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brain import cio
from brain.indicators import TechSummary, analyze_candles
from brain.llm_client import LLMError, request_analysis
from brain.news import fetch_news_snapshot
from brain.prompt_builder import build_analysis_prompt
from brain.validator import validate_idea
from collectors.crypto import fetch_crypto_snapshot, fetch_last_price
from collectors.macro import fetch_macro_snapshot
from database.repository import Repository, Trade
from utils.logger import get_logger

log = get_logger("services.pipeline")


@dataclass
class AnalysisResult:
    macro: dict = field(default_factory=dict)
    coin_scores: list[cio.CoinScore] = field(default_factory=list)
    tech: list[TechSummary] = field(default_factory=list)
    llm: dict = field(default_factory=dict)          # analysis, news_summary, macro_comment
    opened_trades: list[Trade] = field(default_factory=list)
    rejected: list[tuple[dict, str]] = field(default_factory=list)   # (идея, причина)
    errors: list[str] = field(default_factory=list)


def run_full_analysis(repo: Repository, execute_trades: bool = True) -> AnalysisResult:
    res = AnalysisResult()

    # --- 1. Данные ---
    macro_snap = fetch_macro_snapshot()
    crypto_snap = fetch_crypto_snapshot()
    res.macro = macro_snap.to_dict()

    live_prices: dict[str, float] = {}
    funding: dict[str, float | None] = {}
    for t in crypto_snap.tickers:
        if not t.ok:
            res.errors.append(f"Нет данных по {t.symbol}: {t.error}")
            continue
        live_prices[t.symbol] = t.last_price
        funding[t.symbol] = t.funding_rate_pct
        try:
            res.tech.append(analyze_candles(t.symbol, t.candles))
        except ValueError as e:
            res.errors.append(str(e))

    if not res.tech:
        res.errors.append("Ни одной монеты с данными — анализ невозможен")
        return res

    news_snap = fetch_news_snapshot()

    # --- 2. LLM ---
    news_score = 0.0
    try:
        prompt = build_analysis_prompt(
            macro=res.macro,
            crypto=crypto_snap.to_dict(),
            tech=[t.to_dict() for t in res.tech],
            news_lines=news_snap.to_prompt_lines(),
        )
        res.llm = request_analysis(prompt)
        news_score = float(res.llm.get("news_score", 0.0))
    except (LLMError, TypeError, ValueError) as e:
        # Деградация: отчёт выйдет без текста LLM, скоринг — на кодовых факторах
        res.errors.append(f"LLM недоступен: {e}")
        res.llm = {}
        log.error("LLM-этап пропущен: %s", e)

    # --- 3. CIO-скоринг ---
    macro_s = cio.macro_score(res.macro)
    for t in res.tech:
        res.coin_scores.append(cio.aggregate(
            symbol=t.symbol,
            tech_score=t.score,
            macro_s=macro_s,
            news_s=news_score,
            deriv_s=cio.derivatives_score(funding.get(t.symbol)),
        ))

    # --- 4. Цензор + исполнение ---
    ideas = res.llm.get("trade_ideas", []) if isinstance(res.llm, dict) else []
    if not isinstance(ideas, list):
        ideas = []
    open_symbols = {t.symbol for t in repo.list_open_trades()}

    for idea in ideas:
        symbol = str(idea.get("symbol", "")).upper()
        if symbol in open_symbols:
            reason = f"По {symbol} уже есть открытая позиция"
            res.rejected.append((idea, reason))
            repo.log_signal(raw_response=idea, verdict="REJECTED",
                            symbol=symbol, reject_reason=reason)
            continue

        market_price = live_prices.get(symbol, 0.0)
        verdict = validate_idea(idea, market_price, repo.get_balance())
        if not verdict.ok:
            res.rejected.append((idea, verdict.reject_reason))
            repo.log_signal(raw_response=idea, verdict="REJECTED",
                            symbol=symbol or None, reject_reason=verdict.reject_reason)
            continue

        if not execute_trades:
            repo.log_signal(raw_response=idea, verdict="ACCEPTED", symbol=symbol,
                            reject_reason="dry-run: сделка не открыта")
            continue

        o = verdict.approved
        trade = repo.open_trade(
            symbol=o.symbol, direction=o.direction, leverage=o.leverage,
            entry_price=o.entry_price, stop_loss=o.stop_loss,
            take_profit=o.take_profit, margin_usdt=o.margin_usdt,
            risk_pct=o.risk_pct, llm_rationale=o.rationale,
        )
        repo.log_signal(raw_response=idea, verdict="ACCEPTED",
                        symbol=symbol, trade_id=trade.id)
        res.opened_trades.append(trade)
        open_symbols.add(symbol)

    if not ideas and res.llm:
        repo.log_signal(raw_response=res.llm, verdict="NO_TRADE")

    log.info("Анализ завершён: скорингов %d, открыто %d, отклонено %d, ошибок %d",
             len(res.coin_scores), len(res.opened_trades), len(res.rejected), len(res.errors))
    return res


def monitor_open_positions(repo: Repository) -> list[Trade]:
    """Проверка SL/TP по свежим ценам. Возвращает закрытые в этом проходе сделки."""
    closed: list[Trade] = []
    for trade in repo.list_open_trades():
        try:
            price = fetch_last_price(trade.symbol)
        except (ConnectionError, ValueError, KeyError) as e:
            log.warning("Мониторинг #%d %s: цена недоступна (%s), пропуск",
                        trade.id, trade.symbol, e)
            continue

        reason: str | None = None
        if trade.direction == "LONG":
            if price <= trade.stop_loss:
                reason = "SL"
            elif price >= trade.take_profit:
                reason = "TP"
        else:  # SHORT
            if price >= trade.stop_loss:
                reason = "SL"
            elif price <= trade.take_profit:
                reason = "TP"

        if reason:
            closed.append(repo.close_trade(trade.id, close_price=price, reason=reason))
    return closed
