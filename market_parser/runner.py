from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Settings
from .exporter import build_price_matrix, export_monthly_xlsx, last_n_days_range
from .google_sheets import GoogleSheetConfig, GoogleSheetsExporter
from .models import ProductPrice, StoreRunResult
from .storage import Storage
from .stores import STORE_ADAPTERS, create_adapter

DEFAULT_STORES = list(STORE_ADAPTERS)


async def collect_prices(
    settings: Settings,
    *,
    store_slugs: Iterable[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> tuple[list[StoreRunResult], list[ProductPrice]]:
    selected = list(store_slugs or DEFAULT_STORES)
    storage = Storage(settings.db_path)
    run_id = None if dry_run else storage.start_run(selected)
    results: list[StoreRunResult] = []
    all_products: list[ProductPrice] = []
    errors: list[dict] = []
    last_network_store_seen = False

    for index, slug in enumerate(selected):
        adapter = create_adapter(slug, settings)
        if index and last_network_store_seen and adapter.requires_network:
            await asyncio.sleep(settings.store_delay_seconds)
        started = time.perf_counter()
        try:
            products = await adapter.fetch_category(limit=limit)
            duration_seconds = time.perf_counter() - started
            if adapter.requires_network and not products:
                raise RuntimeError(f"{adapter.metadata.name}: no products parsed")
            collected_at = local_now(settings)
            products = [replace(product, collected_at=collected_at) for product in products]
            all_products.extend(products)
            if not dry_run and run_id is not None:
                storage.save_prices(products, run_id=run_id, replace_store_day=True)
            results.append(
                StoreRunResult(
                    store_slug=slug,
                    store_name=adapter.metadata.name,
                    count=len(products),
                    status="ok",
                    duration_seconds=duration_seconds,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one store must not fail the whole run.
            duration_seconds = time.perf_counter() - started
            message = str(exc) or repr(exc)
            errors.append({"store": slug, "error": message, "duration_seconds": duration_seconds})
            results.append(
                StoreRunResult(
                    store_slug=slug,
                    store_name=adapter.metadata.name,
                    count=0,
                    status="error",
                    error=message,
                    duration_seconds=duration_seconds,
                )
            )
        if adapter.requires_network:
            last_network_store_seen = True

    if not dry_run and run_id is not None:
        status = "ok" if not errors else "partial"
        storage.finish_run(
            run_id,
            status=status,
            total_products=sum(result.count for result in results),
            errors=errors,
        )

    return results, all_products


def export_current_month(settings: Settings, today: date | None = None) -> str:
    target = today or local_now(settings).date()
    month = target.strftime("%Y-%m")
    path = export_monthly_xlsx(Storage(settings.db_path), settings.export_dir, month)
    return str(path)


def sync_google_last_days(settings: Settings, days: int = 30) -> None:
    if not settings.google_configured:
        raise RuntimeError("Google Sheets is not configured")
    start_date, end_date = last_n_days_range(days)
    matrix = build_price_matrix(Storage(settings.db_path), start_date, end_date)
    exporter = GoogleSheetsExporter(
        GoogleSheetConfig(
            spreadsheet_id=settings.google_sheet_id or "",
            sheet_name=settings.google_sheet_name,
            service_account_file=(
                str(settings.google_service_account_file)
                if settings.google_service_account_file
                else None
            ),
            service_account_json=settings.google_service_account_json,
        )
    )
    exporter.sync_matrix(matrix)


async def run_and_export(
    settings: Settings,
    *,
    store_slugs: Iterable[str] | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    export_xlsx: bool = True,
    sync_google: bool = True,
) -> tuple[list[StoreRunResult], str | None, str | None]:
    results, _ = await collect_prices(
        settings,
        store_slugs=store_slugs,
        limit=limit,
        dry_run=dry_run,
    )
    xlsx_path = None
    google_status = None
    if not dry_run and export_xlsx:
        xlsx_path = export_current_month(settings)
    if not dry_run and sync_google:
        if settings.google_configured:
            sync_google_last_days(settings, days=30)
            google_status = "synced"
        else:
            google_status = "skipped: Google Sheets is not configured"
    return results, xlsx_path, google_status


def run_async(coro):
    return asyncio.run(coro)


def available_store_slugs() -> list[str]:
    return sorted(STORE_ADAPTERS)


def auto_store_slugs() -> list[str]:
    """Stores that can run unattended (excludes CDP/manual-only ones)."""
    return [
        slug
        for slug in sorted(STORE_ADAPTERS)
        if getattr(STORE_ADAPTERS[slug], "auto_runnable", True)
    ]


def local_now(settings: Settings):
    from datetime import datetime

    try:
        tz = ZoneInfo(settings.timezone)
    except ZoneInfoNotFoundError:
        if settings.timezone == "Europe/Moscow":
            tz = timezone(timedelta(hours=3), name="Europe/Moscow")
        else:
            raise
    return datetime.now(tz=tz)
