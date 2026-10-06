"""Phase C clock/boundary contracts on disposable DBs only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import sys

import pytest

from warera_quant import cli, market_data
from warera_quant.market_store import MarketStore
from warera_quant.report_context import ReportContext, resolve_context
from warera_quant.warera_api import normalize_transaction, TopOrders, OrderLevel

C = datetime(2026, 9, 23, 12, 18, 4, 123456, tzinfo=timezone.utc)
EPS = timedelta(microseconds=1)


def trade(id, at, *, equipment=False):
    source = {"_id": id, "createdAt": at.isoformat(), "transactionType": "itemMarket" if equipment else "trading",
              "itemCode": "helmet" if equipment else "bread", "money": 10, "quantity": 1,
              "buyerId": "u", "sellerId": "v"}
    if equipment:
        source["item"] = {"_id": id, "code": "helmet"}
    return normalize_transaction(source)


def ingest(store, *facts):
    assert store.ingest_transactions(facts, fetched_at=C + timedelta(days=60)).rejected == 0


@pytest.mark.parametrize("requested,logical,mode,end", [
    (None, C, "database-inclusive", C + EPS),
    (C, C, "historical-exclusive", C),
    (C - EPS, C - EPS, "historical-exclusive", C - EPS),
    (C + EPS, C, "database-inclusive", C + EPS),
])
def test_boundary_contract_and_serialization(requested, logical, mode, end):
    ctx = resolve_context(C, requested, generated_at=C + timedelta(days=50))
    assert ctx.analysis_as_of == logical and ctx.window_end_exclusive == end
    assert ctx.window_start() == logical - timedelta(days=7)
    assert ctx.boundary_mode == mode
    assert ReportContext.from_dict(json.loads(json.dumps(ctx.to_dict()))) == ctx
    offset = timezone(timedelta(hours=2))
    assert resolve_context(C.astimezone(offset), requested).data_as_of == C


def test_stale_generation_global_clock_and_reopen_freeze(tmp_path):
    path = tmp_path / "db"
    with MarketStore(path) as store:
        ingest(store, trade("new", C), trade("start", C - timedelta(days=7)),
               trade("before", C - timedelta(days=7) - EPS), trade("older", C - EPS))
        a = store.resolve_report_context(generated_at=C + timedelta(days=20))
        b = store.resolve_report_context(generated_at=C + timedelta(days=90))
        assert replace(a, generated_at=b.generated_at) == b
        assert market_data.load_market_rows(store, context=a) == market_data.load_market_rows(store, context=b)
        assert market_data.load_market_rows(store)[0]["trade_count_7d"] == 3
        ingest(store, trade("future", C + EPS))
    with MarketStore(path) as store:
        # Prohibit a re-resolution while using the frozen report context.
        store.resolve_report_context = lambda *a, **k: pytest.fail("cutoff re-resolved")
        assert len(market_data.load_chart_trades(store, item_code="bread", window="7D", context=a)) == 3
        assert market_data.load_market_rows(store, context=a)[0]["trade_count_7d"] == 3
        report = market_data.load_participant_report(store, context=a)
        assert report["coverage"]["market_transaction_count"] == 3
        assert report["as_of"] == C and report["window_start"] == C - timedelta(days=7)
        history = market_data.load_price_action_history(store, item_code="bread", window_days=7, context=a)
        assert history.window_end == C and history.coverage.last_observation_at == C
        assert len(history.trades) == 4  # Interval floor includes the earlier first-candle trade.


def test_global_observation_clock_and_transaction_only(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        ingest(store, trade("new", C))
        assert store.resolve_report_context().data_as_of == C
        store.insert_price_observations({"unknown": 2}, C + timedelta(days=1))
        assert market_data.load_market_rows(store)[0]["trend_path_90d_end_epoch"] == (C + timedelta(days=1)).timestamp()
        store.insert_order_book_observations({"equipmentOther": TopOrders([], [])}, C + timedelta(days=2))
        assert store.resolve_report_context().analysis_as_of == C + timedelta(days=2)


def test_empty_db_all_read_models_explicitly_unavailable(tmp_path, monkeypatch, capsys):
    with MarketStore(tmp_path / "db") as store:
        store.record_market_sync(C + timedelta(days=100), status="complete")
        ctx = store.resolve_report_context(C)
        assert ctx.analysis_as_of is None and ctx.window_start() is None
        assert market_data.load_market_rows(store) == []
        assert market_data.load_chart_trades(store, item_code="bread", window="7D") == []
        assert market_data.load_chart_data(store, item_code="bread", window="7D")["trades"] == []
        assert market_data.load_highlight_trade_history(store, item_codes=["bread"]) == {}
        assert market_data.load_price_action_history(store, item_code="bread").window_end is None
        assert market_data.load_participant_report(store)["method"] == "unavailable"
        assert list(market_data.iter_equipment_sale_details(store)) == []
        assert market_data.build_we24_market_index(store)["coverage_status"] == "unavailable"
        assert all(r.total_cost is None for r in market_data.load_action_cost_results(store))
    monkeypatch.setattr(sys, "argv", ["warera", "--from-db", "--quiet", "--market-db", str(tmp_path / "db"), "--output", str(tmp_path / "out")])
    cli.main()
    assert "Report unavailable" in capsys.readouterr().out
    output, = (tmp_path / "out").glob("report-*")
    assert json.loads((output / "report_context.json").read_text())["analysis_as_of"] is None
    assert not (output / "charts").exists()


def test_historical_future_and_equipment_subsecond_ties(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        for equipment in (False, True):
            ingest(store, *(trade(f"{equipment}-{tag}", at, equipment=equipment) for tag, at in (
                ("before", C - timedelta(days=7) - EPS), ("start", C - timedelta(days=7)),
                ("near", C - EPS), ("end-a", C), ("end-b", C))))
        default = store.resolve_report_context()
        historical = store.resolve_report_context(C)
        future = store.resolve_report_context(C + timedelta(days=500))
        assert [r["id"] for r in market_data.iter_equipment_sale_details(store, context=default)] == ["True-start", "True-near", "True-end-a", "True-end-b"]
        assert [r["id"] for r in market_data.iter_equipment_sale_details(store, context=historical)] == ["True-start", "True-near"]
        assert market_data.load_participant_report(store, context=default)["coverage"]["market_transaction_count"] == 8
        assert market_data.load_participant_report(store, context=historical)["coverage"]["market_transaction_count"] == 4
        assert market_data.load_market_rows(store, context=default)[0]["trade_count_7d"] == 4
        assert market_data.load_market_rows(store, context=historical)[0]["trade_count_7d"] == 2
        assert market_data.load_market_rows(store, context=future) == market_data.load_market_rows(store, context=default)


def test_observation_and_forecast_sql_upper_bounds(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        for delta in (-1, 0, 1):
            at = C + delta * EPS
            ingest(store, trade(str(delta), at))
            store.insert_price_observations({"bread": 10 + delta}, at)
            store.insert_order_book_observations({"bread": TopOrders([OrderLevel(9 + delta, 1)], [OrderLevel(11 + delta, 1)])}, at)
        ctx = resolve_context(C)
        end = ctx.window_end_exclusive
        assert len(store.transactions_for_window("bread", (C - EPS).timestamp(), end=end)) == 2
        assert len(store.price_observations_for_window("bread", (C - EPS).timestamp(), end=end)) == 2
        assert len(store.order_book_observations_for_window("bread", (C - EPS).timestamp(), end=end)) == 2
        observations = store.order_book_history_with_levels("bread", end=end)
        assert len(observations) == 2 and all(r["asks"] for r in observations)
        forecast = market_data.evaluate_item_forecast(store, item_code="bread", context=ctx)
        assert datetime.fromisoformat(forecast.current_observed_at.replace("Z", "+00:00")) == C
        historical = market_data.evaluate_item_forecast(store, item_code="bread", context=store.resolve_report_context(C))
        assert datetime.fromisoformat(historical.current_observed_at.replace("Z", "+00:00")) == C - EPS
        features = market_data.build_forecast_features(observations, store.transactions_for_window("bread", 0))
        assert [f["trailing_count"] for f in features] == [1, 2]


def test_legacy_null_precision_before_materialization(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        ingest(store, trade("a", C - EPS), trade("b", C), trade("future", C + EPS))
        # Disposable legacy fixture; no production database is accessed.
        store._connect().execute("update transactions set created_at_us=null")
        rows = store.transactions_for_window("bread", (C - EPS).timestamp(), end=C + EPS)
        assert [r["id"] for r in rows] == ["a", "b"]
        assert [r["id"] for r in store.transactions_for_period(["bread"], (C-EPS).timestamp(), (C+EPS).timestamp())] == ["a", "b"]


def test_we24_uses_completed_utc_day_at_midnight(tmp_path, monkeypatch):
    midnight = C.replace(hour=0, minute=0, second=0, microsecond=0)
    with MarketStore(tmp_path / "db") as store:
        ingest(store, trade("latest", C))
        calls = []
        monkeypatch.setattr(store, "completed_daily_facts", lambda codes, start, end: calls.append((start, end)) or [])
        for at in (midnight - EPS, midnight, midnight + EPS):
            market_data.build_we24_market_index(store, context=resolve_context(at))
        assert [end for _, end in calls] == [int((midnight-timedelta(days=1)).timestamp()), int(midnight.timestamp()), int(midnight.timestamp())]
        assert len({start for start, _ in calls}) == 1  # Fixed inception, independent of display clock.


def test_live_resolves_after_sync_and_enrichment_once(tmp_path, monkeypatch):
    path = tmp_path / "db"
    events = []
    original = MarketStore.resolve_report_context
    def resolver(store, *a, **kw):
        events.append("resolve")
        return original(store, *a, **kw)
    monkeypatch.setattr(MarketStore, "resolve_report_context", resolver)
    monkeypatch.setattr(cli, "WarEraApiClient", lambda **k: object())
    monkeypatch.setattr(cli, "WarEraMarketApi", lambda client: object())
    def sync(api, store, **kw):
        events.append("sync")
        ingest(store, trade("new", C))
        return type("Summary", (), {"pages_fetched": 1, "transactions_inserted": 1})()
    monkeypatch.setattr(cli, "sync_market_data", sync)
    monkeypatch.setattr(cli, "_refresh_participant_display", lambda *a, **k: events.append("enrich"))
    prepared = []
    monkeypatch.setattr(cli, "generate_report", lambda args, assumptions, result: prepared.append(result))
    monkeypatch.setattr(sys, "argv", ["warera", "--live", "--quiet", "--market-db", str(path), "--output", str(tmp_path / "out")])
    cli.main()
    assert events == ["sync", "enrich", "resolve"]
    assert prepared[0].context.analysis_as_of == C


def test_generation_timestamp_is_independent_in_rendered_header(tmp_path):
    import pandas as pd
    from warera_quant.report import generate_html_report
    a = resolve_context(C, generated_at=C + timedelta(days=10))
    b = replace(a, generated_at=C + timedelta(days=60))
    frame = pd.DataFrame([{"item_name": "Bread"}])
    for context in (a, b):
        html = generate_html_report(frame, context=context, output_dir=tmp_path)
        assert f"Analysis as of {C.isoformat()}" in html
        assert f"Report generated {context.generated_at.isoformat()}" in html


def test_empty_inflation_chart_has_no_wall_clock_axis(tmp_path, monkeypatch):
    from matplotlib.figure import Figure
    from types import SimpleNamespace
    from warera_quant.charts import render_inflation_overview_chart
    captured = []
    monkeypatch.setattr(Figure, "savefig", lambda fig, *a, **kw: captured.append(fig.axes[0].get_xlim()))
    result = SimpleNamespace(definition=SimpleNamespace(enabled=True, key="broad_market"), monthly_evolution=())
    render_inflation_overview_chart([result], tmp_path / "empty.png")
    assert captured and max(captured[0]) < 2  # No calendar axis invented from now.


def test_subsecond_observations_sort_by_source_time_not_insertion_id(tmp_path):
    with MarketStore(tmp_path / "db") as store:
        # Backfill inserts older snapshots later, in the same integer second.
        for at, value in ((C, 10), (C-EPS, 9)):
            store.insert_price_observations({"bread": value}, at)
            store.insert_order_book_observations({"bread": TopOrders([OrderLevel(value, 1)], [OrderLevel(value+1, 1)])}, at)
        end = C + EPS
        assert [r["current_price"] for r in store.price_observations_for_window("bread", 0, end=end)] == [9, 10]
        assert [r["best_bid"] for r in store.order_book_history_with_levels("bread", end=end)] == [9, 10]
        forecast = market_data.evaluate_item_forecast(store, item_code="bread", context=resolve_context(C))
        assert datetime.fromisoformat(forecast.current_observed_at.replace("Z", "+00:00")) == C
