"""
metrics.py - turns a daily equity curve into the numbers a risk desk looks at.

All functions take `equity` (daily portfolio value, starting at 1.0) and, where needed,
`rate` (T-bill yield per year as a decimal, the "risk-free" rate).
"""
import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Stress periods (start, end), chosen by well-known market dates, not by looking at our results.
STRESS_PERIODS = {
    "2008 crisis (Oct 2007 - Mar 2009)": ("2007-10-09", "2009-03-09"),    # S&P 500 peak to bottom
    "Covid crash (Feb - Mar 2020)": ("2020-02-19", "2020-03-23"),        # S&P 500 peak to bottom
    "2022 rate shock (calendar 2022)": ("2022-01-03", "2022-12-30"),     # stocks AND bonds fell
}


def excess_returns(equity, rate):
    """Daily return minus what cash (T-bills) would have earned that day (yesterday's rate / 252)."""
    r = equity.pct_change().dropna()
    rf = rate.shift(1).reindex(r.index) / TRADING_DAYS
    return r - rf


def cagr(equity):
    """Compound annual growth rate: the steady yearly return that turns the start value into the end value."""
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if equity.iloc[-1] <= 0:
        return -1.0
    return (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1


def annual_vol(equity):
    """How much the daily returns swing, scaled up to a year."""
    return equity.pct_change().dropna().std() * np.sqrt(TRADING_DAYS)


def sharpe(equity, rate):
    """Average return above cash, per unit of total volatility (yearly)."""
    x = excess_returns(equity, rate)
    return x.mean() / x.std() * np.sqrt(TRADING_DAYS)


def sortino(equity, rate):
    """Like Sharpe, but only counts the BAD (below-cash) days as risk."""
    x = excess_returns(equity, rate)
    downside = np.sqrt((np.minimum(x, 0) ** 2).mean())
    return x.mean() / downside * np.sqrt(TRADING_DAYS)


def drawdown(equity):
    """For every day: how far below its previous best value the portfolio is (0 = at a new high, -0.3 = 30% down)."""
    return equity / equity.cummax() - 1


def max_drawdown(equity):
    return drawdown(equity).min()


def calmar(equity):
    """CAGR divided by the worst drawdown: return earned per unit of worst pain."""
    mdd = max_drawdown(equity)
    return cagr(equity) / abs(mdd) if mdd < 0 else np.nan


def calendar_year_returns(equity):
    """Return in each calendar year (first and last years are partial)."""
    year_end = equity.groupby(equity.index.year).last()
    year_start = year_end.shift(1)              # each year starts where the previous one ended
    year_start.iloc[0] = equity.iloc[0]         # ... except the first year, which starts at the first day
    return year_end / year_start - 1


def worst_year(equity):
    yr = calendar_year_returns(equity)
    return yr.min(), int(yr.idxmin())


def longest_recovery(equity):
    """
    Longest time spent below a previous high, in calendar days, from the old high until it is beaten again.
    If the portfolio is still below its high at the end of the data, that stretch counts too (marked ongoing).
    """
    peak_value, peak_day = equity.iloc[0], equity.index[0]
    best = (0, equity.index[0], equity.index[0], False)
    for day, v in equity.items():
        if v >= peak_value:
            length = (day - peak_day).days
            if length > best[0]:
                best = (length, peak_day, day, False)
            peak_value, peak_day = v, day
    ongoing = (equity.index[-1] - peak_day).days
    if ongoing > best[0]:
        best = (ongoing, peak_day, equity.index[-1], True)
    return best          # (days, from, to, still_ongoing)


def summary(equity, rate):
    """All the headline metrics for one strategy, as a dictionary."""
    wy, wy_year = worst_year(equity)
    rec_days, rec_from, rec_to, ongoing = longest_recovery(equity)
    return {
        "CAGR %": round(cagr(equity) * 100, 2),
        "Volatility %": round(annual_vol(equity) * 100, 1),
        "Sharpe": round(sharpe(equity, rate), 2),
        "Sortino": round(sortino(equity, rate), 2),
        "Max drawdown %": round(max_drawdown(equity) * 100, 1),
        "Calmar": round(calmar(equity), 2),
        "Worst year %": round(wy * 100, 1),
        "Worst year": wy_year,
        "Longest recovery (years)": round(rec_days / 365.25, 1),
        "Recovery period": f"{rec_from:%Y-%m} to {rec_to:%Y-%m}" + (" (not yet recovered)" if ongoing else ""),
    }


def stress_table(equity_by_strategy):
    """Return and worst drawdown of each strategy inside each stress period."""
    rows = []
    for period, (start, end) in STRESS_PERIODS.items():
        for name, eq in equity_by_strategy.items():
            # start from the close BEFORE the period begins, so the first day's move is included
            start_close = eq[eq.index < start].iloc[-1:]
            window = pd.concat([start_close, eq.loc[start:end]])
            rows.append({"period": period, "strategy": name,
                         "return %": round((window.iloc[-1] / window.iloc[0] - 1) * 100, 1),
                         "max drawdown %": round(max_drawdown(window) * 100, 1)})
    return pd.DataFrame(rows)
