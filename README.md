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
├── config/
│   └── screening_config.yaml   # all screening/sizing parameters, by cap tier
└── scanner/
    └── scanner.py               # scaffold for the screening pipeline
```

## Status

Scaffold stage — screening parameters and scanner pipeline are stubbed out and need to be filled in / backtested with real data before live use.

## Disclaimer

This repository is a personal research and systematic-trading tool. Nothing here is investment advice. Past patterns (including VCP setups) are not guarantees of future performance. Always validate signals and size positions according to your own risk tolerance.
