"""
Chainlink feed fetcher — step 1 of the cross-price tool.

Reads latestRoundData() from Chainlink AggregatorV3 proxies over plain JSON-RPC
(stdlib only, no web3 dependency). Run:

    python feeds.py            # fetch every feed in FEEDS and print a table
    python feeds.py --json     # same, as JSON (for piping into the next step)

Feed selection rules (per Brook):
  * market-rate feeds  -> native chain of the asset (Ethereum for all of these)
  * exchange-rate feeds -> any chain; picked the one with the freshest/most
    canonical deployment (Arbitrum for sUSDS & syrupUSDC, Mantle for syrupUSDT)
  * reUSD has NO on-chain Chainlink feed — only a Data Streams (off-chain,
    API-key) feed. Left as a placeholder; see NOTES at the bottom.
"""

import json
import sys
import time
import urllib.request

# --------------------------------------------------------------------------
# RPC endpoints (public, CORS/no-key). Swap in your own (Alchemy/Infura/etc.)
# if you hit rate limits. Several fallbacks per chain.
# --------------------------------------------------------------------------
RPC = {
    "ethereum": [
        "https://ethereum-rpc.publicnode.com",
        "https://eth.drpc.org",
        "https://1rpc.io/eth",
    ],
    "arbitrum": [
        "https://arbitrum-one-rpc.publicnode.com",
        "https://arb1.arbitrum.io/rpc",
    ],
    "base": [
        "https://base-rpc.publicnode.com",
        "https://mainnet.base.org",
    ],
    "mantle": [
        "https://mantle-rpc.publicnode.com",
        "https://rpc.mantle.xyz",
    ],
    "optimism": [
        "https://optimism-rpc.publicnode.com",
        "https://mainnet.optimism.io",
        "https://1rpc.io/op",
    ],
    "plasma": [
        "https://rpc.plasma.to",
    ],
}

# --------------------------------------------------------------------------
# Feed registry.
#   kind: "market"   -> Chainlink price feed (aggregated CEX/DEX market price)
#         "exrate"   -> on-chain exchange rate (ERC-4626 convertToAssets etc.)
#   base/quote: the pair the feed answers, e.g. sUSDS/USDS
# Addresses are the *proxy* addresses from reference-data-directory (the same
# source docs.chain.link renders). Verified live on 2026-09-01.
# --------------------------------------------------------------------------
FEEDS = {
    # ---- market rates, native chain = Ethereum ----
    "USDT/USD":  dict(chain="ethereum", kind="market", base="USDT",  quote="USD",
                      address="0x3E7d1eAB13ad0104d2750B8863b489D65364e32D"),
    "USDC/USD":  dict(chain="ethereum", kind="market", base="USDC",  quote="USD",
                      address="0x8fFfFfd4AfB6115b954Bd326cbe7B4BA576818f6"),
    "GHO/USD":   dict(chain="ethereum", kind="market", base="GHO",   quote="USD",
                      address="0x3f12643D3f6f874d39C2a4c9f2Cd6f2DbAC877FC"),
    "USDS/USD":  dict(chain="ethereum", kind="market", base="USDS",  quote="USD",
                      address="0xfF30586cD0F29eD462364C7e81375FC0C71219b1"),
    "USDe/USD":  dict(chain="ethereum", kind="market", base="USDe",  quote="USD",
                      address="0xa569d910839Ae8865Da8F8e70FfFb0cBA869F961"),
    # sUSDe has BOTH a market feed and a "calculated" feed
    # (calculated = sUSDe/USDe exchange rate x USDe/USD market).
    "sUSDe/USD":      dict(chain="ethereum", kind="market", base="sUSDe", quote="USD",
                           address="0xFF3BC18cCBd5999CE63E788A1c250a88626aD099"),
    "sUSDe/USD-calc": dict(chain="ethereum", kind="calculated", base="sUSDe", quote="USD",
                           address="0xeD9960f685C3c4d6aa937E56169a41C19D0aC9c6"),

    # ---- exchange rates, chain doesn't matter ----
    "sUSDe/USDe":     dict(chain="arbitrum", kind="exrate", base="sUSDe",     quote="USDe",
                           address="0x605EA726F0259a30db5b7c9ef39Df9fE78665C44"),
    "sUSDS/USDS":     dict(chain="arbitrum", kind="exrate", base="sUSDS",     quote="USDS",
                           address="0x2483326d19f780Fb082f333Fe124e4C075B207ba"),
    "syrupUSDC/USDC": dict(chain="arbitrum", kind="exrate", base="syrupUSDC", quote="USDC",
                           address="0xF8722c901675C4F2F7824E256B8A6477b2c105FB"),
    "syrupUSDT/USDT": dict(chain="mantle",   kind="exrate", base="syrupUSDT", quote="USDT",
                           address="0xdDEaeAdF319bd363120Af02fBdb1e2C5A3Ce172a"),

    # ---- ETH & liquid-staking assets (added 2026-09-01, verified live) ----
    # Market rates on the asset's native chain (Ethereum) where a feed exists;
    # ETH-quoted feeds compose with ETH/USD in cross.py.
    "ETH/USD":    dict(chain="ethereum", kind="market", base="ETH",    quote="USD",
                       address="0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"),
    "stETH/USD":  dict(chain="ethereum", kind="market", base="stETH",  quote="USD",
                       address="0xCfE54B5cD566aB89272946F602D76Ea879CAb4a8"),
    "wstETH/USD": dict(chain="optimism", kind="market", base="wstETH", quote="USD",
                       address="0x698B585CbC4407e2D54aa898B2600B53C68958f7",
                       note="wstETH has no USD market feed on Ethereum "
                            "(mainnet only has a 'Calculated' one); Optimism "
                            "hosts the only market deployment."),
    "weETH/ETH":  dict(chain="ethereum", kind="market", base="weETH",  quote="ETH",
                       address="0x5c9C449BbC9a6075A2c061dF312a35fd1E05fF22"),
    "rETH/ETH":   dict(chain="ethereum", kind="market", base="rETH",   quote="ETH",
                       address="0x536218f9E9Eb48863970252233c8F271f554C2d0"),
    "cbETH/ETH":  dict(chain="ethereum", kind="market", base="cbETH",  quote="ETH",
                       address="0xF017fcB346A1885194689bA23Eff2fE6fA5C483b"),

    "wstETH/stETH": dict(chain="arbitrum", kind="exrate", base="wstETH", quote="stETH",
                         address="0xB1552C5e96B312d0Bf8b554186F846C40614a540"),
    "weETH/eETH":   dict(chain="arbitrum", kind="exrate", base="weETH", quote="ETH",
                         address="0x20bAe7e1De9c596f5F7615aeaa1342Ba99294e12",
                         note="On-chain description 'weETH / eETH Exchange "
                              "Rate'; eETH is ether.fi's rebasing token, "
                              "redeemable 1:1 for ETH, so the leg is priced "
                              "via ETH — the same assumption every consumer "
                              "of this feed makes."),
    "rETH/ETH-xr":  dict(chain="arbitrum", kind="exrate", base="rETH",  quote="ETH",
                         address="0xF3272CAfe65b190e76caAF483db13424a3e23dD2"),
    "cbETH/ETH-xr": dict(chain="arbitrum", kind="exrate", base="cbETH", quote="ETH",
                         address="0x0518673439245BB95A58688Bc31cd513F3D5bDd6"),

    # ---- more stables (added 2026-09-01, verified live) ----
    # Optional entry fields: src (default "Chainlink"), reader (default
    # "aggregator"), history (default "rounds" | "blocks" = sample past
    # blocks, for sources that keep no round history), note (shown in the
    # UI's derivation card).
    "crvUSD/USD": dict(chain="ethereum", kind="market", base="crvUSD", quote="USD",
                       address="0xEEf0C605546958c1f899b6fB336C20671f9cD49F"),
    "frxUSD/USD": dict(chain="ethereum", kind="market", base="frxUSD", quote="USD",
                       address="0x9B4a96210bc8D9D55b1908B465D8B0de68B7fF83"),
    "apxUSD/USD": dict(chain="ethereum", kind="exrate", base="apxUSD", quote="USD",
                       address="0x651b101f72F82630cf59c68E6EE4305aFBd3B1F5",
                       note="Chainlink 'APXUSD / USD Exchange Rate' — protocol "
                            "exchange rate quoted in USD directly (0.987 on "
                            "2026-09-01, i.e. genuinely below peg)."),
    "sUSDat/USD": dict(chain="ethereum", kind="exrate", base="sUSDat", quote="USD",
                       address="0x73B8E902638a21B4d0319CF99Fa333b2727AD318",
                       note="Chainlink 'Saturn sUSDat NAV' — NAV per share in "
                            "USD. USDat itself has no feed anywhere."),
    "reUSD/USD":  dict(chain="ethereum", kind="exrate", base="reUSD", quote="USD",
                       address="0x72B5760cFBE437DD01409f44055fDfB8f8121B46",
                       src="re.xyz", history="blocks",
                       note="re.xyz 'reUSD/USD exchange rate' oracle (address "
                            "from Brook, 2026-09-01). Speaks latestRoundData() "
                            "but keeps no round history (roundId always 0), so "
                            "historical values are read at past blocks, one "
                            "sample per UTC day."),
    "scrvUSD/crvUSD": dict(chain="ethereum", kind="exrate", base="scrvUSD", quote="crvUSD",
                           address="0x0655977FEb2f289A4aB78af67BAB0d17aAb84367",
                           src="Curve vault", reader="erc4626", history="blocks",
                           note="scrvUSD has no Chainlink feed; this reads the "
                                "Savings-crvUSD ERC-4626 vault's "
                                "convertToAssets(1e18) — the on-chain redemption "
                                "rate (vault symbol/asset verified on-chain). "
                                "History is sampled at past blocks."),

    # ---- project oracles supplied by Brook 2026-09-01 (second batch) ----
    "USD3/USD": dict(chain="ethereum", kind="market", base="USD3", quote="USD",
                     address="0xB39339B82DdCF89d12d987d1D4Db33aFdd40B6AA",
                     src="RedStone", history="blocks",
                     note="RedStone push feed for Reserve's USD3 (on-chain "
                          "description is just 'Redstone Price Feed'). Its "
                          "roundId doesn't increment, so history is sampled "
                          "at past blocks."),
    "apyUSD/apxUSD": dict(chain="ethereum", kind="exrate", base="apyUSD", quote="apxUSD",
                          address="0x770661EE520Ff9F7D8FaCAdC4EFF885739Bd8872",
                          src="project oracle", reader="price", scale=36,
                          history="blocks",
                          note="Bare price() oracle — the only function it "
                               "exposes; returns apyUSD in apxUSD, 1e36-scaled. "
                               "Composes with the Chainlink apxUSD/USD "
                               "exchange-rate feed. History is sampled at past "
                               "blocks."),
    "fxSAVE/USD": dict(chain="ethereum", kind="exrate", base="fxSAVE", quote="USD",
                       address="0x9dD65b6d956E31F4dc093372D975275986695827",
                       src="f(x) Protocol", history="blocks",
                       note="NAV oracle, on-chain description 'Net Asset Value "
                            "in USD'; Brook describes it as fxSAVE-in-fxUSD — "
                            "equivalent while fxUSD holds its $1 peg (fxUSD "
                            "itself has no feed anywhere). Returns updatedAt=0, "
                            "so values count as live state and history is "
                            "sampled at past blocks."),
}

# Alternate deployments of the same feeds (same answer, different chain) —
# useful as fallbacks or if you'd rather read everything from one chain.
ALTERNATES = {
    "sUSDS/USDS":     [("base",   "0x906B24a339b848369B24Dc9Ed368b947fB9693bf")],
    "syrupUSDC/USDC": [("base",   "0x311D3A3faA1d5939c681E33C2CDAc041FF388EB2"),
                       ("mantle", "0xA6B6A4E844126b8a44Ab6564b3A65048217cB58C")],
    "syrupUSDT/USDT": [("plasma", "0x89a0e204591Fce2611e89CA7634c12B400d347fe"),
                       ("arbitrum", "0xdB0c64eFa0395063033b10769A80c62F885A620a")],  # labelled "SYRUPUSDT / USD" on-chain but is the USDT exrate
    "sUSDe/USDe":     [("base",   "0xdEd37FC1400B8022968441356f771639ad1B23aA")],
    "GHO/USD":        [("plasma", "0x26B2D944e1e9db3fC9D38760bde97cd0ADc68C75"),
                       ("arbitrum", "0x3c786e934F23375Ca345C9b8D5aD54838796E8e7")],
    "USDS/USD":       [("plasma", "0x13c24a53aBA7Dee3d42Bc28c6DC56100883Ce42c")],
    "USDT/USD":       [("plasma", "0x70b77FcdbE2293423e41AdD2FB599808396807BC")],
    "wstETH/stETH":   [("base",     "0xB88BAc61a4Ca37C43a3725912B1f472c9A5bc061"),
                       ("optimism", "0xe59EBa0D492cA53C6f46015EEa00517F2707dc77")],
    "weETH/eETH":     [("base",     "0x35e9D7001819Ea3B39Da906aE6b06A62cfe2c181"),
                       ("optimism", "0x72EC6bF88effEd88290C66DCF1bE2321d80502f5"),
                       # labelled "WEETH / USD Exchange Rate" on-chain but returns
                       # the same ~1.10 eETH rate, not a USD price
                       ("optimism", "0xed5D3c24A8B0591CB3029Ca272DD1721343a9C1D")],
    "rETH/ETH-xr":    [("base",     "0x1E6A29666288a310326B37d823Fe4Ea3937424D2"),
                       ("optimism", "0x22F3727be377781d1579B7C9222382b21c9d1a8f")],
                       # NOT 0x052d4200... on arbitrum — that is StaFi's rETH, a
                       # different token than Rocket Pool's
    "cbETH/ETH-xr":   [("base",     "0x868a501e68F3D1E89CfC0D22F6b22E8dabce5F04")],
}

# AggregatorV3Interface selectors
SEL_DECIMALS = "0x313ce567"
SEL_DESCRIPTION = "0x7284e416"
SEL_LATEST_ROUND = "0xfeaf968c"

# several public RPCs 403 the default Python-urllib UA
HEADERS = {"content-type": "application/json",
           "user-agent": "Mozilla/5.0 chainlink-cross-price/0.1"}


def _post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def rpc_call(chain, to, data, block="latest"):
    """eth_call with per-chain fallback across RPC list."""
    last_err = None
    for url in RPC[chain]:
        try:
            j = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                            "params": [{"to": to, "data": data}, block]}, 15)
            if "error" in j:
                raise RuntimeError(j["error"])
            return j["result"]
        except Exception as e:  # try next endpoint
            last_err = e
    raise RuntimeError(f"all RPCs failed for {chain}: {last_err}")


def latest_block(chain):
    """-> (block number, block timestamp)."""
    last_err = None
    for url in RPC[chain]:
        try:
            j = _post(url, {"jsonrpc": "2.0", "id": 1,
                            "method": "eth_getBlockByNumber",
                            "params": ["latest", False]}, 15)
            b = j["result"]
            return int(b["number"], 16), int(b["timestamp"], 16)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"latest_block failed for {chain}: {last_err}")


def rpc_batch(chain, calls):
    """Batched eth_call: calls = [(to, data) or (to, data, block), ...] ->
    list of result hex, or None for calls that REVERTED (e.g. a nonexistent
    round). A transport-level failure (endpoints down / rate-limited) RAISES
    instead — callers negative-cache None results, so an outage must never
    masquerade as a revert. On failure: retry the endpoints once after a
    pause, then bisect (an endpoint may cap batch size); go one-by-one only
    for remnants — a sequential fallback over a big batch is a stall."""
    calls = [c if len(c) == 3 else (c[0], c[1], "latest") for c in calls]
    payload = [{"jsonrpc": "2.0", "id": i, "method": "eth_call",
                "params": [{"to": to, "data": data}, block]}
               for i, (to, data, block) in enumerate(calls)]
    last_err = None
    for attempt in range(2):
        for url in RPC[chain]:
            try:
                j = _post(url, payload, 30)
                if not isinstance(j, list):
                    raise RuntimeError(f"non-batch response: {j}")
                out = [None] * len(calls)
                for item in j:
                    idx = item.get("id")
                    if not isinstance(idx, int):
                        continue
                    if "result" in item:
                        out[idx] = item["result"]
                    elif "revert" not in str(item.get("error")).lower():
                        # not a revert — e.g. an endpoint without archive
                        # state ("missing trie node") — try the next one
                        raise RuntimeError(f"item error: {item.get('error')}")
                return out
            except Exception as e:
                last_err = e
                continue
        time.sleep(1 + attempt)
    if len(calls) > 25:
        mid = len(calls) // 2
        return rpc_batch(chain, calls[:mid]) + rpc_batch(chain, calls[mid:])
    out = []
    for to, data, block in calls:
        try:
            out.append(rpc_call(chain, to, data, block))
        except Exception as e:
            if "revert" in str(e).lower():
                out.append(None)
            else:
                raise RuntimeError(
                    f"batch transport failure for {chain}: {e} "
                    f"(batch had failed with: {last_err})")
    return out


def _decode_string(hexdata):
    h = hexdata[2:]
    length = int(h[64:128], 16)
    return bytes.fromhex(h[128:128 + length * 2]).decode()


def _decode_int256(word):
    v = int(word, 16)
    return v - (1 << 256) if v >= (1 << 255) else v


def read_feed(chain, address):
    """Return dict(description, decimals, answer(float), raw, updated_at, round_id)."""
    decimals = int(rpc_call(chain, address, SEL_DECIMALS), 16)
    description = _decode_string(rpc_call(chain, address, SEL_DESCRIPTION))
    h = rpc_call(chain, address, SEL_LATEST_ROUND)[2:]
    round_id = int(h[0:64], 16)
    answer = _decode_int256(h[64:128])
    updated_at = int(h[192:256], 16)
    return {
        "chain": chain,
        "address": address,
        "description": description,
        "decimals": decimals,
        "raw": answer,
        "answer": answer / 10 ** decimals,
        "updated_at": updated_at,
        "age_h": round((time.time() - updated_at) / 3600, 2),
        "round_id": round_id,
    }


SEL_CONVERT_TO_ASSETS = "0x07a2d13a"  # convertToAssets(uint256)
SEL_PRICE = "0xa035b1fe"              # price()


def _state_dict(chain, address, description, decimals, raw):
    """Result shape for instantaneous (timestamp-less) on-chain state."""
    now = int(time.time())
    return {
        "chain": chain, "address": address, "description": description,
        "decimals": decimals, "raw": raw, "answer": raw / 10 ** decimals,
        "updated_at": now, "age_h": 0.0, "round_id": 0,
    }


def read_vault(chain, address):
    """ERC-4626 vault rate via convertToAssets(1 share)."""
    decimals = int(rpc_call(chain, address, SEL_DECIMALS), 16)
    raw = int(rpc_call(chain, address,
                       SEL_CONVERT_TO_ASSETS + f"{10 ** decimals:064x}"), 16)
    return _state_dict(chain, address, "ERC-4626 convertToAssets", decimals, raw)


def read_price(chain, address, scale):
    """Bare price() oracle (no other interface), value scaled by 10**scale."""
    raw = int(rpc_call(chain, address, SEL_PRICE), 16)
    return _state_dict(chain, address, "price() oracle", scale, raw)


def read_source(feed):
    """Read a FEEDS entry through its declared reader."""
    reader = feed.get("reader", "aggregator")
    if reader == "erc4626":
        return read_vault(feed["chain"], feed["address"])
    if reader == "price":
        return read_price(feed["chain"], feed["address"], feed.get("scale", 18))
    d = read_feed(feed["chain"], feed["address"])
    if d["updated_at"] == 0:  # NAV-style aggregators report no timestamp
        d["updated_at"] = int(time.time())
        d["age_h"] = 0.0
    return d


def fetch_all(feeds=FEEDS):
    out = {}
    for name, f in feeds.items():
        try:
            d = read_source(f)
            d.update(kind=f["kind"], base=f["base"], quote=f["quote"], ok=True)
        except Exception as e:
            d = dict(chain=f["chain"], address=f["address"], kind=f["kind"],
                     base=f["base"], quote=f["quote"], ok=False, error=str(e))
        out[name] = d
    return out


def main():
    data = fetch_all()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=2))
        return
    print(f"{'feed':16} {'chain':9} {'kind':10} {'answer':>14} {'age(h)':>7}  description")
    for name, d in data.items():
        if d["ok"]:
            print(f"{name:16} {d['chain']:9} {d['kind']:10} {d['answer']:>14.8f} {d['age_h']:>7.1f}  {d['description']}")
        else:
            print(f"{name:16} {d['chain']:9} {d['kind']:10} {'ERROR':>14}          {d['error'][:60]}")


if __name__ == "__main__":
    main()

# --------------------------------------------------------------------------
# NOTES
# * Every feed here is a proxy; latestRoundData() returns
#   (roundId, answer, startedAt, updatedAt, answeredInRound). Market feeds use
#   8 decimals, exchange-rate feeds use 18.
# * Heartbeats are 24h with 0.05% (exrate) / 0.25-0.5% (market) deviation, so
#   ages of 10-20h are normal, not stale.
# * reUSD: Chainlink only publishes reUSD/USD as a Data Stream
#   ("reusd-usd-exchangerate-streams"), which is off-chain and needs Data
#   Streams API credentials. Options for step 2: (a) get Streams creds,
#   (b) read the Curve reUSD/scrvUSD pool price_oracle() on Ethereum,
#   (c) use Resupply's own oracle contract.
# * SVR-suffixed duplicates of USDT/USDC/GHO feeds on Ethereum (Smart Value
#   Recapture, for Aave liquidations) were skipped on purpose — same price,
#   different delivery path.
# --------------------------------------------------------------------------
