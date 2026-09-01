# chainlink-cross-price

Price custom stablecoin / LST pairs that aggregators don't list (reUSD/GHO,
sUSDS/USDT, wstETH/weETH, …) by converting each leg to USD via on-chain
oracles and dividing. Live prices, historical charts, and a small web UI.

No dependencies — Python 3.9+ standard library only. Reads public JSON-RPC
endpoints directly (no API keys).

## Quick start

```
python app.py
```

Open http://127.0.0.1:8787 — pick a base and quote feed, a time window
(7/30/90/180/365 days or custom), and you get a line chart plus a daily-close
table. Chart supports drag-to-zoom, wheel zoom, a full-range navigator strip,
a fullscreen view, and an all-updates vs daily-closes toggle. Every pair shows
a "How this price is derived" card listing the exact feeds used (source,
chain, contract address) and any caveats.

### CLI

```
python feeds.py                     # fetch every registered feed, print a table
python cross.py reUSD/GHO sUSDS/USDT --json
python cross.py wstETH:market/ETH   # choose pricing method per side
python cross.py --check             # sanity-check composed sUSDe/USD vs Chainlink's
python history.py syrupUSDT/GHO --days 90 --json
```

## How pricing works

* **Assets** are priced in USD through one of two methods:
  * `exrate` (default where available): exchange-rate feed × the underlying's
    USD price, recursively — e.g. sUSDS/USD = sUSDS/USDS exrate × USDS/USD
    market. Yield-bearing assets are always composed the same way.
  * `market`: a direct market-price feed (labelled with its chain — most live
    on Ethereum, wstETH's only USD market feed is on Optimism).
* **Cross price** = base USD ÷ quote USD. A feed appearing on both sides
  (e.g. ETH/USD in cbETH:market/rETH:market) cancels exactly and is skipped.
* **History** walks Chainlink round history via `getRoundData` (batched
  JSON-RPC, phase-aware) and composes an as-of stepwise series; sources
  without round history (the re.xyz reUSD oracle, the scrvUSD ERC-4626 vault)
  are sampled once per UTC day at estimated past blocks. Everything fetched
  is cached under `.cache/` (rounds are immutable).

## Sources

Mostly Chainlink feeds (proxy addresses verified live against
docs.chain.link's directory), plus two non-Chainlink adapters: re.xyz's
reUSD/USD oracle and the Savings-crvUSD vault rate. The full registry with
addresses, alternates, and per-feed notes is in `feeds.py`; project history
and open issues (assets with no on-chain feed yet: USD3, fxSAVE, apyUSD,
USDat, BOLD) are in `HANDOVER.md`.

## Files

| file | role |
|---|---|
| `feeds.py` | feed registry + JSON-RPC transport (single, batched, archive) |
| `cross.py` | USD resolver, live cross prices, CLI |
| `history.py` | round-history walker, block sampler, cross-price series |
| `app.py` | local web server + JSON API |
| `index.html` | the UI |
