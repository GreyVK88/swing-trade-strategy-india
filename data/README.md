# Data inputs

The scanner is source-agnostic: it reads local CSVs rather than calling any
specific vendor/broker API, since NSE/vendor access is usually behind auth
you control. Point the paths in `config/screening_config.yaml`'s `data:`
section at your own exports. Expected schemas below.

Run `python data/generate_sample_data.py` to generate small synthetic sample
data under `data/sample/` (matches these schemas) so you can run the scanner
out of the box. **It is not real market data** — replace it with your own
NSE bhavcopy / vendor / broker exports for actual use.

## `universe_csv`
One row per stock in your scan universe.

| column | type | notes |
|---|---|---|
| `symbol` | str | NSE/BSE ticker |
| `free_float_mcap_rank` | int | free-float market-cap rank (1 = largest) |
| `avg_daily_turnover_inr_cr` | float | 20D avg daily turnover, INR crores |
| `sector` | str | used by the sector exposure cap in Stage 6 |

## `ohlcv_dir`
One `{symbol}.csv` file per stock (plus the benchmark, see below), daily bars:

| column | type |
|---|---|
| `date` | `YYYY-MM-DD` |
| `open`, `high`, `low`, `close` | float |
| `volume` | int |

Needs at least ~210 trading days of history per symbol for the 200DMA +
base-detection logic to have enough data.

## `benchmark_ohlcv_csv`
Same OHLCV schema as above, for the Nifty 50 (or whichever benchmark you set
in `relative_strength.benchmark`).

## `bulk_block_deals_csv`
NSE bulk/block deal disclosures.

| column | type | notes |
|---|---|---|
| `symbol` | str | |
| `date` | `YYYY-MM-DD` | |
| `buy_sell` | `BUY` \| `SELL` | |
| `deal_type` | `BULK` \| `BLOCK` | not currently used to filter, kept for reference |

## `delivery_pct_csv`
Daily delivery percentage history (needed to compute the trailing 20D
average, not just the latest value).

| column | type |
|---|---|
| `symbol` | str |
| `date` | `YYYY-MM-DD` |
| `delivery_pct` | float (0–100) |

## Where to get real data
- NSE bhavcopy (daily OHLCV + delivery %): nseindia.com's historical data
  section publishes daily bhavcopy + security-wise delivery position files.
- Bulk/block deals: published daily on nseindia.com under Bulk Deals / Block
  Deals.
- Free-float market-cap rank: AMFI publishes a semi-annual large/mid/small
  cap classification list; alternatively derive it from a vendor feed.

None of this is wired up automatically — it's on you (or a scheduled job you
build) to export these into the CSV schemas above and refresh them
periodically.
