"""Отправка результатов дневного прогона в Telegram через Bot API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .config import Settings
from .models import StoreRunResult

API_BASE = "https://api.telegram.org"
# Telegram limits: message text 4096, document caption 1024.
MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024
ERROR_SNIPPET_LIMIT = 120


class TelegramError(RuntimeError):
    pass


def _call(token: str, method: str, *, data: dict | None = None, files: dict | None = None) -> Any:
    url = f"{API_BASE}/bot{token}/{method}"
    response = httpx.post(url, data=data, files=files, timeout=120.0)
    payload = response.json()
    if not payload.get("ok"):
        raise TelegramError(f"Telegram {method}: {payload.get('description', response.text)}")
    return payload["result"]


def _require_telegram(settings: Settings) -> tuple[str, str]:
    if not settings.telegram_configured:
        raise TelegramError(
            "Telegram is not configured: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        )
    return settings.telegram_bot_token or "", settings.telegram_chat_id or ""


def send_message(settings: Settings, text: str) -> None:
    token, chat_id = _require_telegram(settings)
    _call(token, "sendMessage", data={"chat_id": chat_id, "text": text[:MESSAGE_LIMIT]})


def send_document(settings: Settings, path: str | Path, caption: str | None = None) -> None:
    token, chat_id = _require_telegram(settings)
    file_path = Path(path)
    data: dict = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption[:CAPTION_LIMIT]
    with file_path.open("rb") as fh:
        _call(token, "sendDocument", data=data, files={"document": (file_path.name, fh)})


def list_chat_ids(settings: Settings) -> list[dict]:
    """Чаты из недавних апдейтов бота — чтобы узнать свой TELEGRAM_CHAT_ID."""
    if not settings.telegram_bot_token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    updates = _call(settings.telegram_bot_token, "getUpdates")
    chats: dict[int, dict] = {}
    for update in updates:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat")
        if chat and "id" in chat:
            title = chat.get("title") or " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            ) or chat.get("username") or ""
            chats[chat["id"]] = {"id": chat["id"], "type": chat.get("type", ""), "title": title}
    return list(chats.values())


def build_run_summary(
    results: list[StoreRunResult],
    xlsx_path: str | None,
    google_status: str | None,
    run_date: str,
) -> str:
    lines = [f"📊 Цены на детское питание — {run_date}", ""]
    ok = [r for r in results if r.status == "ok"]
    failed = [r for r in results if r.status != "ok"]
    for result in sorted(ok, key=lambda r: r.store_slug):
        lines.append(f"✅ {result.store_name}: {result.count}")
    for result in sorted(failed, key=lambda r: r.store_slug):
        error = (result.error or result.status)[:ERROR_SNIPPET_LIMIT]
        lines.append(f"❌ {result.store_name}: {error}")
    total = sum(r.count for r in ok)
    lines.append("")
    lines.append(f"Итого: {len(ok)}/{len(results)} магазинов, {total} товаров")
    if xlsx_path is None:
        lines.append("⚠️ XLSX-файл не сформирован")
    if google_status and google_status != "synced" and not google_status.startswith("skipped"):
        lines.append(f"⚠️ Google Sheets: {google_status[:ERROR_SNIPPET_LIMIT]}")
    return "\n".join(lines)[:MESSAGE_LIMIT]


def send_run_report(
    settings: Settings,
    results: list[StoreRunResult],
    xlsx_path: str | None,
    google_status: str | None,
    run_date: str,
) -> None:
    """Сводка отдельным сообщением + XLSX-файл следом (если он есть)."""
    send_message(settings, build_run_summary(results, xlsx_path, google_status, run_date))
    if xlsx_path:
        send_document(settings, xlsx_path, caption=f"Выгрузка цен на {run_date}")
