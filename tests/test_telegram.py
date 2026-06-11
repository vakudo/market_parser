from market_parser.models import StoreRunResult
from market_parser.telegram import MESSAGE_LIMIT, build_run_summary


def _result(slug: str, *, count: int = 0, status: str = "ok", error: str | None = None):
    return StoreRunResult(
        store_slug=slug,
        store_name=slug.title(),
        count=count,
        status=status,
        error=error,
    )


def test_summary_lists_ok_and_failed_stores():
    results = [
        _result("ozon", count=120),
        _result("lenta", status="error", error="timeout after 2400s"),
    ]
    summary = build_run_summary(results, "exports/2026-06.xlsx", "synced", "12.06.2026")
    assert "12.06.2026" in summary
    assert "✅ Ozon: 120" in summary
    assert "❌ Lenta: timeout after 2400s" in summary
    assert "Итого: 1/2 магазинов, 120 товаров" in summary
    assert "⚠️" not in summary


def test_summary_flags_missing_xlsx_and_google_failure():
    summary = build_run_summary(
        [_result("ozon", count=5)], None, "failed: boom", "12.06.2026"
    )
    assert "⚠️ XLSX-файл не сформирован" in summary
    assert "⚠️ Google Sheets: failed: boom" in summary


def test_summary_truncates_long_errors_and_fits_limit():
    results = [
        _result(f"store{i}", status="error", error="x" * 5000) for i in range(30)
    ]
    summary = build_run_summary(results, None, None, "12.06.2026")
    assert len(summary) <= MESSAGE_LIMIT
