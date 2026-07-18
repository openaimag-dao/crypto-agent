"""
МОДУЛЬ 2b. Репозиторий: единственная точка доступа к SQLite.

Правила:
  - Все операции атомарны (context manager = транзакция).
  - Баланс меняется ТОЛЬКО внутри open_trade / close_trade — вручную его
    не трогает никто, кроме reset_account().
  - Ошибочные вызовы (закрыть закрытую сделку, уйти в минус) бросают исключения,
    а не молча портят данные.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from database.schema import SCHEMA_SQL
from utils.logger import get_logger

log = get_logger("database.repository")

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "trading.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@dataclass
class Trade:
    id: int
    symbol: str
    direction: str
    leverage: int
    entry_price: float
    stop_loss: float
    take_profit: float
    margin_usdt: float
    qty: float
    risk_pct: float
    status: str
    opened_at: str
    closed_at: str | None = None
    close_price: float | None = None
    close_reason: str | None = None
    pnl_usdt: float | None = None
    llm_rationale: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Trade":
        return cls(**{k: row[k] for k in row.keys()})


class Repository:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    # ---------- инициализация ----------

    def init_db(self, initial_deposit: float = 10_000.0) -> None:
        """Создаёт схему; если аккаунта нет — заводит с начальным депозитом."""
        with _connect(self.db_path) as conn:
            conn.executescript(SCHEMA_SQL)
            row = conn.execute("SELECT id FROM account WHERE id = 1").fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO account (id, balance_usdt, initial_deposit, updated_at) "
                    "VALUES (1, ?, ?, ?)",
                    (initial_deposit, initial_deposit, _now()),
                )
                log.info("Аккаунт создан, депозит %.2f USDT", initial_deposit)

    def reset_account(self, deposit: float = 10_000.0) -> None:
        """Полный сброс песочницы: баланс к депозиту, сделки и логи очищаются."""
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM signals_log")
            conn.execute("DELETE FROM trades")
            conn.execute(
                "UPDATE account SET balance_usdt = ?, initial_deposit = ?, updated_at = ? WHERE id = 1",
                (deposit, deposit, _now()),
            )
        log.warning("ПЕСОЧНИЦА СБРОШЕНА. Новый депозит: %.2f USDT", deposit)

    # ---------- баланс ----------

    def get_balance(self) -> float:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT balance_usdt FROM account WHERE id = 1").fetchone()
            if row is None:
                raise RuntimeError("Аккаунт не инициализирован — вызови init_db()")
            return float(row["balance_usdt"])

    def get_equity_summary(self) -> dict:
        """Сводка для команды /balance в Telegram."""
        with _connect(self.db_path) as conn:
            acc = conn.execute(
                "SELECT balance_usdt, initial_deposit FROM account WHERE id = 1"
            ).fetchone()
            stats = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) AS wins, "
                "COALESCE(SUM(pnl_usdt), 0) AS total_pnl "
                "FROM trades WHERE status = 'CLOSED'"
            ).fetchone()
            open_margin = conn.execute(
                "SELECT COALESCE(SUM(margin_usdt), 0) AS m FROM trades WHERE status = 'OPEN'"
            ).fetchone()

        balance = float(acc["balance_usdt"])
        deposit = float(acc["initial_deposit"])
        total_closed = int(stats["total"] or 0)
        wins = int(stats["wins"] or 0)
        return {
            "balance_usdt": round(balance, 2),
            "in_positions_usdt": round(float(open_margin["m"]), 2),
            "initial_deposit": deposit,
            "total_return_pct": round((balance + float(open_margin["m"])) / deposit * 100 - 100, 2),
            "closed_trades": total_closed,
            "winrate_pct": round(wins / total_closed * 100, 1) if total_closed else 0.0,
            "total_pnl_usdt": round(float(stats["total_pnl"]), 2),
        }

    # ---------- сделки ----------

    def open_trade(
        self,
        *,
        symbol: str,
        direction: str,
        leverage: int,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        margin_usdt: float,
        risk_pct: float,
        llm_rationale: str | None = None,
    ) -> Trade:
        """
        Открывает виртуальную позицию и списывает маржу с баланса. Атомарно.
        ВАЖНО: вызывается только после validator'а. CHECK-ограничения схемы —
        второй эшелон защиты (плечо не 3/5 или риск > 5% упадут на уровне БД).
        """
        qty = margin_usdt * leverage / entry_price
        with _connect(self.db_path) as conn:
            bal = conn.execute(
                "SELECT balance_usdt FROM account WHERE id = 1"
            ).fetchone()
            balance = float(bal["balance_usdt"])
            if margin_usdt > balance:
                raise ValueError(
                    f"Недостаточно средств: маржа {margin_usdt:.2f} > баланс {balance:.2f}"
                )

            cur = conn.execute(
                "INSERT INTO trades (symbol, direction, leverage, entry_price, stop_loss, "
                "take_profit, margin_usdt, qty, risk_pct, status, opened_at, llm_rationale) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)",
                (symbol, direction, leverage, entry_price, stop_loss, take_profit,
                 margin_usdt, qty, risk_pct, _now(), llm_rationale),
            )
            conn.execute(
                "UPDATE account SET balance_usdt = balance_usdt - ?, updated_at = ? WHERE id = 1",
                (margin_usdt, _now()),
            )
            trade_id = cur.lastrowid

        trade = self.get_trade(trade_id)
        log.info("Открыта сделка #%d %s %s x%d, маржа %.2f, вход %.4f",
                 trade.id, trade.symbol, trade.direction, trade.leverage,
                 trade.margin_usdt, trade.entry_price)
        return trade

    def close_trade(self, trade_id: int, close_price: float, reason: str) -> Trade:
        """
        Закрывает позицию, считает PnL, возвращает маржу + PnL на баланс.
        PnL (линейный USDT-M фьючерс):
            LONG:  qty * (close - entry)
            SHORT: qty * (entry - close)
        Убыток ограничен маржой (виртуальная "ликвидация" — хуже минус-маржи не бывает).
        """
        if close_price <= 0:
            raise ValueError(f"Некорректная цена закрытия: {close_price}")

        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            if row is None:
                raise ValueError(f"Сделка #{trade_id} не найдена")
            if row["status"] != "OPEN":
                raise ValueError(f"Сделка #{trade_id} уже закрыта")

            qty = float(row["qty"])
            entry = float(row["entry_price"])
            margin = float(row["margin_usdt"])

            raw_pnl = qty * (close_price - entry) if row["direction"] == "LONG" \
                else qty * (entry - close_price)
            pnl = max(raw_pnl, -margin)          # нельзя потерять больше маржи
            if raw_pnl < -margin:
                reason = "LIQUIDATION"

            conn.execute(
                "UPDATE trades SET status = 'CLOSED', closed_at = ?, close_price = ?, "
                "close_reason = ?, pnl_usdt = ? WHERE id = ?",
                (_now(), close_price, reason, round(pnl, 2), trade_id),
            )
            conn.execute(
                "UPDATE account SET balance_usdt = balance_usdt + ?, updated_at = ? WHERE id = 1",
                (margin + pnl, _now()),
            )

        trade = self.get_trade(trade_id)
        log.info("Закрыта сделка #%d по %.4f (%s), PnL %.2f USDT",
                 trade.id, close_price, reason, trade.pnl_usdt)
        return trade

    def get_trade(self, trade_id: int) -> Trade:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
            if row is None:
                raise ValueError(f"Сделка #{trade_id} не найдена")
            return Trade.from_row(row)

    def list_open_trades(self) -> list[Trade]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY opened_at"
            ).fetchall()
            return [Trade.from_row(r) for r in rows]

    def list_recent_closed(self, limit: int = 10) -> list[Trade]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [Trade.from_row(r) for r in rows]

    # ---------- журнал сигналов ----------

    def log_signal(
        self,
        *,
        raw_response: dict | str,
        verdict: str,
        symbol: str | None = None,
        reject_reason: str | None = None,
        trade_id: int | None = None,
    ) -> int:
        """Пишет КАЖДЫЙ ответ LLM: принят, отклонён или "сделки нет"."""
        raw = raw_response if isinstance(raw_response, str) \
            else json.dumps(raw_response, ensure_ascii=False)
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO signals_log (created_at, symbol, raw_response, verdict, "
                "reject_reason, trade_id) VALUES (?, ?, ?, ?, ?, ?)",
                (_now(), symbol, raw, verdict, reject_reason, trade_id),
            )
            return cur.lastrowid

    def rejected_signals_stats(self) -> list[sqlite3.Row]:
        """Топ причин отказа цензора — покажет, что чаще всего 'хочет' LLM."""
        with _connect(self.db_path) as conn:
            return conn.execute(
                "SELECT reject_reason, COUNT(*) AS cnt FROM signals_log "
                "WHERE verdict = 'REJECTED' GROUP BY reject_reason ORDER BY cnt DESC"
            ).fetchall()
