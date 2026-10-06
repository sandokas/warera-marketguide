"""Final integration on isolated DBs: real offline CLI, browser PNGs and verifier."""
import csv
import json
from pathlib import Path
import runpy
import sys

import pytest

from warera_quant import cli
from warera_quant.market_data import load_entity_activity
from warera_quant.market_store import MarketStore
from warera_quant.warera_api import normalize_transaction
from test_report_context import C, EPS, ingest


@pytest.mark.parametrize("mode", ["window", "full-fifo"])
@pytest.mark.parametrize("historical", [False, True])
def test_offline_cli_browser_publication_context_and_complete_csvs(
        tmp_path, monkeypatch, mode, historical):
    database = tmp_path / "snapshot.db"
    output = tmp_path / "report"
    from datetime import timedelta

    def fact(id, at, code, money, **extra):
        return normalize_transaction({"_id": id, "createdAt": at.isoformat(),
            "transactionType": "trading", "itemCode": code, "money": money,
            "quantity": 1, "buyerId": "U", "sellerId": "V", **extra})

    with MarketStore(database) as store:
        ingest(store, fact("old-buy", C-timedelta(days=30), "bread", 5),
               fact("old-sale", C-timedelta(days=20), "bread", 6,
                    buyerId="V", sellerId="U"),
               fact("dominant", C-timedelta(days=1), "bread", 80),
               fact("small-a", C-EPS, "wood", 10),
               fact("small-b", C-EPS, "grain", 10),
               fact("institution", C-EPS, "bread", 10,
                    buyerId="actor", buyerMuId="M", sellerCountryId="K"),
               normalize_transaction({"_id": "newest-equipment", "createdAt": C.isoformat(),
                    "transactionType": "itemMarket", "itemCode": "helmet", "money": 10,
                    "quantity": 1, "buyerId": "U", "sellerId": "V",
                    "item": {"_id": "helmet-1", "code": "helmet"}}))

    monkeypatch.setattr(cli, "WarEraApiClient", lambda *a, **k: pytest.fail("offline network"))
    monkeypatch.setattr(cli, "sync_market_data", lambda *a, **k: pytest.fail("offline sync"))
    prepared = []
    workflow = cli.run_db_report_workflow

    def observe(*args, **kwargs):
        result = workflow(*args, **kwargs)
        prepared.append(result)
        return result

    monkeypatch.setattr(cli, "run_db_report_workflow", observe)
    flags = ["warera", "--from-db", "--quiet", "--market-db", str(database),
             "--output", str(output), "--participant-accounting", mode,
             "--output-layout", "direct"]
    if historical:
        flags += ["--as-of", C.isoformat()]
    monkeypatch.setattr(sys, "argv", flags)
    cli.main()
    context = prepared[0].context
    assert context.analysis_as_of == C
    assert context.window_end_exclusive == (C if historical else C + EPS)
    assert context.accounting_mode == mode
    report = prepared[0].participant_report
    assert report["accounting_status"] == ("not_calculated" if mode == "window" else "calculated")
    with MarketStore(database) as store:
        for kind, id in (("user", "U"), ("mu", "M"), ("country", "K")):
            expected = next(e for e in report["entities"]
                            if (e["entity_kind"], e["entity_id"]) == (kind, id))
            assert load_entity_activity(store, kind, id, context=context)["entities"] == [expected]
        mu = next(e for e in report["entities"] if e["entity_id"] == "M")
        assert mu["actor_ids"] == ["actor"]
        assert not any(e["entity_id"] == "actor" for e in report["entities"])
    user = next(e for e in report["entities"] if e["entity_id"] == "U")
    assert len(user["top_items"]) < len(user["item_categories"])
    assert sum(r["buy_total_value"] + r["sell_total_value"]
               for r in user["item_categories"]) == user["source_turnover"]
    with (output / "participant_item_breakdown_7d.csv").open(encoding="utf-8", newline="") as f:
        exported = [r for r in csv.DictReader(f) if r["entity_kind"] == "user" and r["entity_id"] == "U"]
    assert len(exported) == len(user["item_categories"])
    assert {r["id"] for r in prepared[0].equipment_details} == (set() if historical else {"newest-equipment"})
    (tmp_path / "publication.json").write_text(json.dumps({"database": str(database),
        "as_of": C.isoformat(), "context": context.to_dict(), "status": "isolated phase I fixture"}))
    monkeypatch.setattr(sys, "argv", ["verify", str(tmp_path)])
    runpy.run_path(str(Path(__file__).parents[1] / "scripts/verify_market_publication.py"))
    summary = json.loads((tmp_path / "reconciliation.json").read_text())
    assert summary["context"] == context.to_dict()
    assert summary["equipment_sales"] == (0 if historical else 1)
    assert summary["participant_table_crops"] == 6
    assets = json.loads((output / "asset_inventory.json").read_text())
    for asset in assets:
        if asset["kind"] == "table":
            table = asset["css_size"]["table"]
            assert all(abs(gap) <= table["rightEdgeTolerance"]
                       for gap in table["rightEdgeGaps"].values() if gap is not None)


def test_participant_numeric_header_text_fits_intrinsic_columns(tmp_path):
    from playwright.sync_api import sync_playwright
    from warera_quant.charts import _chrome_executable
    from warera_quant.report import write_outputs, export_report_assets
    from test_participant_report import participant_fixture, frame, NOW

    _, html = write_outputs(frame(), tmp_path, participant_report=participant_fixture(), as_of=NOW)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_chrome_executable(None), headless=True)
        page = browser.new_page()
        page.goto(html.as_uri())
        page.evaluate("document.fonts.ready")
        headers = page.locator('.participant-table th.number').evaluate_all("""cells => cells.map(cell => {
            const range = document.createRange(); range.selectNodeContents(cell);
            const text = range.getBoundingClientRect(), box = cell.getBoundingClientRect();
            return {label:cell.textContent, left:text.left, right:text.right,
                    cellLeft:box.left, cellRight:box.right};
        })""")
        browser.close()
    assert any(h['label'] == 'Window Avg Difference/Unit' for h in headers)
    assert all(h['left'] >= h['cellLeft'] - 1 and h['right'] <= h['cellRight'] + 1
               for h in headers), headers
    (tmp_path / 'header-text-bounds.json').write_text(json.dumps(headers, indent=2))
    export_report_assets(html, tmp_path)
