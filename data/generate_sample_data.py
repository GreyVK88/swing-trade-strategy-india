"""
generate_sample_data.py

Generates small, synthetic sample data under data/sample/ so the scanner can
be run end-to-end without any external data source. NOT real market data —
for demonstrating / testing the pipeline only. Replace with real NSE
bhavcopy / vendor data for actual use (see data/README.md).

Run: python data/generate_sample_data.py
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).parent / "sample"
OHLCV_DIR = ROOT / "ohlcv"
OHLCV_DIR.mkdir(parents=True, exist_ok=True)

N_DAYS = 260  # ~1 trading year


def gen_dates(n):
    d = datetime(2025, 9, 1)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DATES = gen_dates(N_DAYS)


def write_ohlcv(symbol: str, closes: list[float], volumes: list[int]):
    path = OHLCV_DIR / f"{symbol}.csv"
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        prev_close = closes[0]
        for dt, c, v in zip(DATES, closes, volumes):
            o = prev_close * (1 + random.uniform(-0.004, 0.004))
            h = max(o, c) * (1 + random.uniform(0.0, 0.006))
            l = min(o, c) * (1 - random.uniform(0.0, 0.006))
            w.writerow([dt.strftime("%Y-%m-%d"), round(o, 2), round(h, 2), round(l, 2), round(c, 2), v])
            prev_close = c


def random_walk(n, start, drift=0.0004, vol=0.014):
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1 + random.gauss(drift, vol)))
    return out


def _lerp_path(a, b, steps, noise=0.003):
    out = []
    for t in range(1, steps + 1):
        base = a + (b - a) * t / steps
        out.append(base * (1 + random.uniform(-noise, noise)))
    return out


def vcp_pattern(n, start):
    """Uptrend into a base of progressively tighter contractions, with each
    recovery returning to near the same (slightly declining) resistance
    level, ending near the pivot with a volume dry-up on the final leg.

    Returns (prices, final_leg_index_range) so the caller can dry up volume
    precisely on the final contraction rather than guessing at a fraction of
    the total series.
    """
    up_len = int(n * 0.45)
    base_len = n - up_len
    prices = random_walk(up_len, start, drift=0.0035, vol=0.012)
    pivot = prices[-1]
    contractions = [0.18, 0.11, 0.06]  # progressively tighter, ratio ~0.6-0.65
    seg_len = base_len // (len(contractions) * 2)
    cur = pivot
    final_leg_range = None
    for i, depth in enumerate(contractions):
        resistance = pivot * (1 - 0.008 * i)
        trough = resistance * (1 - depth)
        down_start = len(prices)
        prices += _lerp_path(cur, trough, seg_len)
        down_end = len(prices) - 1
        if i == len(contractions) - 1:
            final_leg_range = (down_start, down_end)
        cur = trough
        recovery_target = pivot * (1 - 0.008 * (i + 1))
        prices += _lerp_path(cur, recovery_target, seg_len)
        cur = recovery_target
    while len(prices) < n:
        prices.append(prices[-1] * (1 + random.uniform(-0.002, 0.003)))
    return prices[:n], final_leg_range


def volumes_for(prices, final_leg_range=None, base_vol=800_000):
    n = len(prices)
    vols = [int(base_vol * random.uniform(0.7, 1.3)) for _ in range(n)]
    if final_leg_range:
        start, end = final_leg_range
        for i in range(start, min(end + 1, n)):
            vols[i] = int(vols[i] * 0.4)  # volume dry-up precisely on the final contraction
    return vols


# --- Symbols ---------------------------------------------------------------
# Two clean VCP setups (one large cap, one mid cap) so the scanner has
# candidates to find; the rest are plain random walks (should mostly get
# filtered out at the VCP or RS stage).
alpha_closes, alpha_final_leg = vcp_pattern(N_DAYS, 1200)
beta_closes, beta_final_leg = vcp_pattern(N_DAYS, 450)

symbols = {
    "ALPHACORP": ("large_cap", alpha_closes, alpha_final_leg),
    "BETAIND": ("mid_cap", beta_closes, beta_final_leg),
    "GAMMASTEEL": ("large_cap", random_walk(N_DAYS, 800), None),
    "DELTACHEM": ("mid_cap", random_walk(N_DAYS, 300, drift=-0.0005), None),
    "EPSILONTEX": ("small_cap", random_walk(N_DAYS, 120), None),
    "ZETAPOWER": ("small_cap", random_walk(N_DAYS, 90, drift=0.0002), None),
}

NIFTY50 = random_walk(N_DAYS, 24000, drift=0.0006, vol=0.008)
write_ohlcv("NIFTY50", NIFTY50, [0] * N_DAYS)

universe_rows = []
bulk_deal_rows = []
delivery_rows = []

mcap_rank = {"large_cap": 40, "mid_cap": 150, "small_cap": 320}
turnover = {"large_cap": 55.0, "mid_cap": 15.0, "small_cap": 6.0}
sectors = {
    "ALPHACORP": "Financials", "BETAIND": "Industrials", "GAMMASTEEL": "Metals",
    "DELTACHEM": "Chemicals", "EPSILONTEX": "Textiles", "ZETAPOWER": "Power",
}

for sym, (tier, closes, final_leg) in symbols.items():
    is_vcp = final_leg is not None
    vols = volumes_for(closes, final_leg) if is_vcp else volumes_for(closes)
    write_ohlcv(sym, closes, vols)

    universe_rows.append({
        "symbol": sym,
        "free_float_mcap_rank": mcap_rank[tier],
        "avg_daily_turnover_inr_cr": turnover[tier],
        "sector": sectors[sym],
    })

    last_date = DATES[-1].strftime("%Y-%m-%d")
    if is_vcp:
        bulk_deal_rows.append({"symbol": sym, "date": last_date, "buy_sell": "BUY", "deal_type": "BLOCK"})
        for i in range(20):
            d = (DATES[-1] - timedelta(days=i)).strftime("%Y-%m-%d")
            pct = 62 + random.uniform(-3, 6) if i < 3 else 48 + random.uniform(-5, 5)
            delivery_rows.append({"symbol": sym, "date": d, "delivery_pct": round(pct, 1)})
    else:
        for i in range(20):
            d = (DATES[-1] - timedelta(days=i)).strftime("%Y-%m-%d")
            delivery_rows.append({"symbol": sym, "date": d, "delivery_pct": round(38 + random.uniform(-5, 5), 1)})

with open(ROOT / "universe.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["symbol", "free_float_mcap_rank", "avg_daily_turnover_inr_cr", "sector"])
    w.writeheader()
    w.writerows(universe_rows)

with open(ROOT / "bulk_block_deals.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["symbol", "date", "buy_sell", "deal_type"])
    w.writeheader()
    w.writerows(bulk_deal_rows)

with open(ROOT / "delivery_pct.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["symbol", "date", "delivery_pct"])
    w.writeheader()
    w.writerows(delivery_rows)

print(f"Sample data written under {ROOT}")
