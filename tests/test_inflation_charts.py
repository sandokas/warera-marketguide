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
    assert labels == ["Market prices"]
    assert plotted[0] == pytest.approx((0.0, 1.0))


def test_inflation_chart_has_no_data_or_service_dependencies():
    source = (Path(__file__).parents[1] / "src" / "warera_quant" / "charts.py").read_text(encoding="utf-8")

    forbidden = ["market_data", "live_market", "warera_api", "api_client", "MarketStore", "sqlite3", "requests"]
    assert [name for name in forbidden if name in source] == []
