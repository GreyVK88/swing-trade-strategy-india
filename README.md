# swing-trade-strategy-india

A cross-cap (large/mid/small cap) swing trading strategy for Indian equities (NSE/BSE), built around Volatility Contraction Pattern (VCP) setups confirmed by institutional/smart-money flow.

> Personal research project under Grey Capital Research. Not investment advice — for personal strategy tracking and backtesting only.

## Strategy Overview

The system scans the Nifty 500 (or a custom universe) across three cap tiers and flags stocks forming a VCP base with corroborating institutional participation, then sizes positions using fixed-fractional risk.

### 1. Cap-Specific Universe Filtering
Stocks are bucketed into large, mid, and small cap tiers (by free-float market cap, using NSE/AMFI cap classification cutoffs) before screening, since VCP behavior, liquidity, and volatility norms differ meaningfully by tier. Each tier can carry its own thresholds (see `config/screening_config.yaml`).

### 2. VCP (Volatility Contraction Pattern) Technical Setup
Core technical filter, adapted from Mark Minervini's VCP methodology:
- Identify a base with a series of progressively tighter price contractions (each pullback shallower than the last).
- Confirm volume dry-up on the final contraction(s) relative to the base's average volume.
- Flag a defined pivot/buy point (typically the base's resistance high) with a tight consolidation immediately below it.
- Require the stock to be in a broader uptrend (above key moving averages, e.g., 50/150/200 DMA in the right alignment).

### 3. Smart-Money / Institutional Confirmation
Technical setups are cross-checked against NSE bulk deal and block deal disclosures, plus delivery percentage data, to filter for setups with real institutional accumulation rather than pure retail noise:
- Recent bulk/block deal activity in the name (buy-side, from the disclosed lists).
- Elevated delivery percentage on breakout/basing days versus the stock's trailing average.

### 4. Relative Strength Ranking vs Nifty 50
Candidates are ranked by relative strength against the Nifty 50 benchmark (and optionally their own cap-tier index) over multiple lookback windows, to prioritize leadership names over laggards.

### 5. Fixed-Fractional Risk Sizing
Position size is derived from a fixed percentage of capital risked per trade, using the distance from entry to stop-loss (typically below the VCP pivot / most recent contraction low) to size the position — not a fixed number of shares or fixed capital per trade.

### 6. Sector / Macro Overlay
A top-down filter/tilt layered on top of the bottom-up scan:
- Sector rotation context (which sectors are showing relative strength).
- Broader macro regime checks (index trend, breadth, volatility regime) used to scale overall exposure up or down rather than trading every signal at full size regardless of environment.

## Repo Structure

```
swing-trade-strategy-india/
├── README.md
├── .github/workflows/run-scanner.yml  # one-click "Run scan" via GitHub Actions (sample or real NSE data)
├── config/
│   ├── screening_config.yaml       # sample-data config (all params, by cap tier)
│   └── screening_config.real.yaml  # same params, pointed at data/real/ instead
├── scanner/
│   └── scanner.py               # the screening pipeline (all 6 stages implemented)
├── data/
│   ├── README.md                # CSV schemas expected by scanner.py
│   ├── generate_sample_data.py  # generates synthetic sample data to run against
│   ├── nse_symbols.yaml         # symbol list + cap tier/sector for real-data fetch (user-maintained)
│   ├── fetch_nse_data.py        # fetches real OHLCV + delivery % via jugaad-data
│   ├── sample/                  # generated sample data (gitignored)
│   └── real/                    # fetched real data (gitignored)
└── output/
    └── scan_results.csv         # written by each run
```

## Run it on GitHub (no local setup)

1. Go to the **Actions** tab → **"Run scanner"** in the left sidebar.
2. Click **"Run workflow"**, set your capital, and choose a data source:
   - **`sample`** — synthetic demo data, generated fresh each run (not real prices).
   - **`real_nse`** — fetches real OHLCV + delivery % for the symbols in
     `data/nse_symbols.yaml` via [jugaad-data](https://github.com/jugaad-py/jugaad-data).
     See the caveats in `data/README.md` before trusting its output — notably,
     no bulk/block deal data is fetched, and cap-tier ranks are hand-maintained.
3. Once it finishes, open the run and download from its summary page:
   - **scan-results** — the `scan_results.csv` output
   - **nse-raw-data** (real_nse runs only) — the fetched CSVs, useful for
     checking the data actually looks right (or debugging if it doesn't)

## Quickstart

```bash
pip install pyyaml
python data/generate_sample_data.py                                    # one-time: synthetic sample data
python scanner/scanner.py --config config/screening_config.yaml --capital 1000000
```

This runs against small synthetic sample data (not real prices) purely to
exercise the pipeline end-to-end. For real data locally:

```bash
pip install jugaad-data pandas pyyaml
python data/fetch_nse_data.py --symbols-config data/nse_symbols.yaml --out-dir data/real
python scanner/scanner.py --config config/screening_config.real.yaml --capital 1000000
```

Or use the `real_nse` option in the GitHub Actions workflow (see below) —
same result, no local setup. Either way, read `data/README.md`'s caveats on
what the real-data fetch does and doesn't cover before trusting the output.

## Status

All six pipeline stages are implemented (cap-tier/liquidity filtering, VCP
detection, smart-money confirmation, RS ranking, fixed-fractional sizing,
sector/macro overlay) and verified end-to-end against synthetic sample data.
The VCP detection is a heuristic swing-high/low based implementation, not a
reference implementation — tune `config/screening_config.yaml`'s `vcp:`
section and validate against your own charts/backtests before trusting its
output.

Real data (OHLCV + delivery %) can be fetched via `data/fetch_nse_data.py`
using jugaad-data — **but this has not been verified against a live NSE
pull**, since the environment that built it can't reach nseindia.com. The
first real test of it is whichever run actually executes it (local or the
GitHub Action) — check the logged column names / `nse-raw-data` artifact if
output looks empty or wrong. Bulk/block deal data and market-cap ranking
are not fetched automatically; see `data/README.md`.

## Disclaimer

This repository is a personal research and systematic-trading tool. Nothing here is investment advice. Past patterns (including VCP setups) are not guarantees of future performance. Always validate signals and size positions according to your own risk tolerance.
