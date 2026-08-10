from warera_quant.json_loader import market_json_to_dataframe


def test_market_json_to_dataframe_extracts_nested_records_and_aliases():
    data = {
        "data": {
            "items": [
                {
                    "name": "Steel",
                    "best_bid": 1.584,
                    "lowest_ask": 1.59,
                    "volume_7d": 500,
                    "week_high": 1.599,
                    "week_low": 1.584,
                    "last_trade_price": 1.587,
                }
            ]
        }
    }

    df = market_json_to_dataframe(data, records_path="data.items")

    assert df.loc[0, "item_name"] == "Steel"
    assert df.loc[0, "bid"] == 1.584
    assert df.loc[0, "ask"] == 1.59
    assert df.loc[0, "trades_7d"] == 500
    assert df.loc[0, "high_7d"] == 1.599
    assert df.loc[0, "low_7d"] == 1.584
    assert df.loc[0, "last_trade_price"] == 1.587
