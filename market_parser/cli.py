from __future__ import annotations

import argparse
import json
from datetime import date

from .config import Settings
from .exporter import export_monthly_xlsx, last_n_days_range
from .runner import available_store_slugs, run_and_export, run_async, sync_google_last_days
from .storage import Storage
from .stores import list_stores


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="market-parser")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-db", help="Create or migrate the SQLite database")
    subparsers.add_parser("stores", help="List configured stores")

    run_parser = subparsers.add_parser("run", help="Collect prices and export outputs")
    run_parser.add_argument("--stores", help="Comma-separated store slugs")
    run_parser.add_argument("--limit", type=int, help="Max products per store")
    run_parser.add_argument("--dry-run", action="store_true", help="Do not write DB/XLSX/Google")
    run_parser.add_argument("--no-xlsx", action="store_true", help="Skip XLSX export")
    run_parser.add_argument("--no-google", action="store_true", help="Skip Google Sheets sync")

    export_parser = subparsers.add_parser("export-xlsx", help="Export one monthly XLSX")
    export_parser.add_argument("--month", default=date.today().strftime("%Y-%m"), help="YYYY-MM")

    google_parser = subparsers.add_parser("sync-google", help="Sync Google Sheets last N days")
    google_parser.add_argument("--days", type=int, default=30)

    runs_parser = subparsers.add_parser("runs", help="Show recent run summaries")
    runs_parser.add_argument("--limit", type=int, default=10)

    args = parser.parse_args(argv)
    settings = Settings()

    if args.command == "init-db":
        Storage(settings.db_path).init_db()
        print(f"Initialized database: {settings.db_path}")
        return

    if args.command == "stores":
        for slug, name in list_stores():
            print(f"{slug}\t{name}")
        return

    if args.command == "run":
        selected = _parse_stores(args.stores)
        results, xlsx_path, google_status = run_async(
            run_and_export(
                settings,
                store_slugs=selected,
                limit=args.limit,
                dry_run=args.dry_run,
                export_xlsx=not args.no_xlsx,
                sync_google=not args.no_google,
            )
        )
        for result in results:
            line = f"{result.store_slug}: {result.status}, {result.count} products"
            if result.error:
                line += f", error={result.error}"
            print(line)
        if xlsx_path:
            print(f"XLSX: {xlsx_path}")
        if google_status:
            print(f"Google Sheets: {google_status}")
        return

    if args.command == "export-xlsx":
        path = export_monthly_xlsx(Storage(settings.db_path), settings.export_dir, args.month)
        print(f"XLSX: {path}")
        return

    if args.command == "sync-google":
        start_date, end_date = last_n_days_range(args.days)
        sync_google_last_days(settings, days=args.days)
        print(f"Google Sheets synced: {settings.google_sheet_name} ({start_date}..{end_date})")
        return

    if args.command == "runs":
        rows = Storage(settings.db_path).fetch_latest_runs(limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return


def _parse_stores(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    stores = [item.strip() for item in raw.split(",") if item.strip()]
    unknown = sorted(set(stores) - set(available_store_slugs()))
    if unknown:
        raise SystemExit(f"Unknown stores: {', '.join(unknown)}")
    return stores
