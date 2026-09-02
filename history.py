"""
Historical cross prices — step 3 of the cross-price tool.

Walks Chainlink round history via getRoundData(roundId) (batched eth_call),
composes the same USD legs as cross.py, and produces an as-of stepwise
cross-price series plus daily closes.

    python history.py sUSDS/USDT --days 30      # daily table
    python history.py syrupUSDT/GHO --days 90 --json

Proxy round ids encode the aggregator phase: roundId = phaseId << 64 | round.
We walk the current phase backwards in batched windows until the window start
is covered; if a phase runs out first (aggregator upgrade), the previous
phase's newest round is found by exponential + binary search and the walk
continues there. Rounds are immutable, so everything fetched is cached on
disk (.cache/) and reused across runs.
"""

import datetime
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from feeds import (FEEDS, SEL_CONVERT_TO_ASSETS, SEL_LATEST_ROUND, SEL_PRICE,
                   latest_block, read_feed, read_source, rpc_batch,
                   _decode_int256)
from cross import legs, methods, parse_spec, PriceError

SEL_GET_ROUND = "0x9a6fc8f5"  # getRoundData(uint80)
PHASE_MASK = (1 << 64) - 1
WINDOW = 100       # rounds fetched per batch while walking back
MAX_WINDOWS = 600  # runaway guard (60k rounds; ETH market feeds do ~5-20/day)
SEC_PER_BLOCK = {"ethereum": 12.05}  # for history="blocks" block estimation
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")


# ---------------------------------------------------------------- round cache

def _cache_path(name):
    return os.path.join(CACHE_DIR, re.sub(r"[^A-Za-z0-9]+", "_", name) + ".json")


def _load_cache(name):
    """Round cache: proxy round id -> (raw, updated_at), or None for rounds
    probed and found invalid (negative cache — saves re-probing phase ends)."""
    try:
        with open(_cache_path(name)) as f:
            j = json.load(f)
        return {int(k): (tuple(v) if v else None) for k, v in j.items()}
    except Exception:
        return {}


def _save_cache(name, rounds):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(name), "w") as f:
        json.dump({str(k): (list(v) if v else None) for k, v in rounds.items()}, f)


# ------------------------------------------------------------- round fetching

def _round_call(address, round_id):
    return (address, SEL_GET_ROUND + f"{round_id:064x}")


def _decode_round(hexdata):
    """-> (raw_answer, updated_at) or None for empty/invalid rounds."""
    if not hexdata or len(hexdata) < 2 + 64 * 5:
        return None
    h = hexdata[2:]
    answer = _decode_int256(h[64:128])
    updated_at = int(h[192:256], 16)
    if updated_at == 0 or answer <= 0:
        return None
    return (answer, updated_at)


def _fetch_rounds(chain, address, round_ids, rounds):
    """Batch-fetch the given proxy round ids into `rounds` (invalid -> None)."""
    todo = [rid for rid in round_ids if rid not in rounds]
    if todo:
        results = rpc_batch(chain, [_round_call(address, rid) for rid in todo])
        for rid, res in zip(todo, results):
            rounds[rid] = _decode_round(res)


def _phase_max_round(chain, address, phase, rounds):
    """Newest valid round of an older phase. Batched: one round trip probes
    powers of two, then a 16-ary search narrows the bracket (~5 round trips
    instead of ~30 sequential calls)."""
    def valid(agg_round):
        rid = (phase << 64) | agg_round
        if rid not in rounds:
            _fetch_rounds(chain, address, [rid], rounds)
        return rounds[rid] is not None

    probes = [1 << i for i in range(25)]  # up to ~33M rounds, beyond any real phase
    _fetch_rounds(chain, address, [(phase << 64) | p for p in probes], rounds)
    if not valid(1):
        return 0
    lo = max(p for p in probes if valid(p))
    hi = lo * 2
    while hi - lo > 1:
        step = max(1, (hi - lo) // 16)
        cand = list(range(lo + step, hi, step))
        _fetch_rounds(chain, address, [(phase << 64) | c for c in cand], rounds)
        for c in cand:
            if valid(c):
                lo = c
            else:
                hi = c
                break
    return lo


def _trim(points, cutoff_ts):
    """Sorted points -> those inside the window plus one anchor before it."""
    anchor = None
    trimmed = []
    for ts, val in points:
        if ts <= cutoff_ts:
            anchor = (ts, val)
        else:
            trimmed.append((ts, val))
    return ([anchor] if anchor else []) + trimmed


def _block_sampled(name, cutoff_ts):
    """History for sources that keep no round history (history="blocks"):
    read the contract at one estimated past block per UTC day (archive
    eth_call), plus a live point. Samples are cached by their midnight ts."""
    f = FEEDS[name]
    chain, address = f["chain"], f["address"]
    cache = _load_cache(name)  # midnight ts -> (raw, value_ts) | None
    live = read_source(f)
    decimals = live["decimals"]
    reader = f.get("reader", "aggregator")

    utc = datetime.timezone.utc
    day = datetime.datetime.fromtimestamp(cutoff_ts, utc).date()
    today = datetime.datetime.now(utc).date()
    sample_ts = []
    while day <= today:
        sample_ts.append(int(datetime.datetime.combine(
            day, datetime.time(0, 0), utc).timestamp()))
        day += datetime.timedelta(days=1)

    todo = [t for t in sample_ts if t not in cache]
    if todo:
        num, now_ts = latest_block(chain)
        spb = SEC_PER_BLOCK.get(chain, 12.05)
        data = {"erc4626": SEL_CONVERT_TO_ASSETS + f"{10 ** decimals:064x}",
                "price": SEL_PRICE}.get(reader, SEL_LATEST_ROUND)
        calls = [(address, data,
                  hex(max(1, num - int((now_ts - t) / spb)))) for t in todo]
        for t, res in zip(todo, rpc_batch(chain, calls)):
            if reader == "aggregator":
                d = _decode_round(res)  # latestRoundData has the same layout
                if d:
                    cache[t] = (d[0], d[1])
                elif res and len(res) >= 2 + 64 * 4:
                    # NAV-style aggregator: updatedAt=0 -> stamp the sample time
                    raw = _decode_int256(res[2:][64:128])
                    cache[t] = (raw, t) if raw > 0 else None
                else:
                    cache[t] = None
            else:
                raw = int(res, 16) if res and res != "0x" else 0
                cache[t] = (raw, t) if raw > 0 else None
        _save_cache(name, cache)

    pts = {(ts, raw / 10 ** decimals) for v in cache.values() if v
           for raw, ts in [v]}
    pts.add((live["updated_at"], live["answer"]))
    return _trim(sorted(pts), cutoff_ts)


def feed_rounds(name, cutoff_ts):
    """As-of history for feed `name`: sorted [(updated_at, price_float), ...]
    covering cutoff_ts..now, plus one anchor round older than the cutoff so
    a value exists at the window start."""
    f = FEEDS[name]
    if f.get("history") == "blocks":
        return _block_sampled(name, cutoff_ts)
    chain, address = f["chain"], f["address"]
    rounds = _load_cache(name)          # proxy round id -> (raw, updated_at) | None
    fetched_before = len(rounds)

    latest = read_feed(chain, address)
    decimals = latest["decimals"]
    rounds[latest["round_id"]] = (latest["raw"], latest["updated_at"])

    phase = latest["round_id"] >> 64
    agg = latest["round_id"] & PHASE_MASK
    newest_ts = oldest_ts = latest["updated_at"]
    seen = 1          # valid rounds observed this walk (for the cadence estimate)
    budget = MAX_WINDOWS
    while oldest_ts > cutoff_ts and budget > 0:
        if agg <= 1:  # phase exhausted — drop to the previous phase
            if phase <= 1:
                break
            phase -= 1
            agg = _phase_max_round(chain, address, phase, rounds)
            if agg == 0:
                break
            agg += 1  # loop body below starts at agg-1
        # estimate rounds still needed from the observed update cadence and
        # fetch that many windows concurrently (ETH market feeds do dozens of
        # deviation-triggered rounds a day; one window at a time is too slow)
        if seen > 1 and newest_ts > oldest_ts:
            rate = seen / (newest_ts - oldest_ts)
            windows = max(1, int((oldest_ts - cutoff_ts) * rate / WINDOW) + 1)
        else:
            windows = 1
        windows = min(windows, 4, budget)  # gentle on public RPC rate limits
        lo = max(0, agg - 1 - WINDOW * windows)
        ids = [(phase << 64) | i for i in range(agg - 1, lo, -1)]
        if not ids:
            agg = 1
            continue
        chunks = [ids[i:i + WINDOW] for i in range(0, len(ids), WINDOW)]
        budget -= len(chunks)
        if len(chunks) == 1:
            _fetch_rounds(chain, address, chunks[0], rounds)
        else:
            with ThreadPoolExecutor(max_workers=len(chunks)) as ex:
                list(ex.map(lambda c: _fetch_rounds(chain, address, c, rounds), chunks))
        got = [rounds[r] for r in ids if rounds.get(r)]
        if got:
            seen += len(got)
            oldest_ts = min(oldest_ts, min(ts for _, ts in got))
        agg = ids[-1] & PHASE_MASK

    if len(rounds) != fetched_before:
        _save_cache(name, rounds)

    valid = sorted(v for v in rounds.values() if v is not None)
    points = sorted({(ts, raw / 10 ** decimals) for raw, ts in valid})
    return _trim(points, cutoff_ts)


# ------------------------------------------------------------- cross series

def cross_series(base, quote, days, base_method=None, quote_method=None):
    """As-of cross-price series over the last `days` days.
    Returns dict(pair, days, points=[[ts, price]...], daily=[[date, price]...],
    legs={feed name: leg info})."""
    cutoff = int(time.time()) - days * 86400
    base_legs = legs(base, base_method)
    quote_legs = legs(quote, quote_method)
    # a leg on both sides cancels exactly (multiplied then divided by the same
    # as-of value) — drop it instead of fetching it, e.g. ETH/USD in
    # cbETH:market / rETH:market
    common = Counter(base_legs) & Counter(quote_legs)
    base_legs = _drop(base_legs, common)
    quote_legs = _drop(quote_legs, common)
    names = list(dict.fromkeys(base_legs + quote_legs))

    result = {
        "pair": f"{base}/{quote}",
        "days": days,
        "base_method": base_method or (methods(base) or [None])[0],
        "quote_method": quote_method or (methods(quote) or [None])[0],
        "base_legs": base_legs,
        "quote_legs": quote_legs,
        "cancelled": sorted(common.elements()),
    }
    if not names:  # identical composition on both sides -> constant 1
        now = int(time.time())
        result.update(points=[[now, 1.0]],
                      daily=[[datetime.datetime.now(datetime.timezone.utc)
                              .date().isoformat(), 1.0]],
                      legs={})
        return result

    with ThreadPoolExecutor(max_workers=len(names)) as ex:  # one walk per leg
        futures = {n: ex.submit(feed_rounds, n, cutoff) for n in names}
        series = {n: f.result() for n, f in futures.items()}

    empty = [n for n in names if not series[n]]
    if empty:
        raise PriceError(f"no round history for {', '.join(empty)}")

    ts_all = sorted({ts for n in names for ts, _ in series[n]})
    idx = {n: 0 for n in names}
    cur = {n: None for n in names}
    points = []
    for t in ts_all:
        for n in names:
            s = series[n]
            while idx[n] < len(s) and s[idx[n]][0] <= t:
                cur[n] = s[idx[n]][1]
                idx[n] += 1
        if t >= cutoff and all(cur[n] is not None for n in names):
            price = 1.0
            for n in base_legs:
                price *= cur[n]
            for n in quote_legs:
                price /= cur[n]
            points.append((t, price))

    result.update(
        points=[[t, p] for t, p in points],
        daily=daily_closes(points),
        legs={n: {"chain": FEEDS[n]["chain"], "kind": FEEDS[n]["kind"],
                  "rounds": len(series[n])}
              for n in names},
    )
    return result


def _drop(leg_list, counts):
    """Remove `counts` occurrences (a Counter) from leg_list, keeping order."""
    c = Counter(counts)
    out = []
    for n in leg_list:
        if c[n] > 0:
            c[n] -= 1
        else:
            out.append(n)
    return out


def daily_closes(points):
    """[[iso date, as-of price at UTC day end], ...] — days with no update
    carry the previous value forward."""
    if not points:
        return []
    utc = datetime.timezone.utc
    day = datetime.datetime.fromtimestamp(points[0][0], utc).date()
    today = datetime.datetime.now(utc).date()
    out, i, cur = [], 0, None
    while day <= today:
        day_end = datetime.datetime.combine(
            day, datetime.time(23, 59, 59), utc).timestamp()
        while i < len(points) and points[i][0] <= day_end:
            cur = points[i][1]
            i += 1
        out.append([day.isoformat(), cur])
        day += datetime.timedelta(days=1)
    return out


# ---------------------------------------------------------------------- CLI

def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    days = 30
    if "--days" in args:
        days = int(args[args.index("--days") + 1])
    pairs = [a for a in args if "/" in a and not a.startswith("--")]
    if len(pairs) != 1:
        print("usage: python history.py BASE[:method]/QUOTE[:method] [--days N] [--json]")
        sys.exit(2)

    base_spec, quote_spec = pairs[0].split("/")
    base, bm = parse_spec(base_spec)
    quote, qm = parse_spec(quote_spec)
    try:
        result = cross_series(base, quote, days, bm, qm)
    except PriceError as e:
        print(f"error: {e}")
        sys.exit(1)

    if as_json:
        print(json.dumps(result, indent=2))
        return
    via = ", ".join(f"{n}@{v['chain']} ({v['rounds']} rounds)"
                    for n, v in result["legs"].items())
    print(f"{result['pair']} — last {days}d, {len(result['points'])} points, via {via}\n")
    print(f"{'date':12} {'close':>14}")
    for date, price in result["daily"]:
        print(f"{date:12} {price:>14.8f}" if price is not None
              else f"{date:12} {'-':>14}")


if __name__ == "__main__":
    main()
