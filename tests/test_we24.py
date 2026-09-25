from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from warera_quant.metrics import (
    WE24_COMPONENTS, calculate_we24_market_index, verified_window,
    FlipAssumptions, calculate_fair_value_guidance, calculate_short_term_guidance,
)
from warera_quant.market_store import MarketStore
from warera_quant.market_data import build_we24_market_index

BASE = pd.Timestamp('2026-08-01T00:00:00Z')
DAY = 86400


def fixture(days=60):
    start = BASE - pd.Timedelta(days=28)
    end = BASE + pd.Timedelta(days=days)
    facts = [dict(item_code=code, day_epoch=int(t.timestamp()), quantity=10, turnover=100)
             for t in pd.date_range(start, end, inclusive='left', freq='D') for code in WE24_COMPONENTS]
    coverage = {code: [(int(start.timestamp()), int(end.timestamp()))] for code in WE24_COMPONENTS}
    return facts, coverage, end


def calc(facts, coverage, end, **kwargs):
    return calculate_we24_market_index(facts, coverage, as_of=end, **kwargs)


@pytest.mark.parametrize('days,display,status,count', [(5,30,'partial',6),(29,30,'complete',30),(60,30,'complete',30),(60,90,'partial',61)])
def test_display_never_rebases_or_manufactures_history(days, display, status, count):
    facts, coverage, end = fixture(days)
    result = calc(facts, coverage, end, display_days=display)
    assert result['coverage_status'] == status
    assert result['valid_observation_count'] == count
    assert result['latest_level'] == pytest.approx(100)
    assert result['first_calculated_at'] == BASE.isoformat()


def test_weights_sum_to_one_and_rebalance_does_not_jump():
    facts, coverage, end = fixture(12)
    # Turnover changes sharply, all unit prices remain 10.
    for fact in facts:
        if fact['item_code'] == 'steel' and fact['day_epoch'] >= int(BASE.timestamp()):
            fact['quantity'] *= 100
            fact['turnover'] *= 100
    result = calc(facts, coverage, end)
    assert all(point['level'] == pytest.approx(100) for point in result['observations'])
    assert sum(result['weights'].values()) == pytest.approx(1)
    assert result['weights']['steel'] > result['weights']['bread']
    assert all(row['reference_end'] == row['effective_at'] for row in result['weight_history'])


def test_future_facts_cannot_change_past_series_or_weights():
    facts, coverage, end = fixture(60)
    cutoff = BASE + pd.Timedelta(days=12)
    before = calc(facts, coverage, cutoff)
    for fact in facts:
        if fact['day_epoch'] >= int(cutoff.timestamp()):
            fact['turnover'] *= 99999
    assert calc(facts, coverage, cutoff) == before


@pytest.mark.parametrize('truncation', [1, 27*DAY])
def test_inflation_regression_component_coverage_does_not_prove_full_window(truncation):
    facts, coverage, end = fixture()
    coverage['wood'][0] = (coverage['wood'][0][0] + truncation, coverage['wood'][0][1])
    result = calc(facts, coverage, end)
    assert result['latest_level'] is None
    assert result['coverage_status'] == 'unavailable'
    assert 'Incomplete turnover' in result['reason']


def test_unverified_legacy_history_has_no_initial_observation():
    facts, _, end = fixture()
    assert calc(facts, {}, end)['latest_level'] is None


def test_missing_non_rebalance_price_is_gap_and_can_resume_fixed_holdings():
    facts, coverage, _ = fixture(7)
    missing_day = int((BASE + pd.Timedelta(days=3)).timestamp())  # Tuesday input -> Wednesday point
    facts = [f for f in facts if not (f['day_epoch'] == missing_day and f['item_code'] == 'wood')]
    result = calc(facts, coverage, BASE + pd.Timedelta(days=6))
    points = result['observations']
    assert points[4]['level'] is None
    assert points[5]['level'] == pytest.approx(100)
    assert points[4]['priced_count'] == 23


def test_missing_rebalance_price_breaks_chain_without_reset():
    facts, coverage, end = fixture(12)
    missing_day = int((BASE + pd.Timedelta(days=1)).timestamp())  # Sunday -> Monday rebalance
    facts = [f for f in facts if not (f['day_epoch'] == missing_day and f['item_code'] == 'wood')]
    result = calc(facts, coverage, end)
    assert result['observations'][2]['level'] is None
    assert result['latest_level'] is None


def test_zero_turnover_and_zero_total_are_unavailable_without_price_substitution():
    facts, coverage, end = fixture(1)
    for fact in facts:
        fact['quantity'] = fact['turnover'] = 0
    result = calc(facts, coverage, end)
    assert result['weights'] == {}
    assert result['latest_level'] is None
    assert 'Zero' in result['reason']


def test_verified_window_merges_adjacency_but_rejects_gap():
    assert verified_window([(0,10),(10,20)], 0,20)
    assert not verified_window([(0,9),(10,20)], 0,20)


def test_store_legacy_coverage_and_retention_are_independent_of_first_trade(tmp_path):
    now = datetime(2026,9,6,tzinfo=timezone.utc)
    with MarketStore(tmp_path/'market.db') as store:
        assert store.transaction_coverage() == {}
        store.record_transaction_coverage('bread', 0, int(now.timestamp()), source='test-contiguous-scan')
        store.run_housekeeping(retention_days=10, vacuum_interval_days=0, now=now)
        assert store.transaction_coverage()['bread'][0][0] == int((now-timedelta(days=10)).timestamp())
        result = build_we24_market_index(store, as_of=now)
        assert result['latest_level'] is None


@pytest.mark.parametrize('age,depth,holder', [(1,True,'Sell now'),(31,True,'Hold / reassess'),(-1,True,'Hold / reassess'),(1,False,'Hold / reassess')])
def test_short_term_guidance_respects_fees_size_and_freshness(age, depth, holder):
    assumptions = FlipAssumptions(quantity=10, fee_pct_per_side=2)
    guidance = calculate_fair_value_guidance(fair_price=10, rich_exit_price=11,
        executable_ask_vwap=9, executable_bid_vwap=11, entry_fully_filled=depth,
        exit_fully_filled=depth, assumptions=assumptions)
    result = calculate_short_term_guidance(guidance, quote_age_minutes=age, assumptions=assumptions)
    assert result['short_term_entry_action'] == 'Wait to buy'
    assert result['short_term_holder_action'] == holder
    assert result['short_term_target'] is None
    if 0 <= age <= 30 and depth:
        assert result['short_term_reference_return_pct'] == pytest.approx(100*(10*.98-9*1.02)/(9*1.02))
    else:
        assert result['short_term_reference_return_pct'] is None


def test_relative_price_movements_not_raw_price_average():
    facts, coverage, end = fixture(1)
    for fact in facts:
        if fact['item_code'] == 'steel' and fact['day_epoch'] == int(BASE.timestamp()):
            fact['turnover'] *= 2
    result = calc(facts, coverage, end)
    assert result['latest_level'] == pytest.approx(100 + 100/24)


def test_verified_zero_constituent_is_not_silently_excluded_or_carried():
    facts, coverage, end = fixture(1)
    facts = [fact for fact in facts if fact['item_code'] != 'wood']
    result = calc(facts, coverage, end)
    assert result['component_count'] == 24
    assert result['priced_count'] == 23
    assert result['latest_level'] is None
    assert result['reason'] == 'Incomplete inception daily prices'


def test_new_verified_backfill_can_restore_previously_pruned_coverage(tmp_path):
    now = datetime(2026,9,6,tzinfo=timezone.utc)
    earliest = int((now-timedelta(days=40)).timestamp())
    with MarketStore(tmp_path/'market.db') as store:
        store.record_transaction_coverage('bread', earliest, int(now.timestamp()), source='initial-scan')
        store.run_housekeeping(retention_days=10, vacuum_interval_days=0, now=now)
        assert store.transaction_coverage()['bread'][0][0] > earliest
        store.record_transaction_coverage('bread', earliest, int(now.timestamp()), source='new-complete-backfill')
        assert store.transaction_coverage()['bread'][0][0] == earliest


def test_database_index_uses_transactions_without_sync_metadata(tmp_path, monkeypatch):
    facts, _, end = fixture(35)
    prior = int((BASE-pd.Timedelta(days=29)).timestamp())
    facts.extend(dict(item_code=code,day_epoch=prior,quantity=10,turnover=100) for code in WE24_COMPONENTS)
    with MarketStore(tmp_path/'market.db') as store:
        monkeypatch.setattr(store, 'completed_daily_facts', lambda *args: facts)
        monkeypatch.setattr(store, 'item_codes', lambda **kwargs: list(WE24_COMPONENTS))
        monkeypatch.setattr(store, 'transaction_coverage', lambda *args: (_ for _ in ()).throw(AssertionError('Index must not read sync metadata')))
        result = build_we24_market_index(store, as_of=end.to_pydatetime())
    assert result['coverage_status'] == 'complete'
    assert result['priced_count'] == 24
    assert result['latest_level'] == pytest.approx(100)


def test_observed_daily_inputs_reject_a_truncated_leading_window():
    facts, _, end = fixture(35)
    result = calculate_we24_market_index(facts, as_of=end)
    assert result['latest_level'] is None
    assert 'Incomplete turnover window' in result['reason']


def test_daily_change_uses_previous_calendar_day_even_for_one_day_display():
    facts, coverage, end = fixture(12)
    for row in facts:
        if row['day_epoch'] == int((end - pd.Timedelta(days=1)).timestamp()):
            row['turnover'] *= 1.1
    result = calc(facts, coverage, end, display_days=1)
    assert result['change_1d_pct'] == pytest.approx(10)
    # Missing the prior daily price must not compare against an older valid day.
    facts = [row for row in facts if not (
        row['item_code'] == 'steel'
        and row['day_epoch'] == int((end - pd.Timedelta(days=2)).timestamp())
    )]
    result = calc(facts, coverage, end)
    assert result['change_1d_pct'] is None


def test_weekly_change_uses_exact_seven_day_baseline_even_for_short_display():
    facts, coverage, end = fixture(12)
    for row in facts:
        if row['day_epoch'] == int((end - pd.Timedelta(days=1)).timestamp()):
            row['turnover'] *= 1.1
    result = calc(facts, coverage, end, display_days=1)
    assert result['change_7d_pct'] == pytest.approx(10)
    facts = [row for row in facts if not (
        row['item_code'] == 'steel'
        and row['day_epoch'] == int((end - pd.Timedelta(days=8)).timestamp())
    )]
    result = calc(facts, coverage, end)
    assert result['change_7d_pct'] is None


def test_new_item_admission_preserves_history_and_rebalance_level():
    facts, coverage, end = fixture(60)
    launch = BASE + pd.Timedelta(days=5)
    facts = [f for f in facts if f['item_code'] != 'woodenCase' or f['day_epoch'] >= int(launch.timestamp())]
    coverage['woodenCase'] = [(int(launch.timestamp()), int(end.timestamp()))]
    result = calc(facts, coverage, end, display_days=90)
    admission = pd.Timestamp(result['wooden_case_admitted_at'])
    assert admission.weekday() == 0
    assert admission >= launch + pd.Timedelta(days=28)
    assert admission < launch + pd.Timedelta(days=35)
    assert all(p['level'] == pytest.approx(100) for p in result['observations'])
    assert result['observations'][0]['component_count'] == 23
    assert result['observations'][-1]['component_count'] == 24
    assert result['weights']['woodenCase'] > 0
    assert result['membership_note'] is None
    early = calc(facts, coverage, launch + pd.Timedelta(days=10))
    assert early['latest_level'] == pytest.approx(100)
    assert early['component_count'] == 23
    assert early['membership_note']
    assert early['wooden_case_admitted_at'] is None


def test_unknown_membership_clears_changes_and_series(tmp_path, monkeypatch):
    facts, _, end = fixture(35)
    prior = int((BASE-pd.Timedelta(days=29)).timestamp())
    facts.extend(dict(item_code=code, day_epoch=prior, quantity=10, turnover=100) for code in WE24_COMPONENTS)
    with MarketStore(tmp_path/'market.db') as store:
        monkeypatch.setattr(store, 'completed_daily_facts', lambda *args: facts)
        monkeypatch.setattr(store, 'item_codes', lambda **kwargs: [*WE24_COMPONENTS, 'unknown'])
        result = build_we24_market_index(store, as_of=end.to_pydatetime())
    assert result['latest_level'] is None
    assert result['change_1d_pct'] is None
    assert result['change_7d_pct'] is None
    assert all(p['level'] is None for p in result['observations'])
    assert 'unknown' in result['reason']


def test_admitted_item_cannot_drop_out_when_its_price_is_missing():
    facts, coverage, end = fixture(12)
    missing = int((BASE + pd.Timedelta(days=1)).timestamp())
    facts = [f for f in facts if not (f['item_code'] == 'woodenCase' and f['day_epoch'] == missing)]
    result = calc(facts, coverage, end)
    assert result['wooden_case_admitted_at'] == BASE.isoformat()
    assert result['component_count'] == 24
    assert result['latest_level'] is None
    assert result['observations'][2]['priced_count'] == 23
