"""
main.py - runs the whole project with one command:  python main.py   (on Windows: py main.py)

Phase 1: load the data and print a summary.
Phase 2: build the strategies, run the backtests (costs + margin calls) and print a first summary.
Phase 3: full metrics, stress periods and the 1x-4x leverage curve.
Phase 4: all charts (charts/), results.csv and leverage_curve.csv.
"""
import os

import numpy as np
import pandas as pd

import metrics
import plots
import strategies
from backtest import leverage_curve, run_backtest
from data import load_data, print_summary


def quick_stats(daily):
    """A few simple numbers for Phase 2 (the full metrics come in Phase 3)."""
    eq = daily["equity"]
    years = len(eq) / 252
    final = eq.iloc[-1]
    cagr = final ** (1 / years) - 1 if final > 0 else -1.0
    rets = eq.pct_change().dropna()
    return {
        "$1 grows to": round(final, 2),
        "CAGR %": round(cagr * 100, 2),
        "vol %": round(rets.std() * np.sqrt(252) * 100, 1),
        "avg leverage": round(daily["leverage"].mean(), 2),
        "max leverage": round(daily["leverage"].max(), 2),
        # costs as % of equity per year, so they are comparable between strategies
        "borrow cost %/yr": round((daily["borrow_cost"] / eq.shift(1)).sum() / years * 100, 2),
        "trading cost %/yr": round((daily["trade_cost"] / eq.shift(1)).sum() / years * 100, 3),
    }


def margin_call_table(prices, rate, all_targets):
    """For each strategy: how many margin calls, and the final result with vs without being forced out."""
    rows = []
    for name, t in all_targets.items():
        with_mc, events = run_backtest(prices, rate, t, margin_calls=True)
        without_mc, events_off = run_backtest(prices, rate, t, margin_calls=False)
        calls = [e for e in events if e["type"] == "margin call"]
        rows.append({
            "strategy": name,
            "margin calls": len(calls),
            "dates": ", ".join(f"{e['date']:%Y-%m-%d}" for e in calls[:3]) + (" ..." if len(calls) > 3 else ""),
            "$1 -> (with margin calls)": round(with_mc["equity"].iloc[-1], 2),
            "$1 -> (never forced out)": round(without_mc["equity"].iloc[-1], 2),
            "wiped out if never forced out": any(e["type"] == "wiped out" for e in events_off),
        })
    return pd.DataFrame(rows)


def main():
    # ---------------- Phase 1: data ----------------
    prices, rate, raw_missing_prices, raw_missing_rate = load_data()
    print_summary(prices, rate, raw_missing_prices, raw_missing_rate)

    # ---------------- Phase 2: strategies + backtests ----------------
    all_targets = strategies.build_all(prices)
    start = all_targets["60/40"].index[0]
    print(f"\n=== PHASE 2: BACKTESTS (from {start:%Y-%m-%d}, after 60 days needed to measure volatility) ===")

    results = {}
    for name, t in all_targets.items():
        daily, events = run_backtest(prices, rate, t)
        results[name] = (daily, events)
    quick = pd.DataFrame({n: quick_stats(d) for n, (d, _) in results.items()}).T
    print(quick.to_string())

    # Average risk parity mix, to show "bonds get more money"
    rp = all_targets["Risk parity 1x"]
    print("\nAverage risk parity weights (1x) - the calm assets get the most money:")
    print((rp.mean() * 100).round(1).astype(str).add("%").to_string())
    _, lev = strategies.vol_target(prices)
    print(f"\nVol-target leverage: average {lev.mean():.2f}x, min {lev.min():.2f}x, max {lev.max():.2f}x, "
          f"at the 3x cap {100 * (lev >= strategies.MAX_LEVERAGE).mean():.0f}% of months, "
          f"at the 0.5x floor {100 * (lev <= strategies.MIN_LEVERAGE).mean():.0f}% of months")

    print("\nMargin calls (forced to 1x if equity / exposure < 25%):")
    mc = margin_call_table(prices, rate, all_targets)
    print(mc.to_string(index=False))

    # None of the strategies above should come close to 4x, so show where margin calls start to appear.
    extra = {f"Risk parity {x}x": strategies.levered(rp, x) for x in (3.0, 3.5, 3.75, 4.0)}
    print("\nMargin call check at higher leverage (the full 1x-4x leverage curve comes in Phase 3):")
    print(margin_call_table(prices, rate, extra).to_string(index=False))

    table, st, curve, curve_no_mc = phase3(prices, rate, results, rp)
    phase4(prices, results, rp, quick, mc, table, st, curve, curve_no_mc)


def curve_table(rows, rate):
    return pd.DataFrame([{"leverage": r["leverage"], "CAGR %": round(metrics.cagr(r["equity"]) * 100, 2),
                          "Volatility %": round(metrics.annual_vol(r["equity"]) * 100, 1),
                          "Sharpe": round(metrics.sharpe(r["equity"], rate), 2),
                          "Max drawdown %": round(metrics.max_drawdown(r["equity"]) * 100, 1),
                          "margin calls": r["margin calls"], "wiped out": r["wiped out"]} for r in rows])


def phase3(prices, rate, results, rp):
    print("\n=== PHASE 3: METRICS ===")
    equity = {name: daily["equity"] for name, (daily, _) in results.items()}
    table = pd.DataFrame({name: metrics.summary(eq, rate) for name, eq in equity.items()}).T
    print(table.to_string())

    print("\nSTRESS PERIODS (return and worst drawdown inside each period)")
    st = metrics.stress_table(equity)
    for col in ("return %", "max drawdown %"):
        print(f"\n{col}:")
        print(st.pivot(index="strategy", columns="period", values=col).loc[list(equity), list(metrics.STRESS_PERIODS)].to_string())

    # ---- leverage curve ----
    levels = np.arange(1.0, 4.0001, 0.25)
    curve = curve_table(leverage_curve(prices, rate, rp, levels, margin_calls=True), rate)
    curve_no_mc = curve_table(leverage_curve(prices, rate, rp, levels, margin_calls=False), rate)
    print("\nLEVERAGE CURVE - risk parity at 1x to 4x, WITH margin calls:")
    print(curve.to_string(index=False))
    print("\nSame, but NEVER forced out (shows the pure effect of borrowing cost + volatility drag):")
    print(curve_no_mc.drop(columns="margin calls").to_string(index=False))
    plots.leverage_curve(curve, curve_no_mc)

    # ---- explain volatility drag with this data ----
    r1 = results["Risk parity 1x"][0]["equity"].pct_change().dropna()
    mu = r1.mean() * 252                      # average (arithmetic) yearly return at 1x
    var = r1.var() * 252                      # yearly variance at 1x
    borrow = (rate.loc[r1.index].mean() + 0.01)
    best = (mu - borrow) / var
    wide = curve_table(leverage_curve(prices, rate, rp, np.arange(1.0, 12.01, 0.5), margin_calls=False), rate)
    peak = wide.loc[wide["CAGR %"].idxmax()]
    print(f"""
WHY RETURNS RISE AND THEN FALL (volatility drag)
  Growth per year at leverage L is roughly:  L x {mu:.1%} (average return)  -  (L - 1) x {borrow:.1%} (borrowing)
                                             -  L^2 x {var:.2%} / 2 (volatility drag)
  The first two parts grow in a straight line with L, but the drag grows with L squared:
  doubling leverage doubles the return but QUADRUPLES the drag. A 50% loss needs +100% to recover,
  so big swings eat into compound growth even when the average return is the same.
  The formula says growth peaks at L = (average return - borrowing) / variance = {best:.1f}x.
  Simulated without margin calls, CAGR peaks at {peak['leverage']:.1f}x ({peak['CAGR %']:.2f}%) - risk parity is so calm
  that pure volatility drag only bites beyond 4x. Inside 1x-4x, what breaks leverage is the MARGIN CALL:
  being forced to sell at the bottom locks in the loss and misses the rebound.""")
    return table, st, curve, curve_no_mc


def phase4(prices, results, rp, quick, mc, table, st, curve, curve_no_mc):
    print("\n=== PHASE 4: CHARTS AND RESULTS FILES ===")
    equity = {name: daily["equity"] for name, (daily, _) in results.items()}
    plots.equity_curves(equity, metrics.STRESS_PERIODS)
    plots.drawdowns({name: metrics.drawdown(eq) for name, eq in equity.items()})
    plots.rp_weights(rp)
    _, lev = strategies.vol_target(prices)
    plots.vt_leverage(lev, strategies.MAX_LEVERAGE, strategies.MIN_LEVERAGE, strategies.TARGET_VOL)
    # (charts/leverage_curve.png was saved in Phase 3)

    # results.csv: one row per strategy, every metric in one place
    out = table.copy()
    out["Avg leverage"] = quick["avg leverage"]
    out["Borrow cost %/yr"] = quick["borrow cost %/yr"]
    out["Trading cost %/yr"] = quick["trading cost %/yr"]
    out["Margin calls"] = mc.set_index("strategy")["margin calls"]
    for period in metrics.STRESS_PERIODS:
        x = st[st["period"] == period].set_index("strategy")
        out[f"{period} return %"] = x["return %"]
        out[f"{period} max DD %"] = x["max drawdown %"]
    here = os.path.dirname(os.path.abspath(__file__))
    out.index.name = "strategy"
    out.to_csv(os.path.join(here, "results.csv"))
    both = curve.merge(curve_no_mc, on="leverage", suffixes=("", " (never forced out)"))
    both.to_csv(os.path.join(here, "leverage_curve.csv"), index=False)
    print("saved results.csv and leverage_curve.csv")


if __name__ == "__main__":
    main()
