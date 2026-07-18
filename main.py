"""
Точка входа: Telegram-бот (aiogram) + планировщик (APScheduler) в одном loop.

Задачи по расписанию:
  - morning_briefing: ежедневно в BRIEFING_HOUR:BRIEFING_MINUTE (Asia/Almaty) —
    полный анализ + брифинг в чат владельца.
  - position_monitor: каждые MONITOR_INTERVAL_MIN минут — проверка SL/TP,
    уведомление о каждом закрытии.

Запуск: python main.py  (перед этим заполнить .env по образцу .env.example)
"""

from __future__ import annotations

import asyncio
import os

from aiogram import Bot, Dispatcher
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from bot import notifier
from bot.handlers import router, repo, _analysis_lock
from services.pipeline import monitor_open_positions, run_full_analysis
from utils.logger import get_logger

load_dotenv()
log = get_logger("main")

TIMEZONE = os.getenv("TIMEZONE", "Asia/Almaty")
BRIEFING_HOUR = int(os.getenv("BRIEFING_HOUR", "8"))
BRIEFING_MINUTE = int(os.getenv("BRIEFING_MINUTE", "0"))
MONITOR_INTERVAL_MIN = int(os.getenv("MONITOR_INTERVAL_MIN", "5"))


async def morning_briefing(bot: Bot, chat_id: int) -> None:
    if _analysis_lock.locked():
        log.warning("Брифинг пропущен: анализ уже идёт")
        return
    async with _analysis_lock:
        log.info("Плановый утренний брифинг...")
        try:
            res = await asyncio.to_thread(run_full_analysis, repo)
            await bot.send_message(chat_id, notifier.format_briefing(res),
                                   parse_mode="HTML")
        except Exception:  # noqa: BLE001 — плановая задача не должна ронять процесс
            log.exception("Сбой утреннего брифинга")
            await bot.send_message(chat_id, "❌ Утренний брифинг упал, детали в логах.")


async def position_monitor(bot: Bot, chat_id: int) -> None:
    try:
        closed = await asyncio.to_thread(monitor_open_positions, repo)
        for trade in closed:
            await bot.send_message(chat_id, notifier.format_trade_closed(trade),
                                   parse_mode="HTML")
    except Exception:  # noqa: BLE001
        log.exception("Сбой мониторинга позиций")


async def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
    if not token or not chat_id:
        raise SystemExit("Заполни TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env "
                         "(образец — .env.example)")

    repo.init_db(initial_deposit=float(os.getenv("INITIAL_DEPOSIT", "10000")))

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        morning_briefing, CronTrigger(hour=BRIEFING_HOUR, minute=BRIEFING_MINUTE),
        args=(bot, chat_id), id="briefing",
        misfire_grace_time=600,
    )
    scheduler.add_job(
        position_monitor, IntervalTrigger(minutes=MONITOR_INTERVAL_MIN),
        args=(bot, chat_id), id="monitor",
        max_instances=1, coalesce=True,
    )
    scheduler.start()

    log.info("Бот запущен. Брифинг %02d:%02d %s, мониторинг каждые %d мин.",
             BRIEFING_HOUR, BRIEFING_MINUTE, TIMEZONE, MONITOR_INTERVAL_MIN)
    await bot.send_message(chat_id, "🚀 AIMAG AI Analyst запущен. /help — команды.")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
