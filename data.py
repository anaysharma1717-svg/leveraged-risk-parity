"""
data.py - gets the price data and the borrowing rate.

What it does:
  1. Downloads daily prices for the 5 ETFs and the 3-month T-bill yield (^IRX) from Yahoo Finance.
  2. Saves them as CSV files in the data/ folder, so the next run reads the files instead of downloading again.
  3. Cleans the data (lines the dates up, fills small gaps) and prints a short summary.

To force a fresh download, delete the files in data/ (or call load_data(refresh=True)).
"""
import os

import pandas as pd
import yfinance as yf

# ---- Settings -------------------------------------------------------------
ASSETS = ["SPY", "TLT", "IEF", "GLD", "DBC"]   # stocks, long bonds, mid bonds, gold, commodities
RATE_TICKER = "^IRX"                            # 3-month T-bill yield, used as the base borrowing rate
START = "2007-01-01"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PRICES_FILE = os.path.join(DATA_DIR, "prices_raw.csv")
RATE_FILE = os.path.join(DATA_DIR, "irx_raw.csv")


def download_raw():
    """Download from Yahoo and save the raw numbers exactly as received."""
    os.makedirs(DATA_DIR, exist_ok=True)

    # auto_adjust=True gives prices adjusted for dividends and splits,
    # which is what we want: the return of actually holding the ETF.
    prices = yf.download(ASSETS, start=START, auto_adjust=True, progress=False)["Close"]
    prices = prices[ASSETS]                     # keep a fixed column order
    prices.to_csv(PRICES_FILE)

    # ^IRX is quoted in percent per year (e.g. 5.2 means 5.2%).
    rate = yf.download(RATE_TICKER, start=START, auto_adjust=True, progress=False)["Close"]
    rate.columns = ["IRX"]
    rate.to_csv(RATE_FILE)


def load_data(refresh=False):
    """
    Returns (prices, rate):
      prices: table of daily adjusted closes, one column per ETF
      rate:   daily T-bill yield as a decimal per year (5.2% -> 0.052), on the same dates as prices
    """
    if refresh or not (os.path.exists(PRICES_FILE) and os.path.exists(RATE_FILE)):
        print("Downloading data from Yahoo Finance...")
        download_raw()
    else:
        print("Using saved data in data/ (delete those files to re-download).")

    prices = pd.read_csv(PRICES_FILE, index_col=0, parse_dates=True)
    rate = pd.read_csv(RATE_FILE, index_col=0, parse_dates=True)["IRX"]

    # Remember the raw gaps so we can report them before we fix them.
    raw_missing_prices = prices.isna().sum()
    raw_missing_rate = int(rate.reindex(prices.index).isna().sum())

    # Clean up:
    # - keep only days where at least one ETF traded (the trading calendar)
    # - drop days at the very start before every ETF has a price
    # - fill any single missing day with the previous day's value (no peeking forward)
    prices = prices.dropna(how="all")
    prices = prices.loc[prices.notna().all(axis=1).idxmax():]
    prices = prices.ffill()

    # The T-bill yield sometimes has holidays the stock market doesn't (and vice versa).
    # Put it on the ETF dates and carry the last known value forward.
    rate = rate.reindex(prices.index).ffill() / 100.0

    return prices, rate, raw_missing_prices, raw_missing_rate


def print_summary(prices, rate, raw_missing_prices, raw_missing_rate):
    """Short, readable data check."""
    print("\n=== DATA SUMMARY ===")
    print(f"Dates: {prices.index[0]:%Y-%m-%d} to {prices.index[-1]:%Y-%m-%d}  ({len(prices)} trading days)")
    print("\nMissing values in the raw download (filled with the previous day's value):")
    for col in prices.columns:
        print(f"  {col:4s}: {int(raw_missing_prices[col])}")
    print(f"  IRX : {raw_missing_rate} (days the ETFs traded but the T-bill yield was not published)")

    daily = prices.pct_change().dropna()
    years = len(daily) / 252
    table = pd.DataFrame({
        "first price": prices.iloc[0].round(2),
        "last price": prices.iloc[-1].round(2),
        "total return %": ((prices.iloc[-1] / prices.iloc[0] - 1) * 100).round(0),
        "CAGR %": (((prices.iloc[-1] / prices.iloc[0]) ** (1 / years) - 1) * 100).round(1),
        "annual vol %": (daily.std() * 252 ** 0.5 * 100).round(1),
        "worst day %": (daily.min() * 100).round(1),
    })
    print("\nPer asset (buy and hold, dividends included):")
    print(table.to_string())

    print("\nCorrelation of daily returns:")
    print(daily.corr().round(2).to_string())

    print(f"\nT-bill yield (^IRX): average {rate.mean():.2%}, min {rate.min():.2%}, max {rate.max():.2%}, "
          f"latest {rate.iloc[-1]:.2%}")


if __name__ == "__main__":
    p, r, mp, mr = load_data()
    print_summary(p, r, mp, mr)
