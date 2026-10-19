"""
plots.py - makes the charts and saves them as PNG files in charts/.
"""
import os

import matplotlib
import matplotlib.ticker
matplotlib.use("Agg")            # draw to files, no pop-up windows
import matplotlib.pyplot as plt  # noqa: E402

CHART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "charts")


def save(fig, name):
    os.makedirs(CHART_DIR, exist_ok=True)
    path = os.path.join(CHART_DIR, name)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print("saved", path)


def leverage_curve(curve, zone_start=3.25):
    """Two panels: leverage vs CAGR, and leverage vs max drawdown, with the margin rule and without it.
    The area above `zone_start` (where margin calls begin) is shaded red."""
    fig, (a, b) = plt.subplots(1, 2, figsize=(12, 4.5))
    x = curve["leverage"]
    for ax, col, title, ylabel in ((a, "CAGR %", "Risk parity: leverage vs CAGR", "CAGR % per year"),
                                   (b, "Max drawdown %", "Risk parity: leverage vs max drawdown", "max drawdown %")):
        ax.axvspan(zone_start, x.max() + 0.1, color="red", alpha=0.08, label=f"margin call zone (above {zone_start}x)")
        ax.plot(x, curve[f"{col} (with margin rule)"], "o-",
                label="with margin rule (forced to 1x below 25% equity)")
        ax.plot(x, curve[f"{col} (if no margin rule existed)"], "s--",
                label="if no margin rule existed (volatility drag only)")
        ax.set(title=title, xlabel="leverage (x)", ylabel=ylabel, xlim=(x.min() - 0.1, x.max() + 0.1))
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)
    save(fig, "leverage_curve.png")


def shade_stress(ax, periods):
    """Grey bands over the stress periods so they are easy to spot."""
    import pandas as pd
    for start, end in periods.values():
        ax.axvspan(pd.Timestamp(start), pd.Timestamp(end), color="grey", alpha=0.15)


def equity_curves(equity, periods):
    """Growth of $1 for every strategy, log scale (equal % moves look the same size at any level)."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for name, eq in equity.items():
        ax.plot(eq.index, eq, label=f"{name} (${eq.iloc[-1]:.2f})", lw=1.3)
    ax.set_yscale("log")
    money = matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:g}")     # show $1, $2, $4 instead of 10^0
    ax.yaxis.set_major_formatter(money)
    ax.yaxis.set_minor_formatter(money)
    shade_stress(ax, periods)
    ax.set(title="Growth of $1 (log scale; grey = 2008, Covid, 2022)", ylabel="value of $1 invested")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)
    save(fig, "equity_curves.png")


def drawdowns(dd):
    """How far each strategy is below its previous best, day by day."""
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for name, d in dd.items():
        ax.plot(d.index, d * 100, label=f"{name} (worst {d.min() * 100:.1f}%)", lw=1.1)
    ax.set(title="Drawdowns: % below the previous high", ylabel="drawdown %")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower left")
    save(fig, "drawdowns.png")


def rp_weights(weights):
    """Risk parity (1x) money weights at each monthly rebalance, stacked to 100%."""
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.stackplot(weights.index, (weights * 100).T.to_numpy(), labels=weights.columns, alpha=0.85)
    ax.set(title="Risk parity weights over time (share of money; bonds get the most)", ylabel="% of portfolio",
           ylim=(0, 100))
    ax.legend(loc="upper left", fontsize=8, ncol=5)
    ax.margins(x=0)
    save(fig, "rp_weights.png")


def vt_leverage(leverage, cap, floor, target):
    """Leverage chosen each month by the volatility-target strategy."""
    fig, ax = plt.subplots(figsize=(11, 4.5))
    ax.step(leverage.index, leverage, where="post", lw=1.3, label="leverage chosen")
    ax.axhline(cap, color="red", ls="--", lw=1, label=f"cap {cap:g}x")
    ax.axhline(floor, color="green", ls="--", lw=1, label=f"floor {floor:g}x")
    ax.axhline(1, color="black", lw=0.6)
    ax.set(title=f"Vol-target strategy: leverage set each month to aim for {target:.0%} volatility",
           ylabel="leverage (x)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    save(fig, "vt_leverage.png")


def correlation_guard(corr, threshold, eq_no_guard, eq_guard, periods):
    """Top: the stock-bond correlation each month and when the guard was on. Bottom: 2x with and without the guard."""
    import pandas as pd
    fig, (a, b) = plt.subplots(2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [1, 1.4]})
    a.step(corr.index, corr, where="post", lw=1.2, label="SPY-IEF correlation, last 63 days")
    a.axhline(threshold, color="red", ls="--", lw=1, label=f"guard threshold ({threshold:g})")
    on = corr > threshold
    for d, is_on in on.items():
        if is_on:                                   # shade each guarded month
            a.axvspan(d, d + pd.offsets.MonthBegin(1), color="red", alpha=0.15, lw=0)
            b.axvspan(d, d + pd.offsets.MonthBegin(1), color="red", alpha=0.08, lw=0)
    a.set(title="Correlation guard: red months = stocks and bonds moving together, leverage cut to 1x",
          ylabel="correlation")
    a.grid(alpha=0.3)
    a.legend(fontsize=8, loc="lower left")
    b.plot(eq_no_guard.index, eq_no_guard, lw=1.2, label=f"Risk parity 2x, no guard (${eq_no_guard.iloc[-1]:.2f})")
    b.plot(eq_guard.index, eq_guard, lw=1.2, label=f"Risk parity 2x, with guard (${eq_guard.iloc[-1]:.2f})")
    b.set_yscale("log")
    money = matplotlib.ticker.FuncFormatter(lambda v, _: f"${v:g}")
    b.yaxis.set_major_formatter(money)
    b.yaxis.set_minor_formatter(money)
    b.set(title="Growth of $1, retail borrowing (T-bill + 1%), log scale", ylabel="value of $1")
    b.grid(alpha=0.3, which="both")
    b.legend(fontsize=8, loc="upper left")
    save(fig, "correlation_guard.png")
