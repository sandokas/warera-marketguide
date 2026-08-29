from pathlib import Path

import pandas as pd

from warera_quant.market_data import ActionCostDefinition, ActionCostResult
from warera_quant.metrics import ActionCostComponent
from warera_quant.report import (
    action_cost_export_frame,
    generate_html_report,
    write_action_costs_csv,
    write_outputs,
)


def _definition() -> ActionCostDefinition:
    return ActionCostDefinition(
        key="factory_upgrade",
        name="Upgrade a factory",
        category="Industrial expansion",
        action_description="Upgrade one factory by one level.",
        unit_description="one factory upgrade",
        quantities=(("steel", 200.0), ("concrete", 10.0)),
        source_url="https://example.test/guide",
        source_published_at="2026-06-18",
        provenance="Authoritative guide, verified 2026-08-28",
    )


def _result(*, available: bool = True) -> ActionCostResult:
    components = (
        ActionCostComponent("steel", 200.0, 4.0, 800.0),
        ActionCostComponent("concrete", 10.0, 3.0, 30.0),
    ) if available else (ActionCostComponent("steel", 200.0, 4.0, 800.0),)
    return ActionCostResult(
        definition=_definition(),
        total_cost=830.0 if available else None,
        coverage_pct=100.0 if available else 50.0,
        required_component_count=2,
        priced_component_count=2 if available else 1,
        missing_item_codes=() if available else ("concrete",),
        components=components,
    )


def test_action_cost_export_is_normalized_per_required_component():
    frame = action_cost_export_frame([_result(available=False)])

    assert list(frame["item_code"]) == ["steel", "concrete"]
    assert frame.loc[0, "component_cost"] == 800.0
    assert frame.loc[0, "component_status"] == "available"
    assert pd.isna(frame.loc[1, "representative_price"])
    assert frame.loc[1, "component_status"] == "missing"
    assert set(frame["availability_status"]) == {"unavailable"}
    assert frame.loc[0, "source_published_at"] == "2026-06-18"


def test_action_cost_csv_remains_available_but_section_is_not_in_report(tmp_path: Path):
    assert "Current Action Cost Benchmarks" not in generate_html_report(pd.DataFrame())

    result = _result()
    html = generate_html_report(pd.DataFrame())
    assert "Current Action Cost Benchmarks" not in html

    csv_path = write_action_costs_csv([result], tmp_path)
    assert csv_path.name == "market_action_costs.csv"
    assert len(pd.read_csv(csv_path)) == 2

    output_dir = tmp_path / "with-actions"
    returned = write_outputs(pd.DataFrame(), output_dir, action_cost_results=[result])
    assert len(returned) == 2
    assert (output_dir / "market_action_costs.csv").exists()

    without_dir = tmp_path / "without-actions"
    write_outputs(pd.DataFrame(), without_dir)
    assert not (without_dir / "market_action_costs.csv").exists()
