"""
ai/client.py — асинхронный клиент OpenRouter (OpenAI-совместимый endpoint).

Ключевые свойства (по ТЗ — критично для стабильности на бесплатных моделях):
1. Fallback-цепочка моделей. Если основная модель ответила ошибкой/таймаутом/429
   или вернула невалидный JSON — пробуем следующие модели из .env.
2. Retry с экспоненциальной задержкой. Учитываем лимит 20 RPM: при 429 ждём дольше.
3. Защищённый парсинг JSON: бесплатные модели возвращают «грязный» ответ
   (обёртка ```json, текст до/после). Извлекаем JSON по фигурным скобкам/regex,
   пробуем json.loads; при неудаче делаем один уточняющий повтор «верни ТОЛЬКО
   валидный JSON»; если и это не помогло — кидаем AIError (вызывающий код решает,
   что делать — обычно строгий фолбэк/постановка в очередь на /retag).

Если OPENROUTER_API_KEY не задан — клиент сразу кидает AIError, не делая запросов
(бот при этом не падает, просто ИИ-функции недоступны).
"""

import asyncio
import json
import re
import logging

import httpx

import config

logger = logging.getLogger("ai.client")


class AIError(Exception):
    """ИИ недоступен или вернул нечитаемый ответ. Вызывающий код применяет фолбэк."""


def _models_chain() -> list[str]:
    """Основная модель + фолбэки, в порядке перебора."""
    chain = [config.OPENROUTER_MODEL] + list(config.OPENROUTER_FALLBACK_MODELS)
    # Убираем дубликаты, сохраняя порядок
    seen: set[str] = set()
    ordered: list[str] = []
    for m in chain:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def _extract_json(text: str) -> dict | list | None:
    """
    Достать JSON из «грязного» ответа модели.
    Стратегия:
    1. Убрать markdown-обёртку ```json ... ```.
    2. Попробовать json.loads целиком.
    3. Найти первый сбалансированный {...} или [...] и распарсить его.
    Возвращает объект или None, если ничего не получилось.
    """
    if not text:
        return None

    cleaned = text.strip()

    # 1. Снимаем ```json ... ``` или ``` ... ```
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    # 2. Прямая попытка
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Поиск сбалансированной скобочной структуры
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = cleaned.find(open_ch)
        if start == -1:
            continue
        depth = 0
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    candidate = cleaned[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        break  # пробуем следующий тип скобок
    return None


async def _raw_chat(model: str, messages: list[dict], temperature: float) -> str:
    """
    Один сырой запрос к одной модели. Возвращает текст ответа (content).
    Кидает httpx-исключения при сетевых проблемах/HTTP-ошибках — их ловит
    вышестоящая логика для перехода к следующей модели/повтору.
    """
    headers = {
        "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    # Необязательные, но рекомендованные OpenRouter заголовки
    if config.OPENROUTER_HTTP_REFERER:
        headers["HTTP-Referer"] = config.OPENROUTER_HTTP_REFERER
    if config.OPENROUTER_APP_TITLE:
        headers["X-Title"] = config.OPENROUTER_APP_TITLE

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    async with httpx.AsyncClient(timeout=config.AI_REQUEST_TIMEOUT) as client:
        resp = await client.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
        )
        # 429/5xx -> исключение, чтобы сработал retry/fallback
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


async def chat_text(system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
    """
    Получить ТЕКСТОВЫЙ ответ модели (для ИИ-описаний).
    Перебирает модели и делает retry с backoff. Кидает AIError, если все попытки
    провалились.
    """
    if not config.OPENROUTER_API_KEY:
        raise AIError("OPENROUTER_API_KEY не задан")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    for model in _models_chain():
        for attempt in range(config.AI_MAX_RETRIES):
            try:
                return (await _raw_chat(model, messages, temperature)).strip()
            except httpx.HTTPStatusError as e:
                last_error = e
                status = e.response.status_code
                # 429 (лимит) / 5xx — ждём и повторяем; 4xx прочие — сразу к следующей модели
                if status == 429:
                    await asyncio.sleep(_backoff(attempt, base=3.0))
                    continue
                if 500 <= status < 600:
                    await asyncio.sleep(_backoff(attempt))
                    continue
                break  # 400/401/404 — модель не подходит, пробуем следующую
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                await asyncio.sleep(_backoff(attempt))
                continue
        logger.warning("Модель %s не ответила, пробую следующую", model)

    raise AIError(f"Все модели недоступны: {last_error}")


async def chat_json(system_prompt: str, user_prompt: str,
                    temperature: float = 0.3) -> dict | list:
    """
    Получить JSON-ответ модели (для тегирования и анализа анкеты).
    Логика: перебор моделей + retry; защищённый парсинг; один уточняющий
    повтор «верни ТОЛЬКО валидный JSON» на модель. Кидает AIError при провале.
    """
    if not config.OPENROUTER_API_KEY:
        raise AIError("OPENROUTER_API_KEY не задан")

    base_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    for model in _models_chain():
        # Сначала обычный запрос; при невалидном JSON — один уточняющий повтор.
        for clarify in (False, True):
            messages = list(base_messages)
            if clarify:
                messages.append({
                    "role": "user",
                    "content": "Верни ТОЛЬКО валидный JSON без каких-либо пояснений, "
                               "без markdown-обёртки и без текста до или после.",
                })
            for attempt in range(config.AI_MAX_RETRIES):
                try:
                    raw = await _raw_chat(model, messages, temperature)
                    parsed = _extract_json(raw)
                    if parsed is not None:
                        return parsed
                    # JSON не извлёкся — выходим из retry, идём в clarify-итерацию
                    last_error = AIError("Не удалось извлечь JSON из ответа модели")
                    break
                except httpx.HTTPStatusError as e:
                    last_error = e
                    status = e.response.status_code
                    if status == 429:
                        await asyncio.sleep(_backoff(attempt, base=3.0))
                        continue
                    if 500 <= status < 600:
                        await asyncio.sleep(_backoff(attempt))
                        continue
                    break
                except (httpx.TimeoutException, httpx.TransportError) as e:
                    last_error = e
                    await asyncio.sleep(_backoff(attempt))
                    continue
        logger.warning("Модель %s не дала валидный JSON, пробую следующую", model)

    raise AIError(f"Не удалось получить валидный JSON: {last_error}")


def _backoff(attempt: int, base: float = 1.5) -> float:
    """Экспоненциальная задержка: base * 2^attempt (с разумным потолком)."""
    return min(base * (2 ** attempt), 30.0)
