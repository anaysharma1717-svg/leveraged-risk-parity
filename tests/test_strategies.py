"""
Simple checks. Run with:  py -m pytest tests

They use made-up prices so they run fast and need no internet.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategies  # noqa: E402
from backtest import run_backtest  # noqa: E402


def fake_prices(days=400, seed=0):
    """Random-walk prices for 5 assets with different volatilities."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=days)
    vols = [0.012, 0.009, 0.004, 0.010, 0.013]
    rets = rng.normal(0.0002, vols, size=(days, 5))
    return pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates, columns=["SPY", "TLT", "IEF", "GLD", "DBC"])


def test_unlevered_weights_sum_to_one():
    p = fake_prices()
    for name, t in strategies.build_all(p).items():
        if name in ("60/40", "Equal weight", "Risk parity 1x"):
            assert np.allclose(t.sum(axis=1), 1.0), name


def test_levered_weights_sum_to_leverage():
    p = fake_prices()
    s = strategies.build_all(p)
    assert np.allclose(s["Risk parity 1.5x"].sum(axis=1), 1.5)
    assert np.allclose(s["Risk parity 2x"].sum(axis=1), 2.0)
    _, lev = strategies.vol_target(p)
    vt = s["Vol target 10%"]
    assert np.allclose(vt.sum(axis=1), lev)
    assert lev.between(strategies.MIN_LEVERAGE, strategies.MAX_LEVERAGE).all()


def test_calm_asset_gets_most_money():
    """Risk parity should give the least volatile asset (IEF here) the biggest weight."""
    w = strategies.inverse_vol_weights(fake_prices())
    assert (w.idxmax(axis=1) == "IEF").all()


def test_no_lookahead_in_weights():
    """
    Weights on a rebalance day must not change if we change prices ON or AFTER that day.
    We wreck every price from that day on and check the weights stay the same.
    """
    p = fake_prices()
    dates = strategies.rebalance_dates(p)
    day = dates[len(dates) // 2]
    before = strategies.inverse_vol_weights(p)
    _, lev_before = strategies.vol_target(p)

    changed = p.copy()
    changed.loc[day:] = changed.loc[day:] * np.random.default_rng(1).uniform(0.5, 2.0, changed.loc[day:].shape)
    after = strategies.inverse_vol_weights(changed)
    _, lev_after = strategies.vol_target(changed)

    pd.testing.assert_series_equal(before.loc[day], after.loc[day])
    assert lev_before.loc[day] == lev_after.loc[day]


def test_no_lookahead_in_backtest():
    """Equity up to a day must not depend on prices after that day."""
    p = fake_prices()
    rate = pd.Series(0.02, index=p.index)
    t = strategies.levered(strategies.inverse_vol_weights(p), 2.0)
    a, _ = run_backtest(p, rate, t)
    cut = p.index[300]
    changed = p.copy()
    changed.loc[p.index > cut] *= 1.5
    b, _ = run_backtest(changed, rate, t)
    pd.testing.assert_series_equal(a.loc[:cut, "equity"], b.loc[:cut, "equity"])


def test_margin_call_forces_1x():
    """A 30% crash on a 3x position (leverage jumps above 4x) must trigger a margin call down to 1x."""
    p = fake_prices(200)
    crash_day = p.index[150]
    p.loc[crash_day:] *= 0.70
    rate = pd.Series(0.0, index=p.index)
    t = strategies.levered(strategies.inverse_vol_weights(p), 3.0)
    daily, events = run_backtest(p, rate, t)
    assert any(e["type"] == "margin call" and e["date"] == crash_day for e in events)
    assert abs(daily.loc[crash_day, "leverage"] - 1.0) < 1e-9


def test_correlation_guard_only_removes_borrowing():
    """The guard can only lower leverage, to at most 1x, and never touches the mix of assets."""
    p = fake_prices()
    t = strategies.levered(strategies.inverse_vol_weights(p), 2.0)
    corr = pd.Series(np.where(np.arange(len(t)) % 2 == 0, 0.3, -0.3), index=t.index)   # guard on every other month
    g = strategies.correlation_guard(t, corr)
    lev, glev = t.sum(axis=1), g.sum(axis=1)
    assert np.allclose(glev[corr > 0], 1.0)
    assert np.allclose(glev[corr <= 0], lev[corr <= 0])
    assert np.allclose(g.div(glev, axis=0), t.div(lev, axis=0))          # same mix
    # vol target with the guard on every month: leverage becomes min(normal leverage, 1x)
    vt, vlev = strategies.vol_target(p)
    guarded = strategies.correlation_guard(vt, pd.Series(0.5, index=vlev.index))
    assert np.allclose(guarded.sum(axis=1), vlev.clip(upper=1.0))


def test_no_lookahead_in_correlation():
    """The correlation used on a rebalance day must not change if prices on or after that day change."""
    p = fake_prices()
    day = strategies.rebalance_dates(p)[-3]
    before = strategies.stock_bond_correlation(p)
    changed = p.copy()
    changed.loc[day:] = changed.loc[day:] * np.random.default_rng(2).uniform(0.5, 2.0, changed.loc[day:].shape)
    after = strategies.stock_bond_correlation(changed)
    assert before.loc[day] == after.loc[day]
