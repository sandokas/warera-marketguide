"""Offline inventory guards, not a claim that phase 1 parsers exist yet."""

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "market_contracts"
TRANSACTION = {
    "_id", "money", "quantity", "itemCode", "transactionType", "createdAt",
    "updatedAt", "offerCreatedAt", "sellerId", "buyerId", "sellerMuId",
    "buyerMuId", "sellerCountryId", "buyerCountryId", "sellerPartyId",
    "buyerPartyId", "processedByModAt", "__v",
}
EQUIPMENT = {
    "item._id", "item.type", "item.code", "item.state", "item.maxState",
    "item.quantity", "item.lastAcquisitionAt",
}
ORDER = {"_id", "user", "mu", "country", "itemCode", "quantity", "price", "offerAt", "type", "__v"}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scalar_paths(value, prefix=""):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from scalar_paths(child, f"{prefix}.{key}" if prefix else key)
    elif isinstance(value, list):
        for child in value:
            yield from scalar_paths(child, prefix + "[]")
    else:
        yield prefix


@pytest.mark.parametrize("name", ["trading", "itemMarket"])
def test_observed_transaction_scalars_have_contract_destinations(name):
    rows = load(name + ".observed.json")["result"]["data"]["items"]
    paths = {path for row in rows for path in scalar_paths(row)}
    unknown = paths - TRANSACTION - EQUIPMENT
    assert all(path.startswith("item.skills.") for path in unknown), unknown
    ledger = json.loads((FIXTURES.parents[2] / "docs" / "market-api-evidence.json").read_text())
    inventory = next(row["scalar_paths"] for row in ledger["requests"] if row["label"] == name + "-summary")
    assert paths == set(inventory), "Selected fixtures must cover every observed scalar path"


def test_orders_cover_individual_identity_and_institution():
    data = load("orders.observed.json")["result"]["data"]
    rows = data["buyOrders"] + data["sellOrders"]
    assert len(rows) == len({row["_id"] for row in rows}) == 6
    assert any("mu" in row and "user" in row for row in rows)
    assert len({row["price"] for row in data["buyOrders"]}) < len(data["buyOrders"])
    assert {path for row in rows for path in scalar_paths(row)} <= ORDER


def test_equipment_covers_optional_type_and_multiple_skill_shapes():
    rows = load("itemMarket.observed.json")["result"]["data"]["items"]
    assert any("type" not in row["item"] for row in rows)
    assert any("type" in row["item"] for row in rows)
    assert {len(row["item"]["skills"]) for row in rows} == {1, 2}
    assert len({row["item"]["_id"] for row in rows}) == len(rows)


def test_synthetic_cases_are_separate_and_never_claim_verified_profit():
    data = load("edge-cases.synthetic.json")
    assert data["provenance"].startswith("SYNTHETIC")
    cases = {case["name"]: case for case in data["cases"]}
    assert cases["simultaneous_institutional_references"]["expected"]["buyer_owner"] == "unresolved"
    assert cases["same_id_without_verified_lineage_contract"]["expected"]["basis"] == "unknown"
    assert cases["unknown_scalar_paths_null_and_zero"]["expected"]["preserve_unknown_scalars"] is True
