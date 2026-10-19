"""
strategies.py - decides how much of each asset to hold at every monthly rebalance.

Every strategy returns a "targets" table:
  - one row per rebalance date (the first trading day of each month)
  - one column per asset
  - each number = dollars of that asset to hold per $1 of our own money (equity)

So the row adds up to the leverage:
  1.0 = no borrowing, 2.0 = we hold $2 of assets for every $1 we own (the other $1 is borrowed),
  0.5 = only half our money is invested, the rest sits in cash.

NO LOOKAHEAD: the weights for a rebalance on day t are computed only from returns up to day t-1
(the previous day's close). The trade then happens at the close of day t.
"""
import numpy as np
import pandas as pd

VOL_WINDOW = 60        # days of history used to measure volatility
TRADING_DAYS = 252     # used to turn daily volatility into yearly volatility

TARGET_VOL = 0.10      # vol-target strategy: aim for 10% expected volatility per year
MAX_LEVERAGE = 3.0     # ... but never more than 3x
MIN_LEVERAGE = 0.5     # ... and never less than 0.5x


def rebalance_dates(prices):
    """First trading day of each month, starting once we have 60 days of returns to measure volatility."""
    idx = prices.index
    first_of_month = pd.Series(idx, index=idx).groupby([idx.year, idx.month]).first()
    # Returns start on day 1 (day 0 has no previous price). On day t we use returns t-60 .. t-1,
    # so the first usable day is t = 61.
    earliest = idx[VOL_WINDOW + 1]
    return pd.DatetimeIndex([d for d in first_of_month if d >= earliest])


def past_returns(prices, day):
    """Daily returns of the last VOL_WINDOW days BEFORE `day` (day itself is not included)."""
    rets = prices.pct_change()
    i = prices.index.get_loc(day)
    return rets.iloc[i - VOL_WINDOW:i]


def fixed_weights(prices, weights):
    """Same weights every month, e.g. 60/40. `weights` is a dict like {"SPY": 0.6, "IEF": 0.4}."""
    dates = rebalance_dates(prices)
    row = pd.Series(0.0, index=prices.columns)
    for asset, w in weights.items():
        row[asset] = w
    return pd.DataFrame([row] * len(dates), index=dates)


def inverse_vol_weights(prices):
    """
    Risk parity (simple version): weight each asset by 1 / its volatility, then scale so weights sum to 100%.
    A calm asset (IEF) gets a lot of money, a wild asset (SPY, DBC) gets little,
    so each one moves the portfolio by roughly the same amount.
    """
    rows = []
    dates = rebalance_dates(prices)
    for d in dates:
        vol = past_returns(prices, d).std()      # daily volatility of each asset, from past data only
        inv = 1.0 / vol
        rows.append(inv / inv.sum())             # normalise so the weights add up to 1
    return pd.DataFrame(rows, index=dates)


def levered(targets, leverage):
    """Same mix, scaled up: 2x means $2 of assets per $1 of equity."""
    return targets * leverage


def vol_target(prices):
    """
    Risk parity weights, then choose leverage each month so the EXPECTED portfolio volatility is 10% a year.
    Expected volatility comes from the last 60 days of returns, including how the assets move together
    (covariance). Leverage = 10% / expected vol, kept between 0.5x and 3x.
    Returns (targets, leverage series).
    """
    weights = inverse_vol_weights(prices)
    lev = []
    for d in weights.index:
        cov = past_returns(prices, d).cov() * TRADING_DAYS     # yearly covariance matrix, past data only
        w = weights.loc[d].to_numpy()
        expected_vol = float(np.sqrt(w @ cov.to_numpy() @ w))
        lev.append(np.clip(TARGET_VOL / expected_vol, MIN_LEVERAGE, MAX_LEVERAGE))
    lev = pd.Series(lev, index=weights.index, name="leverage")
    return weights.mul(lev, axis=0), lev


def build_all(prices):
    """All strategies from the brief, in the order we report them."""
    rp = inverse_vol_weights(prices)
    vt, _ = vol_target(prices)
    return {
        "60/40": fixed_weights(prices, {"SPY": 0.6, "IEF": 0.4}),
        "Equal weight": fixed_weights(prices, {a: 1 / len(prices.columns) for a in prices.columns}),
        "Risk parity 1x": rp,
        "Risk parity 1.5x": levered(rp, 1.5),
        "Risk parity 2x": levered(rp, 2.0),
        "Vol target 10%": vt,
    }


# ---------------------------------------------------------------------------
# Correlation guard (Phase B)
# ---------------------------------------------------------------------------
CORR_WINDOW = 63       # about 3 months of trading days
GUARD_THRESHOLD = 0.0  # fixed in advance: if stocks and bonds moved together (correlation above 0), stop borrowing


def stock_bond_correlation(prices, window=CORR_WINDOW):
    """
    For each rebalance date: correlation of daily SPY and IEF returns over the last `window` trading days,
    using only returns up to the previous day. NaN if there is not yet enough history (only the first month).
    """
    rets = prices.pct_change()
    out = {}
    for d in rebalance_dates(prices):
        i = prices.index.get_loc(d)
        past = rets.iloc[max(1, i - window):i]          # rows before day d only
        out[d] = past["SPY"].corr(past["IEF"]) if len(past) >= window else float("nan")
    return pd.Series(out, name="SPY-IEF correlation")


def correlation_guard(targets, corr, threshold=GUARD_THRESHOLD):
    """
    If the stock-bond correlation is above `threshold`, take leverage down to 1x for that month
    (same mix of assets, just no borrowing). Otherwise keep the strategy's normal leverage.
    The guard only ever REMOVES borrowing: a month that was already below 1x (vol target) stays as it was.
    When the correlation is unknown (first month), the guard does nothing.
    """
    lev = targets.sum(axis=1)
    guarded_lev = lev.where(~(corr.reindex(targets.index) > threshold), lev.clip(upper=1.0))
    return targets.mul(guarded_lev / lev, axis=0)
