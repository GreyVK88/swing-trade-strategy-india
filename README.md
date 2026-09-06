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
├── .github/workflows/run-scanner.yml  # one-click "Run scan" via GitHub Actions
├── config/
│   └── screening_config.yaml   # all screening/sizing parameters, by cap tier + data file paths
├── scanner/
│   └── scanner.py               # the screening pipeline (all 6 stages implemented)
├── data/
│   ├── README.md                # CSV schemas expected by scanner.py
│   ├── generate_sample_data.py  # generates synthetic sample data to run against
│   └── sample/                  # generated sample data (gitignored except via generate script)
└── output/
    └── scan_results.csv         # written by each run
```

## Run it on GitHub (no local setup)

1. Go to the **Actions** tab → **"Run scanner"** in the left sidebar.
2. Click **"Run workflow"**, set your capital, leave "use sample data" checked.
3. Once it finishes (~30s), open the run and download the **scan-results**
   artifact from the bottom of the run's summary page — it's the same
   `scan_results.csv` you'd get locally.

Uncheck "use sample data" only if you've committed your own real CSVs under
`data/` following the schemas in `data/README.md` — the repo's `.gitignore`
excludes `data/sample/` by default, so real data needs its own path.

## Quickstart

```bash
pip install pyyaml
python data/generate_sample_data.py                                    # one-time: synthetic sample data
python scanner/scanner.py --config config/screening_config.yaml --capital 1000000
```

This runs against small synthetic sample data (not real prices) purely to
exercise the pipeline end-to-end. For real use, replace the files under
`data/sample/` — or better, point `config/screening_config.yaml`'s `data:`
section at your own exports — following the schemas in `data/README.md`.

## Status

All six pipeline stages are implemented (cap-tier/liquidity filtering, VCP
detection, smart-money confirmation, RS ranking, fixed-fractional sizing,
sector/macro overlay) and run end-to-end against the bundled sample data.
The VCP detection is a heuristic swing-high/low based implementation, not a
reference implementation — tune `config/screening_config.yaml`'s `vcp:`
section and validate against your own charts/backtests before trusting its
output. No live data source is wired in (see `data/README.md`); this reads
local CSVs you refresh yourself from NSE bhavcopy or a vendor feed.

## Disclaimer

This repository is a personal research and systematic-trading tool. Nothing here is investment advice. Past patterns (including VCP setups) are not guarantees of future performance. Always validate signals and size positions according to your own risk tolerance.
