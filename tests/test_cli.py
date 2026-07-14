import sys

import pandas as pd
import pytest

from warera_quant.cli import main


@pytest.mark.parametrize(
    "option,value",
    [
        ("--trade-quantity", "0"),
        ("--trade-fee-pct", "100"),
        ("--min-net-margin-pct", "-1"),
        ("--max-quote-age-minutes", "nan"),
    ],
)
def test_trade_options_validate_bounds(monkeypatch, option, value):
    monkeypatch.setattr(sys, "argv", ["warera-quant", option, value])
    with pytest.raises(SystemExit):
        main()


def test_csv_mode_writes_unavailable_flip_fields_and_visible_assumptions(tmp_path, monkeypatch):
    csv_path = tmp_path / "market.csv"
    output = tmp_path / "output"
    pd.DataFrame([{
        "item_name": "Bread", "bid": 9, "ask": 10, "trades_7d": 5,
        "high_7d": 11, "low_7d": 8,
    }]).to_csv(csv_path, index=False)
    monkeypatch.setattr(sys, "argv", [
        "warera-quant", "--csv", str(csv_path), "--output", str(output),
        "--trade-quantity", "7", "--trade-fee-pct", "2.5",
        "--min-net-margin-pct", "3", "--max-quote-age-minutes", "15", "--quiet",
    ])

    main()

    exported = pd.read_csv(output / "market_trends.csv")
    assert exported.loc[0, "flip_verdict"] == "Unavailable"
    assert exported.loc[0, "flip_quantity"] == 7
    report = (output / "market_report.html").read_text(encoding="utf-8")
    assert "Quantity: 7" in report
    assert "Fees assumed: 2.50% per side" in report
    assert "Minimum margin: 3.00%" in report
    assert "Max quote age: 15m" in report
