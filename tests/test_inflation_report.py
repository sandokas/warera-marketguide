from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd

from warera_quant.market_data import (
    IndexChange,
    IndexDefinition,
    IndexObservation,
    InflationIndexResult,
)
from warera_quant.metrics import InflationContribution
from warera_quant.report import (
    generate_html_report,
    inflation_export_frame,
    inflation_summary_html,
    write_inflation_csv,
)


UTC = timezone.utc


def _result(*, key="base_pp", available=True, provisional=False):
    base_start = datetime(2026, 1, 1, tzinfo=UTC)
    base_end = base_start + timedelta(days=7)
    end = datetime(2026, 2, 7, tzinfo=UTC)
    definition = IndexDefinition(
        key=key,
        name="Total Upstream PP Inflation Index" if key == "base_pp" else "Industrial Index",
        description="Market value of embodied official production effort.",
        version="2026-q1",
        effective_from=base_end,
        method="base_pp_value",
        components=("steel", "concrete"),
        weight_source="base_period_traded_total_upstream_pp",
        base_period_start=base_start,
        base_period_end=base_end,
    )
    observation = IndexObservation(
        key, definition.version, end, provisional, 108.25 if available else None,
        88.0 if available else 40.0, 2, 2 if available else 1,
        () if available else ("concrete",),
    )
    contributor = InflationContribution("steel", 4.5, 9.0, 0.5)
    change = IndexChange(
        key, definition.version, "30D", end - timedelta(days=30), end,
        8.25 if available else None,
        "Inflation" if available else "Insufficient data",
        88.0 if available else 40.0,
        -7.621247 if available else None,
        (contributor,) if available else (),
        () if available else ("concrete",),
        159.78 if available else None,
    )
    return InflationIndexResult(
        definition=definition,
        weights=(("steel", 0.5), ("concrete", 0.5)),
        base_prices=(("steel", 10.0), ("concrete", 5.0)),
        current_prices=(("steel", 12.5), ("concrete", 5.5)),
        observations=(observation,), changes=(change,), exclusions=(),
    )


def test_inflation_summary_renders_one_purchasing_power_signal_without_component_tables():
    html = inflation_summary_html(
        [_result()],
    )

    assert "BTC is losing purchasing power" in html
    assert "Inflationary &middot; prices +8.25%" in html
    assert "BTC purchasing power -7.62%" in html
    assert "Annualized pace +159.78%" in html
    assert "Early signal" in html
    assert 'data-report-asset="inflation-component-table"' not in html
    assert "Steel +4.50 pp" not in html
    assert "overflow" not in html


def test_unavailable_change_is_not_rendered_as_zero_or_stable():
    html = inflation_summary_html([_result(available=False)])

    assert "Insufficient history" in html
    assert "BTC purchasing-power direction is not available yet" in html
    assert "30D +0.00%" not in html
    assert "BTC purchasing power N/A" in html


def test_generate_report_accepts_precomputed_inflation_results():
    html = generate_html_report(pd.DataFrame(), inflation_results=[_result(provisional=True)])

    assert "Historical Inflation" in html
    assert "Historical Inflation" in html


def test_inflation_is_between_market_trends_and_item_price_context():
    frame = pd.DataFrame([{
        "item_code": "steel", "item_name": "Steel", "last_trade_price": 10.0,
    }])

    html = generate_html_report(frame, inflation_results=[_result()])

    assert html.index("Market Trends") < html.index("Historical Inflation")
    assert html.index("Historical Inflation") < html.index("Item Price Context")


def test_inflation_export_is_normalized_and_contains_no_json(tmp_path):
    result = _result()
    frame = inflation_export_frame([result])

    assert len(frame) == 1
    assert frame.loc[0, "index_key"] == "base_pp"
    assert frame.loc[0, "period_label"] == "30D"
    assert frame.loc[0, "change_pct"] == 8.25
    assert frame.loc[0, "availability_status"] == "available"
    assert not any("contributor" in column for column in frame.columns)

    path = write_inflation_csv([result], tmp_path)
    assert path.name == "market_inflation.csv"
    exported = pd.read_csv(path)
    assert exported.loc[0, "index_version"] == "2026-q1"
    assert "{" not in path.read_text(encoding="utf-8")


def test_disabled_indices_are_not_published():
    result = _result()
    disabled = InflationIndexResult(
        definition=IndexDefinition(**{**result.definition.__dict__, "enabled": False, "disabled_reason": "No mapping"}),
        weights=(), base_prices=(), current_prices=(), observations=(), changes=(), exclusions=(),
    )

    assert inflation_summary_html([disabled]) == ""
    assert inflation_export_frame([disabled]).empty


def test_monthly_headline_does_not_fall_back_to_7d():
    result = _result()
    thirty_day = replace(
        result.changes[0], change_pct=None, classification="Insufficient data",
        contributors=(), purchasing_power_change_pct=None,
    )
    seven_day = replace(
        result.changes[0], period_label="7D",
        start_at=result.changes[0].end_at - timedelta(days=7),
        change_pct=-2.0, classification="Deflation",
        contributors=(InflationContribution("concrete", -2.0, -4.0, 0.5),),
    )
    result = replace(result, changes=(thirty_day, seven_day))

    html = inflation_summary_html([result])

    assert "BTC purchasing-power direction is not available yet" in html
    assert "Insufficient history &middot; prices N/A" in html
    assert "30D market-price signal" in html


def test_chart_is_optional_and_rendered_relative_to_output_directory(tmp_path):
    chart = tmp_path / "charts" / "inflation-overview.png"
    html = inflation_summary_html(
        [_result()], chart_paths={"overview": chart}, output_dir=tmp_path,
    )

    assert '<div class="inflation-section">' in html
    assert '<section class="inflation-section">' not in html
    assert 'src="charts/inflation-overview.png"' in html
    assert "Broad Market Inflation: rolling 30D inflation values over the last 90 days" in html
    assert "positive means BTC buys less" in html
    assert "Dates without an authentic 30-day comparison are omitted" in html

    without_chart = inflation_summary_html([_result()], chart_paths={})
    assert "inflation-chart" not in without_chart


def test_headline_omits_component_contributors():
    result = _result()
    contributors = (
        InflationContribution("large_up", 10.0, 20.0, 0.5),
        InflationContribution("small_down", -0.5, -5.0, 0.1),
        InflationContribution("second_up", 8.0, 16.0, 0.5),
        InflationContribution("third_up", 7.0, 14.0, 0.5),
    )
    result = replace(result, changes=(replace(result.changes[0], contributors=contributors),))

    html = inflation_summary_html([result])

    assert "BTC purchasing power -7.62%" in html
    assert "large_up" not in html
    assert "small_down" not in html
    assert "second_up" not in html
    assert "third_up" not in html


def test_export_retains_enabled_definition_without_observations():
    result = replace(_result(), observations=(), changes=())

    frame = inflation_export_frame([result])

    assert list(frame["period_label"]) == ["7D", "30D", "90D"]
    assert set(frame["availability_status"]) == {"definition_unavailable"}
    assert set(frame["classification"]) == {"Insufficient data"}
    assert frame["level"].isna().all()
    assert frame["change_pct"].isna().all()


def test_broad_market_scope_and_friendly_weight_labels_are_disclosed():
    base = _result(key="broad_market")
    broad = replace(
        base,
        definition=replace(
            base.definition,
            key="broad_market",
            name="Broad Market Price Index",
            weight_source="base_period_trade_value",
        ),
    )

    html = inflation_summary_html([broad])

    assert "BTC is losing purchasing power" in html
