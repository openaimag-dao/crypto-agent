"""
МОДУЛЬ 4c. Хендлеры команд Telegram (aiogram 3.x).

Бот персональный: все команды доступны только OWNER_CHAT_ID из .env.
Чужие сообщения молча игнорируются (без ответа — чтобы не светиться).

Блокирующие операции (сеть, LLM, sqlite) уводятся в asyncio.to_thread,
чтобы не замораживать event loop бота.
"""

from __future__ import annotations

import asyncio
import os

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import notifier
from collectors.crypto import fetch_last_price
from database.repository import Repository
from services.pipeline import run_full_analysis
from utils.logger import get_logger

log = get_logger("bot.handlers")

router = Router()
repo = Repository()

_analysis_lock = asyncio.Lock()   # /report не должен запускаться параллельно


def _owner_id() -> int:
    return int(os.getenv("TELEGRAM_CHAT_ID", "0"))


@router.message()
async def gatekeeper(message: Message) -> None:
    """Первый фильтр не нужен — aiogram матчит команды раньше. Этот хендлер
    ловит всё остальное (текст без команды) и отвечает подсказкой владельцу."""
    if message.chat.id != _owner_id():
        return
    await message.answer(
        "Команды: /report — полный анализ, /balance — счёт, /positions — позиции,\n"
        "/close &lt;id&gt; — закрыть вручную, /rejected — статистика цензора,\n"
        "/reset confirm — сброс песочницы", parse_mode="HTML")


def owned(handler):
    """Декоратор: команда выполняется только для владельца."""
    async def wrapper(message: Message, *args, **kwargs):
        if message.chat.id != _owner_id():
            log.warning("Чужой запрос от chat_id=%s проигнорирован", message.chat.id)
            return
        return await handler(message, *args, **kwargs)
    return wrapper


@router.message(Command("start", "help"))
@owned
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🤖 <b>AIMAG AI Analyst</b> — виртуальный трейдер-аналитик.\n\n"
        "/report — собрать данные и провести полный анализ\n"
        "/balance — состояние виртуального счёта\n"
        "/positions — открытые позиции с текущим PnL\n"
        "/close &lt;id&gt; — закрыть позицию по рынку\n"
        "/rejected — что и почему отклонил цензор\n"
        "/reset confirm — сброс песочницы к депозиту\n\n"
        "Утренний брифинг приходит автоматически.", parse_mode="HTML")


@router.message(Command("report"))
@owned
async def cmd_report(message: Message) -> None:
    if _analysis_lock.locked():
        await message.answer("⏳ Анализ уже выполняется, подожди.")
        return
    async with _analysis_lock:
        await message.answer("🔄 Собираю макро, крипту, новости и гоняю анализ — 1-2 минуты...")
        try:
            res = await asyncio.to_thread(run_full_analysis, repo)
            await message.answer(notifier.format_briefing(res), parse_mode="HTML")
        except Exception as e:  # noqa: BLE001 — пользователь должен увидеть сбой
            log.exception("Сбой /report")
            await message.answer(f"❌ Анализ упал: {type(e).__name__}: {e}")


@router.message(Command("balance"))
@owned
async def cmd_balance(message: Message) -> None:
    summary = await asyncio.to_thread(repo.get_equity_summary)
    await message.answer(notifier.format_balance(summary), parse_mode="HTML")


@router.message(Command("positions"))
@owned
async def cmd_positions(message: Message) -> None:
    trades = await asyncio.to_thread(repo.list_open_trades)
    prices: dict[str, float] = {}
    for symbol in {t.symbol for t in trades}:
        try:
            prices[symbol] = await asyncio.to_thread(fetch_last_price, symbol)
        except Exception as e:  # noqa: BLE001
            log.warning("Цена %s недоступна: %s", symbol, e)
    await message.answer(notifier.format_positions(trades, prices), parse_mode="HTML")


@router.message(Command("close"))
@owned
async def cmd_close(message: Message, command: CommandObject) -> None:
    if not command.args or not command.args.strip().isdigit():
        await message.answer("Формат: /close 3  (id позиции из /positions)")
        return
    trade_id = int(command.args.strip())
    try:
        trade = await asyncio.to_thread(repo.get_trade, trade_id)
        price = await asyncio.to_thread(fetch_last_price, trade.symbol)
        closed = await asyncio.to_thread(repo.close_trade, trade_id, price, "MANUAL")
        await message.answer(notifier.format_trade_closed(closed), parse_mode="HTML")
    except (ValueError, ConnectionError) as e:
        await message.answer(f"❌ {e}")


@router.message(Command("rejected"))
@owned
async def cmd_rejected(message: Message) -> None:
    rows = await asyncio.to_thread(repo.rejected_signals_stats)
    if not rows:
        await message.answer("Цензор пока ничего не отклонял.")
        return
    lines = ["🛡 <b>Топ причин отказа цензора:</b>", ""]
    for r in rows[:10]:
        lines.append(f"• ({r['cnt']}) {r['reject_reason'][:150]}")
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("reset"))
@owned
async def cmd_reset(message: Message, command: CommandObject) -> None:
    if (command.args or "").strip().lower() != "confirm":
        await message.answer("⚠️ Сброс удалит ВСЕ сделки и логи.\n"
                             "Для подтверждения: /reset confirm")
        return
    await asyncio.to_thread(repo.reset_account)
    await message.answer("♻️ Песочница сброшена к начальному депозиту.")
