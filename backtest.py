"""
backtest.py - simulates a strategy day by day, with costs and margin calls.

Each trading day, in this order:
  1. Markets move: every holding changes by that asset's daily return.
  2. Interest: if we borrowed, we pay (T-bill rate + 1%) / 252 on the borrowed amount.
               If we hold cash (leverage below 1x), we earn the T-bill rate on it.
               We use YESTERDAY's T-bill rate, the one known when the day starts.
  3. Margin check: if equity / total exposure < 25% (leverage above 4x), the broker forces us
               down to 1x at today's close. We pay trading costs on that sale and record the event.
               We then stay at 1x until the next monthly rebalance.
  4. Rebalance (first trading day of each month): trade back to the strategy's target mix,
               paying 5 basis points (0.05%) on the value traded.

Money units: we start with $1 of our own money (equity = 1.0).
"""
import numpy as np
import pandas as pd

BORROW_SPREAD = 0.01       # we borrow at T-bill + 1% a year
TRADING_COST = 0.0005      # 5 basis points of every dollar traded
MARGIN_LEVEL = 0.25        # margin call if equity / exposure falls below 25%
TRADING_DAYS = 252


def run_backtest(prices, rate, targets, margin_calls=True):
    """
    prices:  daily adjusted closes (one column per asset)
    rate:    T-bill yield per year as a decimal, same dates as prices
    targets: from strategies.py - $ of each asset per $1 equity, one row per rebalance date
    margin_calls: False = pretend the broker never forces us out (used to measure what margin calls cost)

    Returns (daily, events):
      daily:  one row per day: equity, exposure, leverage, costs, and the $ held in each asset
      events: list of margin calls (date, leverage before, equity before and after)
    """
    days = prices.index[prices.index >= targets.index[0]]      # start trading on the first rebalance
    assets = list(prices.columns)
    # Plain number arrays instead of pandas tables inside the loop: same maths, much faster.
    rets = prices.pct_change().fillna(0.0).loc[days].to_numpy()
    rate_arr = rate.loc[days].to_numpy()
    target_on = {day: targets.loc[day].to_numpy() for day in targets.index}   # rebalance day -> target mix

    equity = 1.0
    holdings = np.zeros(len(assets))            # $ in each asset
    wiped_out = False
    yesterday_exposure = 0.0
    rows, events = [], []

    for n, day in enumerate(days):
        borrow_cost = trade_cost = 0.0
        margin_call_today = False

        if n > 0 and not wiped_out:
            # 1. markets move
            gain = float((holdings * rets[n]).sum())
            holdings = holdings * (1 + rets[n])

            # 2. interest on what we borrowed (or earned on spare cash), using yesterday's rate
            yesterday_rate = rate_arr[n - 1]
            # borrowed = what we held yesterday minus what we owned yesterday (negative = spare cash)
            borrowed = yesterday_exposure - equity
            if borrowed > 0:
                interest = -borrowed * (yesterday_rate + BORROW_SPREAD) / TRADING_DAYS
                borrow_cost = -interest
            else:
                interest = -borrowed * yesterday_rate / TRADING_DAYS    # cash earns the T-bill rate
            equity += gain + interest

        exposure = float(holdings.sum())

        # Lost everything (only possible if margin calls are switched off, or on a huge one-day crash)
        if not wiped_out and equity <= 0 and n > 0:
            wiped_out = True
            events.append({"date": day, "type": "wiped out", "leverage_before": np.nan,
                           "equity_before": equity, "equity_after": 0.0})
            equity, holdings, exposure = 0.0, holdings * 0, 0.0

        # 3. margin check
        if margin_calls and not wiped_out and exposure > 0 and equity / exposure < MARGIN_LEVEL:
            lev_before = exposure / equity
            equity_before = equity
            sold = exposure - equity                    # sell assets until exposure = equity (1x)
            trade_cost = sold * TRADING_COST
            equity -= trade_cost
            holdings = holdings / exposure * equity     # same mix, just smaller
            exposure = equity
            margin_call_today = True
            events.append({"date": day, "type": "margin call", "leverage_before": lev_before,
                           "equity_before": equity_before, "equity_after": equity})

        # 4. monthly rebalance (skipped on a margin-call day: we stay at 1x until next month)
        if day in target_on and not wiped_out and not margin_call_today:
            traded = float(np.abs(equity * target_on[day] - holdings).sum())    # $ bought + $ sold
            trade_cost += traded * TRADING_COST
            equity -= traded * TRADING_COST
            holdings = equity * target_on[day]
            exposure = float(holdings.sum())

        yesterday_exposure = exposure     # remembered for tomorrow's interest
        rows.append({"date": day, "equity": equity, "exposure": exposure,
                     "leverage": exposure / equity if equity > 0 else 0.0,
                     "borrow_cost": borrow_cost, "trade_cost": trade_cost,
                     "margin_call": margin_call_today, **dict(zip(assets, holdings))})

    daily = pd.DataFrame(rows).set_index("date")
    return daily, events


def leverage_curve(prices, rate, weights, levels, margin_calls=True):
    """
    Runs the same risk parity mix at many fixed leverage levels.
    Returns one row per level with the final value, the equity curve and the number of margin calls.
    """
    rows = []
    for lev in levels:
        daily, events = run_backtest(prices, rate, weights * lev, margin_calls=margin_calls)
        rows.append({"leverage": lev, "equity": daily["equity"],
                     "margin calls": sum(e["type"] == "margin call" for e in events),
                     "wiped out": any(e["type"] == "wiped out" for e in events)})
    return rows
