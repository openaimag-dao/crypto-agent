"""
МОДУЛЬ 1c. Слежение за прибыльными кошельками Hyperliquid (публичный ончейн API).

Ключи НЕ нужны: POST https://api.hyperliquid.xyz/info с типами
  - clearinghouseState — текущие перп-позиции, плечо, стоимость счёта;
  - userFillsByTime    — исполненные сделки за окно (aggregateByTime склеивает
                         частичные исполнения одного ордера).

Список кошельков и пороги — в config.py (WHALE_WALLETS). Модуль только читает
данные и ничего не торгует: это контекст для владельца, не сигнал на вход.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import requests

from config import (
    HTTP_RETRIES,
    HTTP_RETRY_DELAY,
    HTTP_TIMEOUT,
    HYPERLIQUID_INFO_URL,
    WHALE_LOOKBACK_HOURS,
    WHALE_MIN_NOTIONAL_USD,
    WHALE_WALLETS,
)
from utils.logger import get_logger

log = get_logger("collectors.whales")

_session = requests.Session()
_session.headers.update({"User-Agent": "crypto-agent-sandbox/1.0",
                         "Content-Type": "application/json"})


@dataclass
class WhalePosition:
    coin: str
    direction: str          # LONG / SHORT
    notional_usd: float
    leverage: float
    entry_px: float
    unrealized_pnl: float


@dataclass
class CoinActivity:
    """Сделки кошелька по одной монете за окно, сжатые в итог."""
    coin: str
    opened_usd: float = 0.0       # объём открытий/доливок
    closed_usd: float = 0.0       # объём закрытий
    realized_pnl: float = 0.0
    actions: set[str] = field(default_factory=set)   # "Open Long", "Close Short", ...


@dataclass
class WhaleSnapshot:
    address: str
    label: str
    account_value: float = 0.0
    total_notional: float = 0.0
    positions: list[WhalePosition] = field(default_factory=list)
    activity: list[CoinActivity] = field(default_factory=list)
    realized_pnl_window: float = 0.0
    ok: bool = True
    error: str | None = None

    @property
    def gross_leverage(self) -> float:
        return self.total_notional / self.account_value if self.account_value > 0 else 0.0


def _post(payload: dict):
    last_err: Exception | None = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            r = _session.post(HYPERLIQUID_INFO_URL, json=payload, timeout=HTTP_TIMEOUT)
            if r.status_code == 429:
                raise requests.HTTPError("429 rate limit")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last_err = e
            log.warning("Hyperliquid %s, попытка %d/%d: %s",
                        payload.get("type"), attempt, HTTP_RETRIES, e)
            time.sleep(HTTP_RETRY_DELAY * attempt)
    raise RuntimeError(f"Hyperliquid недоступен: {last_err}")


# ---------- чистые функции разбора (тестируются офлайн) ----------

def parse_positions(state: dict) -> tuple[float, float, list[WhalePosition]]:
    ms = state.get("marginSummary", {})
    positions: list[WhalePosition] = []
    for ap in state.get("assetPositions", []):
        p = ap.get("position", {})
        szi = float(p.get("szi", 0))
        if szi == 0:
            continue
        positions.append(WhalePosition(
            coin=p["coin"],
            direction="LONG" if szi > 0 else "SHORT",
            notional_usd=float(p.get("positionValue", 0)),
            leverage=float(p.get("leverage", {}).get("value", 0)),
            entry_px=float(p.get("entryPx") or 0),
            unrealized_pnl=float(p.get("unrealizedPnl", 0)),
        ))
    positions.sort(key=lambda x: -x.notional_usd)
    return float(ms.get("accountValue", 0)), float(ms.get("totalNtlPos", 0)), positions


def summarize_fills(fills: list[dict]) -> tuple[list[CoinActivity], float]:
    """Склеивает сделки по монетам. Спот-сделки (coin вида '@123') пропускаем."""
    by_coin: dict[str, CoinActivity] = {}
    total_pnl = 0.0
    for f in fills:
        coin = f.get("coin", "")
        if coin.startswith("@"):
            continue
        notional = float(f.get("px", 0)) * float(f.get("sz", 0))
        pnl = float(f.get("closedPnl", 0)) - float(f.get("fee", 0))
        direction = f.get("dir", "")
        act = by_coin.setdefault(coin, CoinActivity(coin=coin))
        if direction.startswith("Open"):
            act.opened_usd += notional
        elif direction.startswith("Close") or "Liquidat" in direction:
            act.closed_usd += notional
        else:   # "Long > Short" и т.п. — переворот, считаем как оба
            act.opened_usd += notional / 2
            act.closed_usd += notional / 2
        act.realized_pnl += pnl
        act.actions.add(direction)
        total_pnl += pnl
    significant = [a for a in by_coin.values()
                   if a.opened_usd + a.closed_usd >= WHALE_MIN_NOTIONAL_USD]
    significant.sort(key=lambda a: -(a.opened_usd + a.closed_usd))
    return significant, total_pnl


def net_exposure(snapshots: list[WhaleSnapshot]) -> dict[str, dict]:
    """Суммарная позиция всех китов по монете: сколько в лонге/шорте и кто."""
    out: dict[str, dict] = {}
    for s in snapshots:
        for p in s.positions:
            if p.notional_usd < WHALE_MIN_NOTIONAL_USD:
                continue
            e = out.setdefault(p.coin, {"long": 0.0, "short": 0.0, "longs": 0, "shorts": 0})
            if p.direction == "LONG":
                e["long"] += p.notional_usd
                e["longs"] += 1
            else:
                e["short"] += p.notional_usd
                e["shorts"] += 1
    return dict(sorted(out.items(), key=lambda kv: -(kv[1]["long"] + kv[1]["short"])))


# ---------- сетевые функции ----------

def fetch_snapshot(address: str, label: str, lookback_hours: int = WHALE_LOOKBACK_HOURS) -> WhaleSnapshot:
    snap = WhaleSnapshot(address=address, label=label)
    try:
        state = _post({"type": "clearinghouseState", "user": address})
        snap.account_value, snap.total_notional, snap.positions = parse_positions(state)
        start_ms = int((time.time() - lookback_hours * 3600) * 1000)
        fills = _post({"type": "userFillsByTime", "user": address,
                       "startTime": start_ms, "aggregateByTime": True})
        snap.activity, snap.realized_pnl_window = summarize_fills(fills or [])
    except Exception as e:  # noqa: BLE001 — один кошелёк не должен ронять сводку
        snap.ok, snap.error = False, str(e)
        log.exception("Не удалось получить данные кошелька %s", address)
    return snap


def fetch_all(lookback_hours: int = WHALE_LOOKBACK_HOURS) -> list[WhaleSnapshot]:
    snaps = []
    for addr, label in WHALE_WALLETS.items():
        snaps.append(fetch_snapshot(addr, label, lookback_hours))
        time.sleep(0.3)   # бережём лимиты публичного API
    return snaps


if __name__ == "__main__":
    for s in fetch_all():
        print(f"{s.label}: счёт ${s.account_value:,.0f}, плечо {s.gross_leverage:.1f}x, "
              f"PnL за окно ${s.realized_pnl_window:,.0f}, сделок по монетам: {len(s.activity)}")
        for a in s.activity:
            print(f"   {a.coin}: открыто ${a.opened_usd:,.0f}, закрыто ${a.closed_usd:,.0f}, "
                  f"PnL ${a.realized_pnl:,.0f} {sorted(a.actions)}")
        for p in s.positions[:5]:
            print(f"   поз. {p.coin} {p.direction} ${p.notional_usd:,.0f} x{p.leverage:g}")
