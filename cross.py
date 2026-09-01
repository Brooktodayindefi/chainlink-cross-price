"""
Cross-price calculator — step 2 of the cross-price tool.

Prices any base/quote pair by converting each leg to USD via the Chainlink
feeds registered in feeds.py, then dividing.

    python cross.py sUSDS/USDT syrupUSDT/GHO      # table
    python cross.py --json sUSDS/USDT             # JSON (for downstream use)
    python cross.py --check                       # sanity-check composed
                                                  # sUSDe/USD vs the market
                                                  # and calculated feeds
    python cross.py                               # list supported assets

Pricing policy (per HANDOVER.md):
  * Yield-bearing assets are ALWAYS composed as exrate x usd_price(underlying),
    even when a direct USD feed exists (sUSDe), so every yield-bearing asset
    is priced the same way. The sUSDe market/calculated feeds are used only
    by --check.
  * Plain assets use their market USD feed.
  * reUSD has no source yet (Chainlink only publishes an off-chain Data
    Stream). Pricing it raises until an adapter is added — see NOTES in
    feeds.py and "Open issues" in HANDOVER.md.
"""

import json
import sys

from feeds import FEEDS, read_source

# asset -> feed-name indexes, derived from the registry
MARKET = {f["base"]: name for name, f in FEEDS.items() if f["kind"] == "market"}
EXRATE = {f["base"]: name for name, f in FEEDS.items() if f["kind"] == "exrate"}


class PriceError(ValueError):
    pass


def _fetch(name, cache):
    if name not in cache:
        f = FEEDS[name]
        d = read_source(f)
        d.update(name=name, kind=f["kind"], base=f["base"], quote=f["quote"])
        cache[name] = d
    return cache[name]


def methods(asset):
    """Pricing methods available for `asset`, default first. Exrate is the
    default where it exists (yield-bearing assets are composed by default,
    per HANDOVER.md); market is the alternative when the asset has both."""
    out = []
    if asset in EXRATE:
        out.append("exrate")
    if asset in MARKET:
        out.append("market")
    return out


def legs(asset, method=None):
    """Feed names whose answers multiply into asset's USD price, using the
    given pricing method (None -> the asset's default). A feed quoted in
    something other than USD composes recursively with the quote asset's
    default-method USD price (e.g. weETH/ETH x ETH/USD)."""
    if asset == "USD":
        return []
    avail = methods(asset)
    if not avail:
        raise PriceError(f"no price source for {asset!r}")
    m = method or avail[0]
    if m not in avail:
        raise PriceError(f"{asset} has no {m} feed (available: {', '.join(avail)})")
    name = EXRATE[asset] if m == "exrate" else MARKET[asset]
    return [name] + legs(FEEDS[name]["quote"])


def parse_spec(spec):
    """CLI asset spec 'asset' or 'asset:method' -> (asset, method|None)."""
    asset, _, m = spec.partition(":")
    return asset, (m or None)


def usd_price(asset, cache, method=None):
    """Return (price, legs): asset's USD price and the feed dicts used, in
    composition order. cache maps feed name -> read_feed() result so each
    feed is fetched at most once per run."""
    price, used = 1.0, []
    for name in legs(asset, method):
        d = _fetch(name, cache)
        price *= d["answer"]
        used.append(d)
    return price, used


def cross(base, quote, cache=None, base_method=None, quote_method=None):
    """cross-price = usd_price(base) / usd_price(quote). Reports every feed
    used and the max age across those legs."""
    cache = {} if cache is None else cache
    base_usd, base_legs = usd_price(base, cache, base_method)
    quote_usd, quote_legs = usd_price(quote, cache, quote_method)
    legs = base_legs + quote_legs
    return {
        "pair": f"{base}/{quote}",
        "price": base_usd / quote_usd,
        "base_usd": base_usd,
        "quote_usd": quote_usd,
        "feeds": [{"name": l["name"], "chain": l["chain"], "answer": l["answer"],
                   "updated_at": l["updated_at"], "age_h": l["age_h"]} for l in legs],
        "max_age_h": max(l["age_h"] for l in legs) if legs else 0.0,
    }


def sanity_check():
    """Compare composed sUSDe/USD against Chainlink's market and calculated
    feeds (kept in FEEDS for exactly this purpose)."""
    cache = {}
    composed, legs = usd_price("sUSDe", cache)
    print(f"sUSDe/USD composed    {composed:.8f}  via {' x '.join(l['name'] for l in legs)}")
    for name in ("sUSDe/USD", "sUSDe/USD-calc"):
        d = _fetch(name, cache)
        diff_bp = (composed / d["answer"] - 1) * 1e4
        print(f"{d['description']:21} {d['answer']:.8f}  diff {diff_bp:+.2f} bp  age {d['age_h']:.1f}h")


def main():
    args = sys.argv[1:]
    as_json = "--json" in args
    pairs = [a for a in args if not a.startswith("--")]

    if "--check" in args:
        sanity_check()
        return

    if not pairs:
        assets = sorted(set(MARKET) | set(EXRATE) | {"USD"})
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python cross.py [--json] BASE[:method]/QUOTE[:method] ...")
        print("       method = exrate | market (default: exrate where available)")
        print(f"assets: {', '.join(assets)}")
        return

    cache, results, failed = {}, [], False
    for pair in pairs:
        try:
            base_spec, quote_spec = pair.split("/")
            base, bm = parse_spec(base_spec)
            quote, qm = parse_spec(quote_spec)
            results.append(cross(base, quote, cache, bm, qm))
        except (PriceError, ValueError) as e:
            failed = True
            results.append({"pair": pair, "error": str(e)})

    if as_json:
        print(json.dumps(results, indent=2))
    else:
        print(f"{'pair':16} {'price':>14} {'max_age(h)':>11}  feeds used")
        for r in results:
            if "error" in r:
                print(f"{r['pair']:16} {'ERROR':>14}              {r['error']}")
            else:
                via = ", ".join(f"{f['name']}@{f['chain']}" for f in r["feeds"])
                print(f"{r['pair']:16} {r['price']:>14.8f} {r['max_age_h']:>11.1f}  {via}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
