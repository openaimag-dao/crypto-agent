"""
МОДУЛЬ 3c. Промпт-билдер: превращает данные Модуля 1 + индикаторы + новости
в один структурированный промпт для LLM.

LLM возвращает СТРОГИЙ JSON:
  - news_scores: оценка новостного фона -1..+1 и краткое резюме
  - analysis: связный текст для утреннего брифинга (рус.)
  - trade_ideas: список идей (или пустой), каждая с направлением/уровнями.
    Плечо и риск LLM только ПРЕДЛАГАЕТ — окончательное слово за validator.py.
"""

from __future__ import annotations

import json

RESPONSE_SCHEMA = """{
  "news_score": <число от -1.0 до 1.0, тональность новостного фона>,
  "news_summary": "<2-3 предложения: главное из новостей, по-русски>",
  "macro_comment": "<1-2 предложения: что говорит макро-фон, по-русски>",
  "analysis": "<связный аналитический вывод для брифинга, 4-6 предложений, по-русски>",
  "trade_ideas": [
    {
      "symbol": "<BTCUSDT|ETHUSDT|SOLUSDT>",
      "direction": "<LONG|SHORT>",
      "confidence": <0.0-1.0>,
      "entry_price": <число, близко к текущей цене>,
      "stop_loss": <число>,
      "take_profit": <число>,
      "leverage": <3 или 5>,
      "risk_pct": <от 2.0 до 5.0>,
      "rationale": "<краткое обоснование, 1-2 предложения>"
    }
  ]
}"""

SYSTEM_PROMPT = (
    "Ты — дисциплинированный крипто-аналитик. Анализируешь ТОЛЬКО переданные данные, "
    "ничего не выдумываешь. Работаешь в виртуальной песочнице (не реальные деньги). "
    "Жёсткие правила: плечо только 3 или 5; риск на сделку 2-5%; стоп-лосс обязателен; "
    "если явного сетапа нет — trade_ideas оставляй пустым списком, это нормальный "
    "и частый исход. Отвечай ТОЛЬКО валидным JSON по заданной схеме, без markdown, "
    "без пояснений вне JSON."
)


def build_analysis_prompt(
    macro: dict,
    crypto: dict,
    tech: list[dict],
    news_lines: list[str],
    derivatives_note: str | None = None,
) -> str:
    """
    macro   — MacroSnapshot.to_dict()
    crypto  — CryptoSnapshot.to_dict() (цены/funding; свечи не шлём — есть индикаторы)
    tech    — [TechSummary.to_dict(), ...]
    news_lines — NewsSnapshot.to_prompt_lines()
    """
    crypto_slim = {
        "timestamp": crypto.get("timestamp"),
        "tickers": [
            {k: v for k, v in t.items() if k != "candles_4h_tail"}
            for t in crypto.get("tickers", [])
        ],
        "failed": crypto.get("failed", []),
    }

    parts = [
        "=== МАКРО-ИНДЕКСЫ ===",
        json.dumps(macro, ensure_ascii=False, indent=1),
        "",
        "=== КРИПТА (цены, funding) ===",
        json.dumps(crypto_slim, ensure_ascii=False, indent=1),
        "",
        "=== ТЕХНИЧЕСКИЕ ИНДИКАТОРЫ (рассчитаны кодом, доверяй цифрам) ===",
        json.dumps(tech, ensure_ascii=False, indent=1),
        "",
        "=== НОВОСТНЫЕ ЗАГОЛОВКИ (последние) ===",
        "\n".join(news_lines) if news_lines else "(новости недоступны)",
    ]
    if derivatives_note:
        parts += ["", "=== ДЕРИВАТИВЫ ===", derivatives_note]

    parts += [
        "",
        "Проанализируй данные и верни JSON строго по схеме:",
        RESPONSE_SCHEMA,
    ]
    return "\n".join(parts)
