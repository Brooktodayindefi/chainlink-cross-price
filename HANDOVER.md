# Handover: Chainlink cross-price tool

Date: 2026-09-01. Written in Cowork; project continues in Claude Code.
Working folder: `C:\Users\aaa88\source\chainlink-cross-price\`

## Goal

Compute prices for custom stablecoin pairs that CoinGecko doesn't list
(e.g. reUSD/GHO, sUSDS/USDT, syrupUSDT/GHO). Approach: fetch a Chainlink feed
for each leg, convert every leg to USD, divide.

Rules from Brook:

- Exchange-rate feeds (sUSDS/USDS, syrupUSDT/USDT, …): chain doesn't matter, pick any deployment.
- Market-rate feeds (USDT/USD, GHO/USD, …): prefer the asset's native chain (Ethereum for everything in scope so far).
- Work in small steps. Step 1 (fetch feeds, verify) is done. Step 2 (cross-price calc) is next.

## Status

Step 1 complete and verified live (2026-09-01, Claude Code): `python feeds.py`
prints all 11 feeds with values matching the table below. One fix from the real
run: several public RPCs (publicnode, drpc, 1rpc) return 403 for Python's
default urllib User-Agent, so `rpc_call()` now sends a browser-like UA.

Step 2 complete: `cross.py` implements `usd_price()` / `cross()` and the CLI
(details under "Suggested step 2" — all items done except the reUSD decision,
which is still Brook's call; Brook said to skip reUSD for now).

Steps 3–4 complete (2026-09-01): historical cross prices + web UI.

- `history.py` walks Chainlink round history via `getRoundData(roundId)`
  (batched `eth_call`, phase-aware: proxy roundId = phaseId << 64 | round;
  older phases found by exponential + binary search). Rounds are immutable,
  so every fetched round is cached in `.cache/` and reused across runs.
  Series semantics: as-of stepwise — cross price recomputed at every leg
  update; `daily` gives UTC day-end closes with carry-forward.
  CLI: `python history.py BASE/QUOTE [--days N] [--json]`.
- `app.py` + `index.html`: local web UI (`python app.py`, port 8787, or the
  `cross-price` entry in `.claude/launch.json`). Select base + quote FEED
  (asset · method) and a 7/30/90/180/365d or custom window; renders line
  chart (crosshair + tooltip, light/dark) and a daily-close table with day
  deltas. JSON API: `/api/assets`,
  `/api/history?base=&quote=&days=[&base_method=&quote_method=]`.
- Verified in browser: sUSDS/USDT 30d/365d, syrupUSDT/GHO 90d, swap to
  GHO/syrupUSDT (exact inverse), wstETH:market/ETH over a custom 21d window.
  Daily closes match the CLI and today's close matches `cross.py` live values.

Step 5 (2026-09-01, same session): ETH assets + method selection + windows.

- New assets (feeds verified live): ETH, stETH, wstETH, weETH, rETH, cbETH.
  ETH-quoted feeds (weETH/ETH, rETH/ETH, cbETH/ETH) compose with ETH/USD.
  weETH's exrate feed quotes eETH (ether.fi rebasing token, redeems 1:1 for
  ETH) and is priced via ETH — same assumption every consumer makes. The
  Optimism "WEETH / USD Exchange Rate" feed returns that same ~1.10 rate
  despite its name (same trap as Arbitrum's SYRUPUSDT/USD label) — noted in
  ALTERNATES. Avoid Arbitrum 0x052d4200… for rETH: that's StaFi's rETH, not
  Rocket Pool's. Optimism RPC added.
- Pricing METHODS: an asset with both feed kinds can be priced either way —
  `exrate` (composed, the default) or `market` (direct market feed, labelled
  with its chain in the UI). CLI syntax: `wstETH:market/ETH`. Applies to
  sUSDe, wstETH, weETH, rETH, cbETH today. wstETH's only USD market feed
  lives on Optimism (mainnet has just a "Calculated" one) — market-method
  quotes are labelled with their chain per Brook.
- Windows: presets 7/30/90/180/365d + custom 1..1825d (input in UI,
  `--days N` in CLI).
- Perf (ETH market feeds do 15-45 rounds/day, so a year is ~15k rounds):
  legs fetch in parallel; the round walk estimates feed cadence and fetches
  several 100-round batches concurrently; `rpc_batch` retries endpoints with
  backoff then bisects before ever falling back to per-call fetches; legs
  appearing on both sides of a pair cancel exactly and are dropped without
  fetching (e.g. ETH/USD in cbETH:market/rETH:market). Invalid rounds are
  negative-cached. Cold 365d ETH pair ≈ 1-2 min once; cached ≈ 2 s.

`feeds.py` (stdlib only, plain JSON-RPC `eth_call` to public RPCs) fetches
every feed below. Decoding logic was unit-tested against canned hex in Cowork
and confirmed against live RPC reads in Claude Code.

Step 6 (2026-09-01): reUSD via re.xyz oracle; crvUSD, scrvUSD, apxUSD,
sUSDat, frxUSD added (see stables table below); block-sampled history for
round-less sources; per-pair "How this price is derived" card in the UI
(feeds used, source, chain, address, notes, cancelled legs). USD3 / fxSAVE /
apyUSD / USDat / BOLD remain uncovered — see Open issues.

Step 7 (2026-09-01): chart readability for dense/long series (ETH pairs do
thousands of updates per 90d). All client-side in index.html:
- Zoom: drag-select a region, mouse-wheel in/out around the cursor,
  double-click or "Reset zoom" to clear; y-axis rescales to the visible
  window. Zoom state survives All/Daily toggling but resets on new data.
- Navigator strip under the chart: full-range mini-line with the current
  window shaded; drag it to pan.
- Fullscreen: &#x26F6; button opens the chart in a large modal (same
  interactions); &#x2715; or Esc closes.
- "All updates" vs "Daily" resolution toggle (daily closes come from the
  same response, no refetch).
- The drawn path is M4-downsampled (first/min/max/last per pixel bucket) so
  spikes survive at any width; hover/tooltip still uses full data with
  binary-search snapping.
- Stats line under the meta: visible-window point count, min, max, change %.

## Verified feeds (proxy addresses, 2026-09-01)

Market feeds: 8 decimals. Exchange-rate feeds: 18 decimals. All have 24h heartbeat
with 0.05 % (exrate) / 0.25–0.5 % (market) deviation triggers, so 10–20 h staleness is normal.

| Pair | Chain | Kind | Proxy | Value seen |
|---|---|---|---|---|
| USDT/USD | Ethereum | market | `0x3E7d1eAB13ad0104d2750B8863b489D65364e32D` | 0.99988 |
| USDC/USD | Ethereum | market | `0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6` | 0.99990 |
| GHO/USD | Ethereum | market | `0x3f12643D3f6f874d39C2a4c9f2Cd6f2DbAC877FC` | 0.99920 |
| USDS/USD | Ethereum | market | `0xfF30586cD0F29eD462364C7e81375FC0C71219b1` | 0.99992 |
| USDe/USD | Ethereum | market | `0xa569d910839Ae8865Da8F8e70FfFb0cBA869F961` | 0.99980 |
| sUSDe/USD | Ethereum | market | `0xFF3BC18cCBd5999CE63E788A1c250a88626aD099` | 1.24589 |
| sUSDe/USD Calculated | Ethereum | exrate×USDe/USD | `0xeD9960f685C3c4d6aa937E56169a41C19D0aC9c6` | 1.24574 |
| sUSDe/USDe | Arbitrum | exrate | `0x605EA726F0259a30db5b7c9ef39Df9fE78665C44` | 1.24590 |
| sUSDS/USDS | Arbitrum | exrate | `0x2483326d19f780Fb082f333Fe124e4C075B207ba` | 1.10805 |
| syrupUSDC/USDC | Arbitrum | exrate | `0xF8722c901675C4F2F7824E256B8A6477b2c105FB` | 1.18092 |
| syrupUSDT/USDT | Mantle | exrate | `0xdDEaeAdF319bd363120Af02fBdb1e2C5A3Ce172a` | 1.14118 |

ETH & LST feeds added 2026-09-01 (verified live; values from that day):

| Pair | Chain | Kind | Proxy | Value seen |
|---|---|---|---|---|
| ETH/USD | Ethereum | market | `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419` | 2459.48 |
| stETH/USD | Ethereum | market | `0xCfE54B5cD566aB89272946F602D76Ea879CAb4a8` | 2459.95 |
| wstETH/USD | Optimism | market | `0x698B585CbC4407e2D54aa898B2600B53C68958f7` | 3049.61 |
| weETH/ETH | Ethereum | market | `0x5c9C449BbC9a6075A2c061dF312a35fd1E05fF22` | 1.10249 |
| rETH/ETH | Ethereum | market | `0x536218f9E9Eb48863970252233c8F271f554C2d0` | 1.16961 |
| cbETH/ETH | Ethereum | market | `0xF017fcB346A1885194689bA23Eff2fE6fA5C483b` | 1.13795 |
| wstETH/stETH | Arbitrum | exrate | `0xB1552C5e96B312d0Bf8b554186F846C40614a540` | 1.24295 |
| weETH/eETH | Arbitrum | exrate | `0x20bAe7e1De9c596f5F7615aeaa1342Ba99294e12` | 1.10268 |
| rETH/ETH xr | Arbitrum | exrate | `0xF3272CAfe65b190e76caAF483db13424a3e23dD2` | 1.17074 |
| cbETH/ETH xr | Arbitrum | exrate | `0x0518673439245BB95A58688Bc31cd513F3D5bDd6` | 1.13844 |

More stables added 2026-09-01 (verified live; values from that day):

| Pair | Chain | Kind / source | Proxy | Value seen |
|---|---|---|---|---|
| crvUSD/USD | Ethereum | market | `0xEEf0C605546958c1f899b6fB336C20671f9cD49F` | 0.99947 |
| frxUSD/USD | Ethereum | market | `0x9B4a96210bc8D9D55b1908B465D8B0de68B7fF83` | 0.99987 |
| apxUSD/USD | Ethereum | exrate (quotes USD) | `0x651b101f72F82630cf59c68E6EE4305aFBd3B1F5` | 0.98727 |
| sUSDat/USD | Ethereum | exrate ("Saturn sUSDat NAV") | `0x73B8E902638a21B4d0319CF99Fa333b2727AD318` | 1.01921 |
| reUSD/USD | Ethereum | exrate, re.xyz oracle, history=blocks | `0x72B5760cFBE437DD01409f44055fDfB8f8121B46` | 1.09814 |
| scrvUSD/crvUSD | Ethereum | ERC-4626 `convertToAssets`, history=blocks | `0x0655977FEb2f289A4aB78af67BAB0d17aAb84367` | 1.10658 |
| USD3/USD | Ethereum | market, RedStone, history=blocks | `0xB39339B82DdCF89d12d987d1D4Db33aFdd40B6AA` | 1.10978 |
| apyUSD/apxUSD | Ethereum | exrate, bare `price()` 1e36, history=blocks | `0x770661EE520Ff9F7D8FaCAdC4EFF885739Bd8872` | 1.42089 |
| fxSAVE/USD | Ethereum | exrate, f(x) NAV oracle, history=blocks | `0x9dD65b6d956E31F4dc093372D975275986695827` | 1.11535 |

`history="blocks"` sources keep no round history; `history.py` samples them
once per UTC day via archive `eth_call` at estimated past blocks (ethereum
~12.05 s/block), cached by midnight timestamp. FEEDS entries can carry
optional `src` / `reader` / `history` / `note` fields; `note` feeds the UI's
"How this price is derived" card.

Alternate deployments (same answer to ~5 dp, useful as fallbacks) are in
`ALTERNATES` in `feeds.py`: sUSDS/USDS on Base; syrupUSDC/USDC on Base and
Mantle; syrupUSDT/USDT on Plasma and Arbitrum (the Arbitrum one is labelled
"SYRUPUSDT / USD Exchange Rate" on-chain but returns the USDT exrate);
sUSDe/USDe on Base; GHO/USD and USDS/USD and USDT/USD on Plasma.

Deliberately skipped: the `-svr` / `-shared-svr` duplicates of USDT, USDC, GHO
on Ethereum (Smart Value Recapture feeds for Aave liquidations — same price,
different delivery path).

RPCs that worked keylessly: `ethereum-rpc.publicnode.com`, `eth.drpc.org`,
`1rpc.io/eth`, `arbitrum-one-rpc.publicnode.com`, `base-rpc.publicnode.com`,
`mantle-rpc.publicnode.com`, `rpc.plasma.to`. `eth.llamarpc.com` and
`cloudflare-eth.com` failed; `plasma.drpc.org` needs a paid plan.

Feed directory source (same JSON docs.chain.link renders):
`https://reference-data-directory.vercel.app/feeds-<network>.json` with
`feeds-mainnet`, `feeds-ethereum-mainnet-arbitrum-1`, `feeds-ethereum-mainnet-base-1`,
`feeds-ethereum-mainnet-mantle-1`, `feeds-plasma-mainnet`, etc. Entries with an
empty `proxyAddress` are Data Streams (off-chain), not contracts.

## Open issues

**reUSD — RESOLVED 2026-09-01.** Brook supplied re.xyz's own oracle:
`0x72B5760cFBE437DD01409f44055fDfB8f8121B46` on Ethereum, description
"reUSD/USD exchange rate", 18 dec, AggregatorV3-compatible reads but NO round
history (roundId always 0) — so history is read at past blocks (see
history="blocks" below). Note: Brook's message displayed a second address
`0xA66a4F03Fd8031973f8C7718904ce32385f54E70` — it's a contract but answers
neither aggregator nor ERC-20 calls (probably the internal rate contract the
oracle wraps); the URL's address above is the working one.

**USD3 / apyUSD / fxSAVE — RESOLVED 2026-09-02.** Brook supplied oracle
addresses (all Ethereum, all history="blocks"):
- USD3: `0xB39339B82DdCF89d12d987d1D4Db33aFdd40B6AA` — RedStone push feed,
  quotes USD, roundId stuck at 1.
- apyUSD: `0x770661EE520Ff9F7D8FaCAdC4EFF885739Bd8872` — exposes ONLY
  `price()` (everything else reverts), 1e36-scaled apyUSD-in-apxUSD; new
  reader="price" with a `scale` field handles it; composes with the
  Chainlink apxUSD/USD feed.
- fxSAVE: `0x9dD65b6d956E31F4dc093372D975275986695827` — aggregator
  interface, description "Net Asset Value in USD", but updatedAt=0 and no
  rounds; zero timestamps now count as live state.

**Still uncovered (no on-chain feed found anywhere, 2026-09-01 sweep of the
Chainlink directory across ~15 networks):** USDat (only sUSDat has a feed —
the Saturn NAV), BOLD (Liquity v2 — nothing at all). Each needs a
project-supplied oracle address (like reUSD's) or another source (Curve
pool `price_oracle()`, ERC-4626 vault, …) to be added.

**Step 9 (2026-09-02): sUSDS market alias + reUSD CoinGecko market rate.**
- sUSDS has no direct USD market feed anywhere, so its "market" method is an
  alias for the exrate composition (sUSDS/USDS × USDS/USD market) — Brook
  asked for it explicitly; MARKET_VIA_EXRATE in cross.py, labelled
  "market · via USDS", numerically identical to exrate, noted in the UI.
- reUSD gained a real market method: CoinGecko id `re-protocol-reusd`
  (NOT `resupply-usd`, a different project at ~0.989), reader/history
  "coingecko" (spot via simple/price, history via market_chart, 365d free
  cap, hourly ≤90d, not disk-cached). reUSD trades ~12 bp below its oracle
  rate; the pair reUSD:market/reUSD:exrate charts that discount directly.

**Transport hardening (2026-09-02, found via a real bug):** `rpc_batch` used
to return None for transport failures, which callers negative-cached as
"round doesn't exist" — a rate-limit burst permanently blanked fxSAVE/USD3
history. Now: only genuine reverts become None; item-level errors like
"missing trie node" (an endpoint without archive state) reject that endpoint
for the batch; total transport failure raises. Poisoned caches were purged.

**sUSDe has two USD feeds** (market vs calculated), ~1 bp apart. Recommendation
for step 2: always compose exrate × underlying market yourself
(sUSDS/USD = sUSDS/USDS × USDS/USD, syrupUSDT/USD = syrupUSDT/USDT × USDT/USD,
sUSDe/USD = sUSDe/USDe × USDe/USD) so every yield-bearing asset is priced the
same way; keep the market/calculated sUSDe feeds only as sanity checks.

"Any API" / Chainlink Functions were ruled out — they're for contracts pulling
off-chain data on-chain, not a REST API for feed values. On-chain RPC reads are
the official consumption path.

## Suggested step 2

1. ~~Run `feeds.py`; fix anything the real run turns up.~~ Done (User-Agent fix).
2. ~~Add a `usd_price(asset)` resolver.~~ Done in `cross.py`. Per the sUSDe
   recommendation above, exrate composition takes precedence over a direct
   market feed: if an asset has an exrate feed, its USD price is always
   exrate × `usd_price(underlying)` (recursive); only plain assets use market
   feeds. The sUSDe market/calculated feeds are used only by `--check`, which
   showed the composed price within 2 bp of both.
3. ~~`cross(base, quote)`~~ Done — reports price, per-leg feed details, and
   max age across the legs used.
4. Decide the reUSD source; add it as its own adapter rather than faking a
   Chainlink entry. **Still open — `cross.py` raises a clear error for reUSD
   until this is decided.**
5. ~~CLI + JSON output.~~ Done: `python cross.py [--json] BASE/QUOTE ...`,
   plus `--check` (sUSDe sanity check); no args lists supported assets.

Verified output (2026-09-01): sUSDS/USDT 1.10809841, syrupUSDT/GHO 1.14195833,
syrupUSDC/GHO 1.18174370, sUSDe/USDT 1.24580510.

## Files

- `feeds.py` — registry (`FEEDS`, `ALTERNATES`, `RPC`), `read_feed()`, `fetch_all()`, `rpc_batch()`, CLI (`--json`).
- `cross.py` — `legs()` / `usd_price()` resolver, `cross()`, CLI (`--json`, `--check`).
- `history.py` — round-history walker (`feed_rounds()`), `cross_series()`, daily closes, disk cache in `.cache/`, CLI.
- `app.py` — local web server (`/api/assets`, `/api/history`), serves `index.html` on port 8787.
- `index.html` — the UI: asset selectors, window presets, line chart, daily table.
- `.claude/launch.json` — `cross-price` launch entry for the app.
- `HANDOVER.md` — this note.
