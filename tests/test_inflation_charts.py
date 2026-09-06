from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from matplotlib.axes import Axes

from warera_quant.charts import (
    render_inflation_overview_chart,
)


def _observation(day: int, level: float | None, *, provisional: bool = False):
    return SimpleNamespace(
        as_of=datetime(2026, 8, day, tzinfo=timezone.utc),
        level=level,
        is_provisional=provisional,
    )


def _result(*observations, key="standard_combat"):
    baseline = next((observation.level for observation in observations if observation.level), None)
    evolution = tuple(
        SimpleNamespace(
            as_of=observation.as_of,
            change_pct=(
                None if observation.level is None or baseline is None
                else (observation.level / baseline - 1) * 100
            ),
        )
        for observation in observations
    )
    return SimpleNamespace(
        definition=SimpleNamespace(name="Standard Combat Index", key=key, enabled=True),
        observations=observations,
        monthly_evolution=evolution,
    )


def test_render_inflation_overview_contains_only_broad_market_series(monkeypatch, tmp_path: Path):
    labels = []
    plotted = []
    original_plot = Axes.plot

    def capture_plot(self, x, y, *args, **kwargs):
        labels.append(kwargs.get("label"))
        plotted.append(tuple(y))
        return original_plot(self, x, y, *args, **kwargs)

    monkeypatch.setattr(Axes, "plot", capture_plot)
    output = render_inflation_overview_chart((
        _result(_observation(1, 100), _observation(2, 101), key="broad_market"),
        _result(_observation(1, 100), _observation(2, 102), key="base_pp"),
        _result(_observation(1, 100), _observation(2, 150), key="standard_combat"),
    ), tmp_path / "overview.png")

    assert output.is_file()
    assert labels == ["Rolling 30D inflation"]
    assert plotted[0] == pytest.approx((0.0, 1.0))


def test_inflation_overview_has_required_90d_copy_and_latest_label(
    monkeypatch, tmp_path: Path,
):
    titles = []
    annotations = []
    text_values = []
    original_title = Axes.set_title
    original_annotate = Axes.annotate
    original_text = Axes.text

    def capture_title(self, label, *args, **kwargs):
        titles.append(label)
        return original_title(self, label, *args, **kwargs)

    def capture_annotate(self, text, *args, **kwargs):
        annotations.append(text)
        return original_annotate(self, text, *args, **kwargs)

    def capture_text(self, x, y, text, *args, **kwargs):
        text_values.append(text)
        return original_text(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Axes, "set_title", capture_title)
    monkeypatch.setattr(Axes, "annotate", capture_annotate)
    monkeypatch.setattr(Axes, "text", capture_text)
    result = _result(_observation(1, 100), _observation(2, 102), key="broad_market")

    output = render_inflation_overview_chart((result,), tmp_path / "overview.png")

    assert output.stat().st_size > 0
    assert titles == ["Broad Market Inflation — Rolling 30D over the Last 90 Days"]
    assert "Latest 30D: +2.00%" in annotations
    assert any("Positive means BTC buys less" in value for value in text_values)
    assert any("Partial history" in value for value in text_values)


def test_inflation_overview_renders_unavailable_state_without_fabricated_points(
    monkeypatch, tmp_path: Path,
):
    text_values = []
    original_text = Axes.text

    def capture_text(self, x, y, text, *args, **kwargs):
        text_values.append(text)
        return original_text(self, x, y, text, *args, **kwargs)

    monkeypatch.setattr(Axes, "text", capture_text)
    result = SimpleNamespace(
        definition=SimpleNamespace(key="broad_market", enabled=True),
        monthly_evolution=(),
    )

    output = render_inflation_overview_chart((result,), tmp_path / "unavailable.png")

    assert output.stat().st_size > 0
    assert any("authentic 30-day lookback is not yet available" in value for value in text_values)


def test_inflation_events_outside_display_window_are_not_annotated(monkeypatch, tmp_path: Path):
    vertical_lines = []
    original_axvline = Axes.axvline

    def capture_axvline(self, x, *args, **kwargs):
        vertical_lines.append(x)
        return original_axvline(self, x, *args, **kwargs)

    monkeypatch.setattr(Axes, "axvline", capture_axvline)
    result = _result(_observation(1, 100), _observation(2, 101), key="broad_market")
    render_inflation_overview_chart(
        (result,), tmp_path / "events.png",
        events=(
            {"at": datetime(2026, 8, 1, tzinfo=timezone.utc), "label": "Sourced"},
            {"at": datetime(2026, 4, 1, tzinfo=timezone.utc), "label": "Too old"},
        ),
    )

    assert vertical_lines == [datetime(2026, 8, 1, tzinfo=timezone.utc)]


def test_inflation_chart_has_no_data_or_service_dependencies():
    source = (Path(__file__).parents[1] / "src" / "warera_quant" / "charts.py").read_text(encoding="utf-8")

    forbidden = ["market_data", "live_market", "warera_api", "api_client", "MarketStore", "sqlite3", "requests"]
    assert [name for name in forbidden if name in source] == []
