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

## Automated fetch via jugaad-data (partial)

`data/fetch_nse_data.py` uses the [jugaad-data](https://github.com/jugaad-py/jugaad-data)
library to fetch real OHLCV + delivery % history for the symbols listed in
`data/nse_symbols.yaml`, writing them into `data/real/` in the schemas above.
It's wired into the GitHub Actions workflow (`data_source: real_nse`).

**What it covers:** `universe.csv`, `ohlcv_dir` (per-symbol + NIFTY50
benchmark), `delivery_pct.csv`.

**What it does NOT cover — you still need to handle these separately:**
- **`bulk_block_deals_csv`** — jugaad-data has no historical bulk/block deal
  download function. The fetch script writes an empty file (headers only).
  `config/screening_config.real.yaml` sets
  `require_bulk_block_deal_confirmation: false` to match — without a real
  deal data source, requiring deal confirmation would reject every candidate,
  every run. If you want this signal, you'll need to source it separately
  (NSE publishes Bulk Deals / Block Deals reports daily) and either merge it
  in or flip that config flag back to `true` once you do.
- **`free_float_mcap_rank`** — jugaad-data has no market-cap ranking
  endpoint. You maintain this by hand in `data/nse_symbols.yaml` (AMFI
  publishes an official classification list twice a year if you want to be
  precise).

**Honesty note:** this fetch script was written without the ability to test
against live NSE data (Claude's sandbox network doesn't reach nseindia.com),
so its column-name matching is defensive/probing rather than verified. The
GitHub Action logs the raw column names jugaad-data returns on every run —
if a run comes back with unexpectedly empty output, check those logs first;
the fetch script prints exactly what it found and didn't find, and the
workflow uploads the raw fetched CSVs (`nse-raw-data` artifact) for
inspection.
