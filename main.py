"""
main.py - runs the whole project with one command:  python main.py   (on Windows: py main.py)

Phase 1: load the data and print a summary.
Phase 2: build the strategies, run the backtests (costs + margin calls) and print a first summary.
Phase 3: full metrics, stress periods and the 1x-4x leverage curve.
Phase 4: all charts (charts/), results.csv and leverage_curve.csv.
Phase A: borrowing-cost sensitivity, a month-by-month check of 2008, and a clearer margin-call model note.
Phase B: correlation guard - cut leverage to 1x in months when stocks and bonds have been moving together.
"""
import os

import numpy as np
import pandas as pd

import backtest
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

    table, st, curve = phase3(prices, rate, results, rp)
    sens = borrow_sensitivity(prices, rate, all_targets, results)
    check_2008(prices, results, all_targets)
    guard = phase_b(prices, rate, all_targets)
    phase4(prices, rate, results, rp, quick, mc, table, st, curve, sens, guard)


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
    with_rule = curve_table(leverage_curve(prices, rate, rp, levels, margin_calls=True), rate)
    no_rule = curve_table(leverage_curve(prices, rate, rp, levels, margin_calls=False), rate)
    curve = pd.DataFrame({
        "leverage": with_rule["leverage"],
        "CAGR % (with margin rule)": with_rule["CAGR %"],
        "Sharpe (with margin rule)": with_rule["Sharpe"],
        "Max drawdown % (with margin rule)": with_rule["Max drawdown %"],
        "margin calls": with_rule["margin calls"],
        "CAGR % (if no margin rule existed)": no_rule["CAGR %"],
        "Max drawdown % (if no margin rule existed)": no_rule["Max drawdown %"],
    })
    print("\nLEVERAGE CURVE - risk parity at 1x to 4x")
    print("  'with margin rule' = forced down to 1x when equity falls below 25% of exposure")
    print("  'if no margin rule existed' = the broker never forces a sale (pure borrowing cost + volatility drag)")
    print(curve.to_string(index=False))
    plots.leverage_curve(curve)
    print(f"""
NOTE ON THE MARGIN MODEL
  - The margin rule is: equity / exposure must stay at or above {backtest.MARGIN_LEVEL:.0%}, i.e. leverage at most
    {1 / backtest.MARGIN_LEVEL:.0f}x. At a 4x target, equity is EXACTLY 25% of exposure right after every rebalance, so the
    portfolio starts on the margin-call line and almost any down day triggers a forced sale.
    The margin calls at 4x are a result of the model's rule, not of unusual markets.
  - After a margin call the portfolio is cut to 1x at that day's close and STAYS at 1x until the next
    monthly rebalance (first trading day of the next month). Only then does it go back to its target leverage.""")

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
    return table, st, curve


# ---------------------------------------------------------------------------
# Phase A
# ---------------------------------------------------------------------------
SPREADS = {0.0: "no spread (theoretical)", 0.0025: "institutional / futures",
           0.005: "in between", 0.01: "retail broker (main case)"}
LEVERED = ["Risk parity 1.5x", "Risk parity 2x", "Vol target 10%"]


def borrow_sensitivity(prices, rate, all_targets, results):
    """Rerun the levered strategies with different borrowing spreads over the T-bill rate."""
    print("\n=== PHASE A1: SENSITIVITY TO BORROWING COST ===")
    base = metrics.summary(results["60/40"][0]["equity"], rate)
    rows, equity = [], {}
    for name in LEVERED:
        for spread, label in SPREADS.items():
            eq = run_backtest(prices, rate, all_targets[name], borrow_spread=spread)[0]["equity"]
            m = metrics.summary(eq, rate)
            equity[f"{name} @ spread {spread:.2%}"] = eq
            rows.append({"strategy": name, "spread over T-bill %": spread * 100, "case": label,
                         "CAGR %": m["CAGR %"], "Sharpe": m["Sharpe"], "Max drawdown %": m["Max drawdown %"],
                         "CAGR vs 60/40": round(m["CAGR %"] - base["CAGR %"], 2),
                         "Sharpe vs 60/40": round(m["Sharpe"] - base["Sharpe"], 2),
                         "Max DD vs 60/40": round(m["Max drawdown %"] - base["Max drawdown %"], 1)})
    t = pd.DataFrame(rows)
    print(f"60/40 for comparison: CAGR {base['CAGR %']}%, Sharpe {base['Sharpe']}, "
          f"max drawdown {base['Max drawdown %']}%")
    print("('vs 60/40' columns: positive CAGR / Sharpe = better than 60/40; negative Max DD = deeper drawdown)")
    print(t.to_string(index=False))

    inst = t[t["spread over T-bill %"] == 0.25]
    beat_sharpe = inst[inst["Sharpe vs 60/40"] > 0]["strategy"].tolist()
    beat_cagr = inst[inst["CAGR vs 60/40"] > 0]["strategy"].tolist()
    print(f"""
DOES LEVERAGE BEAT 60/40 ONCE BORROWING IS CHEAP?
  At the institutional spread (0.25%), higher CAGR than 60/40: {', '.join(beat_cagr) or 'none'};
  higher Sharpe than 60/40: {', '.join(beat_sharpe) or 'none'}.""")
    return t, equity


def check_2008(prices, results, all_targets):
    """Month by month: what vol target and risk parity 2x held, and what they earned, Jul 2007 - Mar 2009."""
    print("\n=== PHASE A2: CHECK OF THE 2008 RESULT (vol target +11.9% vs risk parity 2x +9.7%) ===")
    print("Positions are % of our own money (equity), set at the close of the first trading day of each month.")
    print("Monthly return = change in equity from one month-end to the next.")
    monthly = {}
    for name in ("Risk parity 2x", "Vol target 10%"):
        t = all_targets[name].loc["2007-07":"2009-03"]
        eq = results[name][0]["equity"]
        ret = eq.resample("ME").last().pct_change()
        table = (t * 100).round(1)
        table.insert(0, "leverage x", t.sum(axis=1).round(2))
        table["month return %"] = (ret.loc["2007-07":"2009-03"].to_numpy() * 100).round(1)
        table.index = table.index.strftime("%Y-%m")
        monthly[name] = table
        print(f"\n{name}:")
        print(table.to_string())

    # Confirm the volatility estimate only uses data up to the previous day
    dates = strategies.rebalance_dates(prices)
    ok = all(strategies.past_returns(prices, d).index[-1] < d for d in dates)
    ex = pd.Timestamp("2008-10-01")
    w = strategies.past_returns(prices, ex)
    print(f"\nLookahead check: for all {len(dates)} rebalances, the last return used is from BEFORE "
          f"the rebalance day: {ok}")
    print(f"  Example: the 2008-10-01 rebalance used {len(w)} daily returns from {w.index[0]:%Y-%m-%d} "
          f"to {w.index[-1]:%Y-%m-%d} (the previous trading day).")

    rp, vt = monthly["Risk parity 2x"], monthly["Vol target 10%"]
    worst = sorted(rp["month return %"].nsmallest(3).index)
    lines = "; ".join(f"{m}: {rp.loc[m, 'month return %']:+.1f}% vs {vt.loc[m, 'month return %']:+.1f}% "
                      f"(vol target at {vt.loc[m, 'leverage x']:.2f}x)" for m in worst)
    print(f"""
WHY VOL TARGET DID BETTER IN 2008
  Both strategies hold the same mix of assets; the only difference is leverage. Risk parity 2x stayed at 2x the
  whole time, while vol target had already cut to about 1.4x from February 2008, because markets were getting
  more volatile (Bear Stearns, oil spike), and cut again to about 0.75x after the October crash.
  In risk parity 2x's three worst months it lost far more: {lines}.
  Vol target gave some of that back in the Nov-Dec 2008 rebound (it was below 1x by then), but avoiding big losses
  matters more than catching rebounds when returns compound, and it paid less interest because it borrowed less.""")
    return monthly


# ---------------------------------------------------------------------------
# Phase B: correlation guard
# ---------------------------------------------------------------------------
GUARD_STRATEGIES = ["Risk parity 2x", "Vol target 10%"]
GUARD_SPREADS = {0.01: "retail 1%", 0.0025: "institutional 0.25%"}


def phase_b(prices, rate, all_targets):
    print("\n=== PHASE B: CORRELATION GUARD ===")
    print(f"Rule (fixed in advance, not tuned): each month, if the correlation of daily SPY and IEF returns over the last "
          f"{strategies.CORR_WINDOW} trading days (up to the previous day) is above {strategies.GUARD_THRESHOLD:g}, "
          f"leverage is cut to 1x for that month.")
    print("NOTE: this rule was designed AFTER seeing 2022, so its 2022 result is NOT a true out-of-sample test.")
    corr = strategies.stock_bond_correlation(prices)
    on = corr > strategies.GUARD_THRESHOLD
    print(f"Guard was ON in {int(on.sum())} of {int(corr.notna().sum())} months ({100 * on.mean():.0f}%). "
          f"Months ON per year: " + ", ".join(f"{y}: {n}" for y, n in on[on].groupby(on[on].index.year).size().items()))

    # main result: threshold 0
    equity, labels = {}, []
    for name in GUARD_STRATEGIES:
        for spread, sl in GUARD_SPREADS.items():
            for guarded in (False, True):
                t = strategies.correlation_guard(all_targets[name], corr) if guarded else all_targets[name]
                label = f"{name}, {sl}, " + ("WITH guard" if guarded else "no guard")
                equity[label] = run_backtest(prices, rate, t, borrow_spread=spread)[0]["equity"]
                labels.append(label)
    table = pd.DataFrame({k: metrics.summary(v, rate) for k, v in equity.items()}).T
    print("\nFULL METRICS, with and without the guard (threshold 0 = main result):")
    print(table.drop(columns=["Recovery period"]).to_string())

    st = metrics.stress_table(equity)
    print("\nSTRESS PERIODS, return % (max drawdown % in brackets):")
    ret = st.pivot(index="strategy", columns="period", values="return %").loc[labels, list(metrics.STRESS_PERIODS)]
    dd = st.pivot(index="strategy", columns="period", values="max drawdown %").loc[labels, list(metrics.STRESS_PERIODS)]
    print((ret.astype(str) + " (" + dd.astype(str) + ")").to_string())

    # robustness check only: other thresholds
    rows = []
    for th in (-0.2, 0.0, 0.2):
        t_on = corr > th
        for name in GUARD_STRATEGIES:
            for spread, sl in GUARD_SPREADS.items():
                eq = run_backtest(prices, rate, strategies.correlation_guard(all_targets[name], corr, th),
                                  borrow_spread=spread)[0]["equity"]
                m = metrics.summary(eq, rate)
                s22 = metrics.stress_table({"x": eq})
                rows.append({"threshold": th, "strategy": name, "borrowing": sl,
                             "months guarded %": round(100 * t_on.mean()), "CAGR %": m["CAGR %"],
                             "Sharpe": m["Sharpe"], "Max drawdown %": m["Max drawdown %"],
                             "2022 return %": s22.iloc[2]["return %"]})
    rob = pd.DataFrame(rows)
    print("\nROBUSTNESS CHECK ONLY (threshold 0 is the main result; -0.2 and +0.2 are NOT alternatives to pick from):")
    print(rob.to_string(index=False))

    plots.correlation_guard(corr, strategies.GUARD_THRESHOLD,
                            equity["Risk parity 2x, retail 1%, no guard"],
                            equity["Risk parity 2x, retail 1%, WITH guard"], metrics.STRESS_PERIODS)
    return table, st, rob, equity


def phase4(prices, rate, results, rp, quick, mc, table, st, curve, sens, guard):
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
    out.insert(0, "Borrow spread %", backtest.BORROW_SPREAD * 100)
    for period in metrics.STRESS_PERIODS:
        x = st[st["period"] == period].set_index("strategy")
        out[f"{period} return %"] = x["return %"]
        out[f"{period} max DD %"] = x["max drawdown %"]

    # extra rows: the levered strategies at the other borrowing spreads (Phase A1)
    sens_table, sens_equity = sens
    main_case = f"{backtest.BORROW_SPREAD:.2%}"
    extra = {k: v for k, v in sens_equity.items() if not k.endswith(main_case)}
    xt = pd.DataFrame({name: metrics.summary(eq, rate) for name, eq in extra.items()}).T
    xt.insert(0, "Borrow spread %", [float(k.split("spread ")[1].rstrip("%")) for k in extra])
    xs = metrics.stress_table(extra)
    for period in metrics.STRESS_PERIODS:
        x = xs[xs["period"] == period].set_index("strategy")
        xt[f"{period} return %"] = x["return %"]
        xt[f"{period} max DD %"] = x["max drawdown %"]
    out = pd.concat([out, xt])

    # extra rows: correlation guard results (Phase B, threshold 0, both borrowing spreads)
    g_table, g_st, g_rob, g_equity = guard
    gt = g_table.copy()
    gt.insert(0, "Borrow spread %", [0.25 if "institutional" in k else 1.0 for k in gt.index])
    for period in metrics.STRESS_PERIODS:
        x = g_st[g_st["period"] == period].set_index("strategy")
        gt[f"{period} return %"] = x["return %"]
        gt[f"{period} max DD %"] = x["max drawdown %"]
    gt = gt[gt.index.str.contains("WITH guard")]          # the "no guard" rows are already above
    out = pd.concat([out, gt])
    g_rob.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "guard_robustness.csv"), index=False)

    here = os.path.dirname(os.path.abspath(__file__))
    out.index.name = "strategy"
    out.to_csv(os.path.join(here, "results.csv"))
    curve.to_csv(os.path.join(here, "leverage_curve.csv"), index=False)
    sens_table.to_csv(os.path.join(here, "borrow_sensitivity.csv"), index=False)
    print("saved results.csv, leverage_curve.csv and borrow_sensitivity.csv")


if __name__ == "__main__":
    main()
