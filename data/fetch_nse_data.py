"""
fetch_nse_data.py

Pulls real NSE bhavcopy (OHLCV) + delivery % history via jugaad-data for the
symbols listed in data/nse_symbols.yaml, and writes it into the CSV schemas
scanner.py expects (see data/README.md), under data/real/.

IMPORTANT — things this script does NOT do:
  - It does NOT fetch bulk/block deal history. jugaad-data has no historical
    bulk/block deal download function (as of writing); NSE publishes those as
    a separate daily report this script doesn't pull. It writes an empty
    bulk_block_deals.csv (headers only) so the pipeline doesn't crash — pair
    this with config/screening_config.real.yaml's
    require_bulk_block_deal_confirmation: false, or the smart-money
    confirmation stage will reject every candidate.
  - It does NOT rank stocks by market cap. free_float_mcap_rank comes from
    data/nse_symbols.yaml, which you maintain by hand (see comments there).
  - This has not been run against live NSE from Claude's sandbox — the
    sandbox's network is locked to github/pypi/npm. It HAS been run once
    against live NSE via the GitHub Actions workflow: stock_df's delivery %%
    column was initially missed by an incomplete alias guess (fixed — see
    DELIVERY_PCT_ALIASES), and the full-bhavcopy fallback was confirmed
    working (10/10 symbols matched) in that same run. Column-name matching
    elsewhere is still probing/defensive; if a run produces empty output,
    check the logged raw column names in the Action's output.

Usage:
    pip install jugaad-data pandas
    python data/fetch_nse_data.py --symbols-config data/nse_symbols.yaml --out-dir data/real
"""

import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml


def _normalize_col(col: str) -> str:
    return "".join(ch for ch in col.strip().lower() if ch.isalnum())


def _find_col(columns_normalized: dict, aliases: list[str]) -> str | None:
    """columns_normalized maps normalized_name -> original_name."""
    for alias in aliases:
        if alias in columns_normalized:
            return columns_normalized[alias]
    return None


# Column alias sets to probe for, since the exact column names returned by
# jugaad_data.nse.stock_df / index_raw haven't been verified against a live
# run. Add more aliases here if a real run logs unrecognized columns.
DATE_ALIASES = ["date", "historicaldate", "timestamp"]
OPEN_ALIASES = ["open"]
HIGH_ALIASES = ["high"]
LOW_ALIASES = ["low"]
CLOSE_ALIASES = ["close", "ltp"]
VOLUME_ALIASES = ["volume", "totaltradedquantity", "ttlqty", "tottrdqty"]
VALUE_ALIASES = ["value", "turnover", "totaltradedvalue"]
DELIVERY_PCT_ALIASES = [
    "delivery",  # confirmed real column: stock_df's "DELIVERY %%" normalizes to this
    "deliverble", "deliverablepct", "deliverabletotradedquantity",
    "deliverytotradedquantity", "dlyqttotradedqty", "pctdlyqttotradedqty",
    "deliveryqtypct", "deliverypercentage", "delivper", "delivpercentage",
]
# Full-bhavcopy "sec_bhavdata_full" report is a fallback source for
# delivery %% if stock_df's own column ever isn't found (it normally is —
# see DELIVERY_PCT_ALIASES's "delivery" entry, confirmed against a real run).
# Its columns (NSE's own naming, per public documentation of this report):
# SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE,
# LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS,
# NO_OF_TRADES, DELIV_QTY, DELIV_PER.
FULL_BHAV_SYMBOL_ALIASES = ["symbol"]
FULL_BHAV_DELIV_PER_ALIASES = ["delivper", "delivpercentage", "deliverypercentage"]


def fetch_delivery_pct_via_full_bhavcopy(symbols: set, trading_dates: list[str]) -> list[dict]:
    """Fetches NSE's daily 'full bhavcopy with delivery' report for each date
    in trading_dates (YYYY-MM-DD strings) and extracts delivery_pct rows for
    the requested symbols. One failed/unavailable date is logged and skipped
    rather than aborting the whole fetch — NSE's archive occasionally lacks
    a file for a given date (holiday, or the report just isn't published).

    Import path/columns are per public documentation of this report and
    jugaad-data's full_bhavcopy_raw, but NOT verified against a live call
    from this environment — check the printed diagnostics if this comes
    back empty.
    """
    import io
    import csv as csv_module
    from datetime import datetime

    try:
        from jugaad_data.nse import full_bhavcopy_raw
    except ImportError:
        try:
            from jugaad_data.nse.archives import NSEArchives
            full_bhavcopy_raw = NSEArchives().full_bhavcopy_raw
        except ImportError as e:
            print(f"ERROR: could not import a full-bhavcopy function from jugaad_data "
                  f"({e}) — delivery %% will be unavailable this run.", file=sys.stderr)
            return []

    rows = []
    for date_str in trading_dates:
        dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        try:
            raw_text = full_bhavcopy_raw(dt)
        except Exception as e:
            print(f"  [full-bhavcopy {date_str}] WARNING: fetch failed ({e}) — skipping this date", file=sys.stderr)
            continue

        try:
            reader = csv_module.DictReader(io.StringIO(raw_text))
            file_columns_normalized = {_normalize_col(c): c for c in (reader.fieldnames or [])}
        except Exception as e:
            print(f"  [full-bhavcopy {date_str}] WARNING: could not parse CSV ({e}) — skipping this date", file=sys.stderr)
            continue

        symbol_col = _find_col(file_columns_normalized, FULL_BHAV_SYMBOL_ALIASES)
        deliv_col = _find_col(file_columns_normalized, FULL_BHAV_DELIV_PER_ALIASES)
        if symbol_col is None or deliv_col is None:
            print(f"  [full-bhavcopy {date_str}] WARNING: expected columns not found "
                  f"(raw columns: {reader.fieldnames}) — skipping this date", file=sys.stderr)
            continue

        matched = 0
        for row in csv_module.DictReader(io.StringIO(raw_text)):
            sym = row.get(symbol_col, "").strip()
            if sym not in symbols:
                continue
            try:
                pct = float(row[deliv_col].strip())
            except (TypeError, ValueError):
                continue
            rows.append({"symbol": sym, "date": date_str, "delivery_pct": round(pct, 2)})
            matched += 1
        print(f"  [full-bhavcopy {date_str}] matched {matched}/{len(symbols)} symbols")

    return rows


def fetch_symbol_ohlcv_and_delivery(symbol: str, from_date: date, to_date: date):
    """Returns (ohlcv_rows, delivery_rows). ohlcv_rows: list of dicts with
    date/open/high/low/close/volume. delivery_rows: list of dicts with
    date/delivery_pct, possibly empty if no delivery column was found."""
    from jugaad_data.nse import stock_df

    df = stock_df(symbol=symbol, from_date=from_date, to_date=to_date, series="EQ")
    if df is None or len(df) == 0:
        print(f"  [{symbol}] WARNING: no data returned", file=sys.stderr)
        return [], []

    columns_normalized = {_normalize_col(c): c for c in df.columns}
    print(f"  [{symbol}] raw columns: {list(df.columns)}")

    date_col = _find_col(columns_normalized, DATE_ALIASES)
    open_col = _find_col(columns_normalized, OPEN_ALIASES)
    high_col = _find_col(columns_normalized, HIGH_ALIASES)
    low_col = _find_col(columns_normalized, LOW_ALIASES)
    close_col = _find_col(columns_normalized, CLOSE_ALIASES)
    volume_col = _find_col(columns_normalized, VOLUME_ALIASES)
    delivery_col = _find_col(columns_normalized, DELIVERY_PCT_ALIASES)

    missing = [name for name, col in [
        ("date", date_col), ("open", open_col), ("high", high_col),
        ("low", low_col), ("close", close_col), ("volume", volume_col),
    ] if col is None]
    if missing:
        print(f"  [{symbol}] ERROR: could not find columns for {missing} — "
              f"skipping this symbol. Check raw columns above and add aliases "
              f"to fetch_nse_data.py.", file=sys.stderr)
        return [], []

    ohlcv_rows = []
    delivery_rows = []
    df_sorted = df.sort_values(by=date_col)
    for _, row in df_sorted.iterrows():
        d = str(row[date_col])[:10]  # tolerate either date or datetime-like values
        ohlcv_rows.append({
            "date": d,
            "open": float(row[open_col]),
            "high": float(row[high_col]),
            "low": float(row[low_col]),
            "close": float(row[close_col]),
            "volume": float(row[volume_col]),
        })
        if delivery_col is not None:
            try:
                pct = float(row[delivery_col])
                # Defensive: some NSE-derived sources express this as a
                # fraction (0-1) rather than already-scaled 0-100. Unverified
                # against a live run — check Action logs' printed sample
                # values if delivery_pct in the output looks wrong.
                if 0 <= pct <= 1.5:
                    pct *= 100
                delivery_rows.append({"symbol": symbol, "date": d, "delivery_pct": round(pct, 2)})
            except (TypeError, ValueError):
                pass

    if delivery_col is None:
        print(f"  [{symbol}] WARNING: no delivery %% column found "
              f"(tried {DELIVERY_PCT_ALIASES}) — this symbol will have no "
              f"delivery_pct rows, and the smart-money confirmation stage "
              f"will drop it (no delivery history to check).", file=sys.stderr)
    elif delivery_rows:
        print(f"  [{symbol}] sample delivery_pct (first row): {delivery_rows[0]['delivery_pct']} "
              f"— sanity check this looks like a real 0-100 percentage, not a raw fraction.")

    return ohlcv_rows, delivery_rows


def fetch_benchmark_ohlcv(index_name: str, from_date: date, to_date: date):
    from jugaad_data.nse import index_raw

    data = index_raw(index_name, from_date, to_date)
    if not data:
        print(f"  [{index_name}] WARNING: no benchmark data returned", file=sys.stderr)
        return []

    # index_raw returns a list of dicts (per jugaad-data's documented usage),
    # not a DataFrame — normalize keys the same way.
    columns_normalized = {_normalize_col(k): k for k in data[0].keys()}
    print(f"  [{index_name}] raw columns: {list(data[0].keys())}")

    date_col = _find_col(columns_normalized, DATE_ALIASES)
    open_col = _find_col(columns_normalized, OPEN_ALIASES)
    high_col = _find_col(columns_normalized, HIGH_ALIASES)
    low_col = _find_col(columns_normalized, LOW_ALIASES)
    close_col = _find_col(columns_normalized, CLOSE_ALIASES)

    missing = [name for name, col in [
        ("date", date_col), ("open", open_col), ("high", high_col),
        ("low", low_col), ("close", close_col),
    ] if col is None]
    if missing:
        print(f"  [{index_name}] ERROR: could not find columns for {missing} — "
              f"benchmark data will be empty.", file=sys.stderr)
        return []

    rows = []
    for item in sorted(data, key=lambda r: r[date_col]):
        rows.append({
            "date": str(item[date_col])[:10],
            "open": float(item[open_col]),
            "high": float(item[high_col]),
            "low": float(item[low_col]),
            "close": float(item[close_col]),
            "volume": 0,  # index volume isn't meaningful/used by scanner.py
        })
    return rows


def write_ohlcv_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Fetch real NSE data via jugaad-data")
    parser.add_argument("--symbols-config", default="data/nse_symbols.yaml")
    parser.add_argument("--out-dir", default="data/real")
    args = parser.parse_args()

    with open(args.symbols_config) as f:
        symbols_cfg = yaml.safe_load(f)

    lookback_days = symbols_cfg.get("lookback_calendar_days", 420)
    to_date = date.today()
    from_date = to_date - timedelta(days=lookback_days)

    out_dir = Path(args.out_dir)
    ohlcv_dir = out_dir / "ohlcv"

    print(f"Fetching {from_date} to {to_date} for {len(symbols_cfg['symbols'])} symbols...")

    universe_rows = []
    all_delivery_rows = []
    fetched_symbols = set()
    reference_dates = None  # trading-day list from a successfully fetched symbol, used to drive the full-bhavcopy delivery fetch

    for entry in symbols_cfg["symbols"]:
        symbol = entry["symbol"]
        print(f"Fetching {symbol}...")
        ohlcv_rows, delivery_rows = fetch_symbol_ohlcv_and_delivery(symbol, from_date, to_date)
        if not ohlcv_rows:
            print(f"  [{symbol}] skipped (no usable data)", file=sys.stderr)
            continue

        write_ohlcv_csv(ohlcv_dir / f"{symbol}.csv", ohlcv_rows)
        all_delivery_rows.extend(delivery_rows)  # from stock_df, if it ever does carry the column
        fetched_symbols.add(symbol)
        if reference_dates is None:
            reference_dates = [r["date"] for r in ohlcv_rows]

        # 20D avg daily turnover (INR crore) from close * volume.
        trailing = ohlcv_rows[-20:] if len(ohlcv_rows) >= 20 else ohlcv_rows
        avg_turnover_inr = sum(r["close"] * r["volume"] for r in trailing) / len(trailing)
        avg_turnover_cr = avg_turnover_inr / 1e7

        universe_rows.append({
            "symbol": symbol,
            "free_float_mcap_rank": entry["free_float_mcap_rank"],
            "avg_daily_turnover_inr_cr": round(avg_turnover_cr, 2),
            "sector": entry.get("sector", "Unknown"),
        })

    if not all_delivery_rows and reference_dates and fetched_symbols:
        # Fallback only — stock_df normally provides delivery %% directly
        # (confirmed against a real run) via this file's "delivery" alias.
        # This path exists for if that column is ever missing/renamed.
        print("stock_df didn't carry delivery %% this run — falling back to full-bhavcopy report...")
        recent_dates = reference_dates[-25:]
        all_delivery_rows = fetch_delivery_pct_via_full_bhavcopy(fetched_symbols, recent_dates)

    print("Fetching NIFTY50 benchmark...")
    bench_rows = fetch_benchmark_ohlcv("NIFTY 50", from_date, to_date)
    if bench_rows:
        write_ohlcv_csv(ohlcv_dir / "NIFTY50.csv", bench_rows)
    else:
        print("ERROR: benchmark fetch failed — scanner.py's RS ranking and "
              "sector/macro overlay stages need this file and will error "
              "without it.", file=sys.stderr)

    with open(out_dir / "universe.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "free_float_mcap_rank", "avg_daily_turnover_inr_cr", "sector"])
        w.writeheader()
        w.writerows(universe_rows)

    with open(out_dir / "delivery_pct.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "date", "delivery_pct"])
        w.writeheader()
        w.writerows(all_delivery_rows)

    if not all_delivery_rows:
        print("ERROR: delivery_pct.csv is empty after trying both stock_df and "
              "full-bhavcopy — every candidate will be dropped by the "
              "smart-money confirmation stage regardless of the "
              "require_bulk_block_deal_confirmation setting, since it also "
              "requires at least some delivery history per symbol. Check the "
              "full-bhavcopy warnings above.", file=sys.stderr)

    # No historical bulk/block deal source wired up (see module docstring) —
    # write headers only so scanner.py's CSV reader doesn't error.
    with open(out_dir / "bulk_block_deals.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "date", "buy_sell", "deal_type"])
        w.writeheader()

    print(f"Done: {len(universe_rows)}/{len(symbols_cfg['symbols'])} symbols written to {out_dir}")
    if len(universe_rows) < len(symbols_cfg["symbols"]):
        print("Some symbols were skipped — check the WARNING/ERROR lines above.", file=sys.stderr)


if __name__ == "__main__":
    main()
