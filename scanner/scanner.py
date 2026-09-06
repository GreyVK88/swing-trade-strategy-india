"""
scanner.py

VCP-based cross-cap swing trading scanner for Indian equities.
Pipeline stages mirror the strategy README:
  1. Universe filtering (cap-tier + liquidity)
  2. VCP technical setup detection
  3. Smart-money confirmation (bulk/block deals, delivery %)
  4. Relative strength ranking vs Nifty 50 / cap-tier index
  5. Position sizing (fixed-fractional risk)
  6. Sector / macro overlay (exposure scaling)

This scanner is source-agnostic: it reads local CSVs (see data/README.md for
schemas) rather than calling any specific vendor/broker API directly, since
NSE/vendor terminals are usually behind auth you control. Point config['data']
at your own exports (NSE bhavcopy, broker API dump, etc.) to run on real data;
`data/generate_sample_data.py` produces small synthetic sample data to run
against out of the box.

Usage (from repo root):
    python data/generate_sample_data.py   # first time only
    python scanner/scanner.py --config config/screening_config.yaml --capital 1000000
"""

import argparse
import csv
import dataclasses
from collections import defaultdict
from pathlib import Path
from typing import Optional

import yaml


@dataclasses.dataclass
class Candidate:
    symbol: str
    cap_tier: str
    sector: Optional[str] = None
    pivot_price: Optional[float] = None
    stop_loss: Optional[float] = None
    rs_percentile: Optional[float] = None
    delivery_pct: Optional[float] = None
    bulk_block_deal_flag: bool = False
    suggested_position_size: Optional[float] = None
    _volume_dryup_ratio: Optional[float] = None
    _rs_score: Optional[float] = None


# ---------------------------------------------------------------------------
# Small local helpers (no pandas/numpy dependency, kept dependency-light)
# ---------------------------------------------------------------------------

def load_config(config_path: str) -> dict:
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _read_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _load_ohlcv(path: Path) -> list[dict]:
    """Returns rows sorted ascending by date, numeric fields cast to float."""
    rows = _read_csv(path)
    for r in rows:
        r["open"] = float(r["open"])
        r["high"] = float(r["high"])
        r["low"] = float(r["low"])
        r["close"] = float(r["close"])
        r["volume"] = float(r["volume"])
    rows.sort(key=lambda r: r["date"])
    return rows


def _sma(values: list[float], window: int) -> list[Optional[float]]:
    out = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= window:
            running -= values[i - window]
        if i >= window - 1:
            out[i] = running / window
    return out


def _swing_points(closes: list[float], window: int = 5) -> list[tuple[int, str, float]]:
    """Local extrema detector: a point is a swing high/low if it is the
    max/min within +/- `window` bars. Returns [(index, 'H'|'L', price), ...]
    in chronological order."""
    n = len(closes)
    points = []
    for i in range(window, n - window):
        seg = closes[i - window: i + window + 1]
        if closes[i] == max(seg) and closes[i] > closes[i - 1]:
            points.append((i, "H", closes[i]))
        elif closes[i] == min(seg) and closes[i] < closes[i - 1]:
            points.append((i, "L", closes[i]))
    # collapse consecutive same-type points, keeping the more extreme one
    collapsed: list[tuple[int, str, float]] = []
    for p in points:
        if collapsed and collapsed[-1][1] == p[1]:
            if (p[1] == "H" and p[2] >= collapsed[-1][2]) or (p[1] == "L" and p[2] <= collapsed[-1][2]):
                collapsed[-1] = p
        else:
            collapsed.append(p)
    return collapsed


# ---------------------------------------------------------------------------
# Stage 0/1: Universe + cap-tier / liquidity filtering
# ---------------------------------------------------------------------------

def load_universe(config: dict) -> list[dict]:
    """Stage 0: Load the raw universe with cap-rank / turnover / sector info.

    Expects data['universe_csv'] with columns:
        symbol, free_float_mcap_rank, avg_daily_turnover_inr_cr, sector
    """
    path = config["data"]["universe_csv"]
    rows = _read_csv(path)
    for r in rows:
        r["free_float_mcap_rank"] = int(r["free_float_mcap_rank"])
        r["avg_daily_turnover_inr_cr"] = float(r["avg_daily_turnover_inr_cr"])
    return rows


def filter_by_cap_tier_and_liquidity(universe: list[dict], config: dict) -> dict[str, list[dict]]:
    """Stage 1: Bucket symbols into large/mid/small cap tiers per
    config['universe']['cap_tiers'], and drop anything below each tier's
    min_avg_daily_turnover_inr_cr.
    """
    tier_cfg = config["universe"]["cap_tiers"]
    tiered: dict[str, list[dict]] = defaultdict(list)
    for row in universe:
        rank = row["free_float_mcap_rank"]
        for tier_name, tcfg in tier_cfg.items():
            lo, hi = tcfg["free_float_mcap_rank"]
            if lo <= rank <= hi:
                if row["avg_daily_turnover_inr_cr"] >= tcfg["min_avg_daily_turnover_inr_cr"]:
                    tiered[tier_name].append(row)
                break
    return dict(tiered)


# ---------------------------------------------------------------------------
# Stage 2: VCP detection
# ---------------------------------------------------------------------------

def detect_vcp_setups(tiered_universe: dict[str, list[dict]], config: dict) -> list[Candidate]:
    """Stage 2: For each symbol, detect a VCP base using config['vcp'] params.

    Heuristic (not a textbook-perfect implementation, but a real one):
      - locate swing highs/lows over the trailing window
      - take the alternating H-L-H-L... legs since the most recent swing high
        before the base as "contractions"
      - each contraction's % depth must be <= tightening_ratio * previous
        contraction's depth, within [min_contractions, max_contractions]
      - final contraction depth <= final_contraction_max_depth_pct
      - volume during the final contraction leg must have dried up vs the
        base's average volume by >= volume_dry_up_ratio
      - trend filter: 50/150/200 DMA alignment and price above them
      - price must be within pivot_buffer_pct of the base high (the pivot)
    """
    vcfg = config["vcp"]
    ohlcv_dir = Path(config["data"]["ohlcv_dir"])
    candidates: list[Candidate] = []

    for tier, rows in tiered_universe.items():
        for row in rows:
            symbol = row["symbol"]
            path = ohlcv_dir / f"{symbol}.csv"
            if not path.exists():
                continue
            bars = _load_ohlcv(path)
            if len(bars) < 210:
                continue  # not enough history for 200DMA + base

            closes = [b["close"] for b in bars]
            volumes = [b["volume"] for b in bars]
            sma50 = _sma(closes, 50)
            sma150 = _sma(closes, 150)
            sma200 = _sma(closes, 200)

            tf = vcfg["trend_filter"]
            last = -1
            if sma200[last] is None:
                continue
            if tf.get("require_above_50dma") and closes[last] < sma50[last]:
                continue
            if tf.get("require_above_150dma") and closes[last] < sma150[last]:
                continue
            if tf.get("require_above_200dma") and closes[last] < sma200[last]:
                continue
            if tf.get("require_ma_alignment") and not (sma50[last] > sma150[last] > sma200[last]):
                continue

            swings = _swing_points(closes, window=5)
            if len(swings) < 3:
                continue

            # Build legs (H->L = contraction down) walking from the most
            # recent swing backwards, stopping as soon as tightening breaks.
            # This finds *just* the current base rather than validating
            # tightening across the whole price history (an earlier, looser
            # pullback from a prior uptrend shouldn't disqualify a valid base
            # that formed after it).
            legs = []  # will be newest-first while collecting
            prev_depth = None
            for j in range(len(swings) - 1, 0, -1):
                idx2, type2, price2 = swings[j]
                idx1, type1, price1 = swings[j - 1]
                if type1 != "H" or type2 != "L":
                    continue
                depth_pct = (price1 - price2) / price1 * 100
                if prev_depth is not None and prev_depth > depth_pct * vcfg["contraction_tightening_ratio"] + 1e-9:
                    # this older leg isn't loose enough relative to the newer
                    # one already collected -> tightening breaks -> base ends here
                    break
                legs.append({"start": idx1, "end": idx2, "high": price1, "low": price2, "depth_pct": depth_pct})
                prev_depth = depth_pct
                if len(legs) >= vcfg["max_contractions"]:
                    break
            legs.reverse()  # chronological order, oldest contraction first

            if not (vcfg["min_contractions"] <= len(legs) <= vcfg["max_contractions"]):
                continue

            final_leg = legs[-1]
            if final_leg["depth_pct"] > vcfg["final_contraction_max_depth_pct"]:
                continue

            base_start = legs[0]["start"]
            base_vol_avg = sum(volumes[base_start:final_leg["start"]]) / max(1, final_leg["start"] - base_start)
            final_vol_avg = sum(volumes[final_leg["start"]:final_leg["end"] + 1]) / max(1, final_leg["end"] - final_leg["start"] + 1)
            dryup_ratio = final_vol_avg / base_vol_avg if base_vol_avg else 1.0
            if dryup_ratio > vcfg["volume_dry_up_ratio"]:
                continue

            # Pivot = the most recent contraction's high (the resistance the
            # stock is currently basing under), not the tallest historical
            # high in the base — bases often have slightly declining highs.
            pivot = legs[-1]["high"]
            dist_to_pivot_pct = (pivot - closes[last]) / pivot * 100
            if closes[last] < pivot and dist_to_pivot_pct > vcfg["pivot_buffer_pct"]:
                continue
            # (Price at/above pivot, or within pivot_buffer_pct below it, both pass.)

            stop_loss = final_leg["low"]

            candidates.append(Candidate(
                symbol=symbol,
                cap_tier=tier,
                sector=row.get("sector"),
                pivot_price=round(pivot, 2),
                stop_loss=round(stop_loss, 2),
                _volume_dryup_ratio=round(dryup_ratio, 3),
            ))

    return candidates


# ---------------------------------------------------------------------------
# Stage 3: Smart-money confirmation
# ---------------------------------------------------------------------------

def apply_smart_money_confirmation(candidates: list[Candidate], config: dict) -> list[Candidate]:
    """Stage 3: Cross-check candidates against bulk/block deal disclosures and
    delivery percentage data.

    Expects:
      data['bulk_block_deals_csv']: symbol, date, buy_sell, deal_type
      data['delivery_pct_csv']: symbol, date, delivery_pct  (daily history)
    """
    scfg = config["smart_money_confirmation"]
    deals = _read_csv(config["data"]["bulk_block_deals_csv"])
    delivery = _read_csv(config["data"]["delivery_pct_csv"])
    for r in delivery:
        r["delivery_pct"] = float(r["delivery_pct"])

    deals_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for d in deals:
        deals_by_symbol[d["symbol"]].append(d)

    delivery_by_symbol: dict[str, list[dict]] = defaultdict(list)
    for d in delivery:
        delivery_by_symbol[d["symbol"]].append(d)
    for sym in delivery_by_symbol:
        delivery_by_symbol[sym].sort(key=lambda r: r["date"])

    survivors = []
    for c in candidates:
        sym_deals = deals_by_symbol.get(c.symbol, [])
        recent_deals = sym_deals[-scfg["bulk_block_deal_lookback_days"]:]
        buys = sum(1 for d in recent_deals if d["buy_sell"].upper() == "BUY")
        sells = sum(1 for d in recent_deals if d["buy_sell"].upper() == "SELL")
        c.bulk_block_deal_flag = bool(recent_deals) and (not scfg["require_net_buy_side"] or buys > sells)

        hist = delivery_by_symbol.get(c.symbol, [])
        if not hist:
            continue
        latest_pct = hist[-1]["delivery_pct"]
        trailing = hist[-21:-1] if len(hist) > 1 else hist
        trailing_avg = sum(r["delivery_pct"] for r in trailing) / len(trailing)
        c.delivery_pct = round(latest_pct, 1)

        meets_min = latest_pct >= scfg["min_delivery_pct"]
        meets_multiplier = trailing_avg == 0 or (latest_pct / trailing_avg) >= scfg["delivery_pct_vs_avg_multiplier"]

        if meets_min and meets_multiplier and c.bulk_block_deal_flag:
            survivors.append(c)

    return survivors


# ---------------------------------------------------------------------------
# Stage 4: Relative strength ranking
# ---------------------------------------------------------------------------

def _period_return(closes: list[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    return (closes[-1] - closes[-1 - lookback]) / closes[-1 - lookback] * 100


def rank_relative_strength(candidates: list[Candidate], config: dict) -> list[Candidate]:
    """Stage 4: Rank candidates by relative strength vs the Nifty 50 across
    config['relative_strength']['lookback_windows_days'], using an IBD-style
    recency-weighted composite, then keep only the top percentile.
    """
    rcfg = config["relative_strength"]
    ohlcv_dir = Path(config["data"]["ohlcv_dir"])
    bench_bars = _load_ohlcv(Path(config["data"]["benchmark_ohlcv_csv"]))
    bench_closes = [b["close"] for b in bench_bars]

    windows = rcfg["lookback_windows_days"]
    # More weight on the most recent window (IBD-style), least on the oldest.
    weights = list(range(len(windows), 0, -1))
    weight_sum = sum(weights)

    scored: list[Candidate] = []
    for c in candidates:
        path = ohlcv_dir / f"{c.symbol}.csv"
        bars = _load_ohlcv(path)
        closes = [b["close"] for b in bars]

        rs_components = []
        for w, lb in zip(weights, windows):
            stock_ret = _period_return(closes, lb)
            bench_ret = _period_return(bench_closes, lb)
            if stock_ret is None or bench_ret is None:
                continue
            rs_components.append(w * (stock_ret - bench_ret))
        if not rs_components:
            continue
        c._rs_score = sum(rs_components) / weight_sum
        scored.append(c)

    if not scored:
        return []

    scored.sort(key=lambda c: c._rs_score)
    n = len(scored)
    for i, c in enumerate(scored):
        c.rs_percentile = round((i + 1) / n * 100, 1)

    min_pct = rcfg["min_percentile_rank"]
    return [c for c in scored if c.rs_percentile >= min_pct]


# ---------------------------------------------------------------------------
# Stage 5: Position sizing
# ---------------------------------------------------------------------------

def size_positions(candidates: list[Candidate], config: dict, account_capital: float) -> list[Candidate]:
    """Stage 5: Fixed-fractional position sizing based on
    config['position_sizing']['risk_per_trade_pct_of_capital'] and the
    distance between pivot_price and stop_loss, capped by
    max_position_pct_of_capital.
    """
    pcfg = config["position_sizing"]
    risk_amount = account_capital * pcfg["risk_per_trade_pct_of_capital"] / 100
    max_position_value = account_capital * pcfg["max_position_pct_of_capital"] / 100

    for c in candidates:
        risk_per_share = c.pivot_price - c.stop_loss
        if risk_per_share <= 0:
            c.suggested_position_size = 0.0
            continue
        shares = risk_amount / risk_per_share
        position_value = shares * c.pivot_price
        c.suggested_position_size = round(min(position_value, max_position_value), 2)

    candidates = [c for c in candidates if c.suggested_position_size and c.suggested_position_size > 0]
    max_positions = pcfg.get("max_concurrent_positions")
    if max_positions and len(candidates) > max_positions:
        # keep the strongest RS names when trimming to the concurrent-position cap
        candidates.sort(key=lambda c: c.rs_percentile or 0, reverse=True)
        candidates = candidates[:max_positions]
    return candidates


# ---------------------------------------------------------------------------
# Stage 6: Sector / macro overlay
# ---------------------------------------------------------------------------

def _compute_breadth(config: dict) -> float:
    """% of symbols in the universe currently trading above their own 200DMA.
    Proxy for the config['sector_macro_overlay']['regime_filters']['breadth_threshold_pct'] check."""
    ohlcv_dir = Path(config["data"]["ohlcv_dir"])
    universe = load_universe(config)
    above, total = 0, 0
    for row in universe:
        path = ohlcv_dir / f"{row['symbol']}.csv"
        if not path.exists():
            continue
        bars = _load_ohlcv(path)
        closes = [b["close"] for b in bars]
        if len(closes) < 200:
            continue
        sma200 = _sma(closes, 200)[-1]
        if sma200 is None:
            continue
        total += 1
        if closes[-1] >= sma200:
            above += 1
    return (above / total * 100) if total else 0.0


def apply_sector_macro_overlay(candidates: list[Candidate], config: dict, account_capital: float) -> list[Candidate]:
    """Stage 6: Scale suggested_position_size by the current regime
    (risk_on / neutral / risk_off multiplier) and enforce
    max_sector_exposure_pct (of total account capital) across the candidate
    list.
    """
    ocfg = config["sector_macro_overlay"]
    rf = ocfg["regime_filters"]
    es = ocfg["exposure_scaling"]

    bench_bars = _load_ohlcv(Path(config["data"]["benchmark_ohlcv_csv"]))
    bench_closes = [b["close"] for b in bench_bars]
    bench_sma200 = _sma(bench_closes, 200)[-1]
    index_above_200dma = bench_sma200 is not None and bench_closes[-1] >= bench_sma200

    breadth = _compute_breadth(config)

    if rf.get("index_above_200dma_required") and not index_above_200dma:
        regime = "risk_off"
    elif breadth >= rf["breadth_threshold_pct"]:
        regime = "risk_on"
    elif breadth >= rf["breadth_threshold_pct"] * 0.6:
        regime = "neutral"
    else:
        regime = "risk_off"

    multiplier = {
        "risk_on": es["risk_on_multiplier"],
        "neutral": es["neutral_multiplier"],
        "risk_off": es["risk_off_multiplier"],
    }[regime]

    for c in candidates:
        c.suggested_position_size = round(c.suggested_position_size * multiplier, 2)

    # Enforce per-sector exposure cap, expressed as % of total account
    # capital: if a sector's total sizing exceeds that cap, scale every
    # position in that sector down proportionally.
    total_by_sector: dict[str, float] = defaultdict(float)
    for c in candidates:
        total_by_sector[c.sector or "Unknown"] += c.suggested_position_size

    sector_cap_value = account_capital * ocfg["max_sector_exposure_pct"] / 100
    for c in candidates:
        sector_total = total_by_sector[c.sector or "Unknown"]
        if sector_total > sector_cap_value:
            c.suggested_position_size = round(c.suggested_position_size * (sector_cap_value / sector_total), 2)

    return candidates


# ---------------------------------------------------------------------------
# Output + orchestration
# ---------------------------------------------------------------------------

def write_output(candidates: list[Candidate], config: dict) -> None:
    out_cfg = config["output"]
    out_path = Path(out_cfg["path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    columns = out_cfg["include_columns"]
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for c in candidates:
            row = dataclasses.asdict(c)
            writer.writerow({col: row.get(col) for col in columns})


def run(config_path: str, account_capital: float) -> None:
    config = load_config(config_path)

    universe = load_universe(config)
    tiered_universe = filter_by_cap_tier_and_liquidity(universe, config)
    candidates = detect_vcp_setups(tiered_universe, config)
    candidates = apply_smart_money_confirmation(candidates, config)
    candidates = rank_relative_strength(candidates, config)
    candidates = size_positions(candidates, config, account_capital)
    candidates = apply_sector_macro_overlay(candidates, config, account_capital)

    write_output(candidates, config)
    print(f"Scan complete: {len(candidates)} candidates written to {config['output']['path']}")
    for c in candidates:
        print(f"  {c.symbol:<12} {c.cap_tier:<10} pivot={c.pivot_price} stop={c.stop_loss} "
              f"RS%={c.rs_percentile} size={c.suggested_position_size}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VCP cross-cap swing trade scanner")
    parser.add_argument(
        "--config",
        default="config/screening_config.yaml",
        help="Path to screening_config.yaml (default assumes you run this from the repo root)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        required=True,
        help="Total account capital (INR) used for position sizing",
    )
    args = parser.parse_args()
    run(args.config, args.capital)
