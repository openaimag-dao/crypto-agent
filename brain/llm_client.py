"""
МОДУЛЬ 3d. Клиент LLM (Anthropic Messages API) + парсер строгого JSON.

Ключ берётся из переменной окружения ANTHROPIC_API_KEY (.env).
Парсер терпим к типичным огрехам модели: markdown-ограда ```json,
текст до/после объекта — вырезаем и парсим только JSON.
"""

from __future__ import annotations

import json
import os
import time

import requests

from brain.prompt_builder import SYSTEM_PROMPT
from utils.logger import get_logger

log = get_logger("brain.llm")

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = 2500
LLM_RETRIES = 2
LLM_TIMEOUT = 90


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    """Достаёт первый JSON-объект из текста (срезает ```-ограды и преамбулы)."""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"В ответе LLM нет JSON-объекта: {text[:200]!r}")
    try:
        return json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON не парсится: {e}; фрагмент: {cleaned[start:start+200]!r}") from e


def request_analysis(prompt: str) -> dict:
    """Один вызов LLM -> распарсенный dict по схеме из prompt_builder."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise LLMError("ANTHROPIC_API_KEY не задан в окружении (.env)")

    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    last_err = ""
    for attempt in range(1, LLM_RETRIES + 1):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=LLM_TIMEOUT)
            if resp.status_code == 429:
                wait = int(resp.headers.get("retry-after", 15))
                log.warning("LLM rate limit, пауза %d c", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", [])
                           if b.get("type") == "text")
            result = _extract_json(text)
            usage = data.get("usage", {})
            log.info("LLM ответил: in=%s out=%s токенов, идей: %d",
                     usage.get("input_tokens"), usage.get("output_tokens"),
                     len(result.get("trade_ideas", [])))
            return result
        except (requests.RequestException, LLMError) as e:
            last_err = str(e)
            log.warning("LLM попытка %d/%d: %s", attempt, LLM_RETRIES, e)
            if attempt < LLM_RETRIES:
                time.sleep(5)

    raise LLMError(f"LLM недоступен/невалиден после {LLM_RETRIES} попыток: {last_err}")
