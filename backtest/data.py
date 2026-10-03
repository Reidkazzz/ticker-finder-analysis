"""The backtest's downloads, each made once and kept under .cache/backtest/:

- the SEC's bulk companyfacts.zip, every XBRL fact every company filed, each with its filing date (about 1.4 GB);
- a snapshot of today's listings (universe.load_universe), the companies the backtest can test on every date;
- Yahoo Finance's weekly chart of each listing from August 2021 to today, with its dividends and stock splits;
- the SEC's insider transaction data sets for the third quarter of 2022 to 2025 (Forms 3, 4 and 5 as tables);
- the location (loc) the SEC's frames give each company, which the bulk companyfacts file doesn't carry.

    python -m backtest.data companyfacts|universe|charts|form345|loc|all

Every request goes through pipeline.net (TOOLS_COMMIT's copy), except companyfacts.zip's, streamed to disk with the
same SEC user agent: the SEC's with its user agent and rate limit, Yahoo's
with its browser client, held to CHART_RATE requests a second here since another job may share the connection.
"""
import argparse
import datetime as dt
import json
import os
import pickle
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import CACHE, CHARTS_DIR, COMPANYFACTS, FORM345_DIR, UNIVERSE, use_tools_code

# The charts start a year before the first date's year of prices (its 52-week high and 52 weekly closes) plus a margin,
# and run to today, which covers the fourth date's 52 weeks after it.
CHART_START = dt.datetime(2021, 8, 1, tzinfo=dt.timezone.utc)
CHART_RATE = 3  # Yahoo requests a second, at most
CHART_TRIES = 4  # attempts per chart, the pipeline's own retries on throttling and server errors aside
FORM345_URL = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets/{q}_form345.zip"
COMPANYFACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
FORM345_QUARTERS = ["2022q3", "2023q3", "2024q3", "2025q3"]
LOC = os.path.join(CACHE, "loc.json")
# Live frames for the companies the replay lacks (those that stopped filing since): a cover page's share count and total
# assets at a few year ends reach nearly every filer of those years.
LOC_FRAMES = ["dei/EntityCommonStockSharesOutstanding/shares/CY2024Q4I", "us-gaap/Assets/USD/CY2024Q4I",
              "us-gaap/Assets/USD/CY2023Q4I", "us-gaap/Assets/USD/CY2022Q4I", "us-gaap/Assets/USD/CY2021Q4I"]


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.replace(tmp, path)


def load_universe():
    """Today's listings, as universe.load_universe gave them when the snapshot was taken: {symbol: {...}}."""
    with open(UNIVERSE, encoding="utf-8") as fh:
        return json.load(fh)["universe"]


def snapshot_day():
    """The day the snapshot of today's listings was taken (in New York, where Nasdaq's market values are set)."""
    with open(UNIVERSE, encoding="utf-8") as fh:
        taken = dt.datetime.fromisoformat(json.load(fh)["taken"])
    return (taken - dt.timedelta(hours=4)).date()


def snapshot_universe(force=False):
    """Takes the snapshot of today's listings once (Nasdaq's screener and the SEC's ticker list)."""
    if os.path.exists(UNIVERSE) and not force:
        return load_universe()
    use_tools_code()
    from pipeline import universe
    uni = universe.load_universe()
    _write_json(UNIVERSE, {"taken": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "universe": uni})
    print(f"universe: {len(uni)} listings", flush=True)
    return uni


def _ysym(sym):
    return sym.replace(".", "-")  # as market.price_history asks Yahoo


def chart_path(sym):
    return os.path.join(CHARTS_DIR, _ysym(sym) + ".json")


def load_chart(sym):
    """A listing's weekly chart as fetch_charts kept it, or None where Yahoo had none."""
    try:
        with open(chart_path(sym), encoding="utf-8") as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        return None
    return None if c.get("missing") else c


def _trim(res):
    """What the backtest reads from a chart answer: the weekly timestamps, closes (split-adjusted) and adjusted closes
    (splits and dividends), the stock splits and dividends, and the exchange's time zone offset."""
    meta = res.get("meta") or {}
    ind = res.get("indicators") or {}
    ev = res.get("events") or {}
    return {
        "ts": res.get("timestamp") or [],
        "close": ((ind.get("quote") or [{}])[0] or {}).get("close") or [],
        "adjclose": ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose") or [],
        "splits": sorted([int(e["date"]), float(e["numerator"]), float(e["denominator"])]
                         for e in (ev.get("splits") or {}).values()),
        "dividends": sorted([int(e["date"]), float(e["amount"])] for e in (ev.get("dividends") or {}).values()),
        "gmtoffset": meta.get("gmtoffset") or 0,
        "currency": meta.get("currency"),
        "fetched": int(time.time()),
    }


def fetch_charts(symbols, workers=3, rate=CHART_RATE):
    """Downloads each listing's weekly chart not already kept (CHARTS_DIR). A symbol Yahoo doesn't know is kept as
    missing, so it isn't asked again; one that keeps failing is left for the next call."""
    use_tools_code()
    from pipeline import net
    os.makedirs(CHARTS_DIR, exist_ok=True)
    limiter = net.RateLimiter(rate)
    end = int(time.time())
    todo = [s for s in symbols if not os.path.exists(chart_path(s))]
    print(f"charts: {len(symbols) - len(todo)} kept, {len(todo)} to fetch", flush=True)
    done, failed, lock = [0], [], threading.Lock()

    def one(sym):
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{_ysym(sym)}?period1={int(CHART_START.timestamp())}"
               f"&period2={end}&interval=1wk&events=div,split")
        for attempt in range(CHART_TRIES):
            limiter.wait()
            try:
                res = net.web_json(url)["chart"]["result"][0]
                _write_json(chart_path(sym), _trim(res))
                break
            except net.NotFound:
                _write_json(chart_path(sym), {"missing": True, "fetched": int(time.time())})
                break
            except Exception as e:  # throttled past the pipeline's own retries, or a broken answer
                if attempt == CHART_TRIES - 1:
                    with lock:
                        failed.append((sym, repr(e)))
                    break
                time.sleep(5 * 3 ** attempt)
        with lock:
            done[0] += 1
            if done[0] % 250 == 0:
                print(f"  {done[0]} of {len(todo)} charts", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, todo))
    if failed:
        print(f"charts: {len(failed)} failed, left for the next call: "
              + ", ".join(s for s, _ in failed[:30]) + (f" (first error {failed[0][1]})" if failed else ""), flush=True)
    return failed


def form345_path(q):
    return os.path.join(FORM345_DIR, f"{q}_form345.zip")


def fetch_companyfacts():
    """Downloads the SEC's bulk companyfacts.zip once (about 1.4 GB), straight to disk rather than through memory, with
    the pipeline's SEC User-Agent. The SEC rebuilds it every night; delete it (and .cache/backtest/facts, the index built
    from it) to take a newer one."""
    if os.path.exists(COMPANYFACTS):
        return
    import shutil
    import urllib.request
    use_tools_code()
    from pipeline import net
    os.makedirs(CACHE, exist_ok=True)
    req = urllib.request.Request(COMPANYFACTS_URL, headers={"User-Agent": net.SEC_UA})
    with urllib.request.urlopen(req, timeout=120) as resp, open(COMPANYFACTS + ".part", "wb") as fh:
        shutil.copyfileobj(resp, fh, 1 << 20)
    os.replace(COMPANYFACTS + ".part", COMPANYFACTS)
    print(f"companyfacts.zip: {os.path.getsize(COMPANYFACTS) / 1e9:.2f} GB", flush=True)


def fetch_form345(quarters=FORM345_QUARTERS):
    """Downloads the SEC's insider transaction data set of each quarter not already kept."""
    use_tools_code()
    from pipeline import net
    os.makedirs(FORM345_DIR, exist_ok=True)
    for q in quarters:
        path = form345_path(q)
        if os.path.exists(path):
            continue
        body = net.sec_get(FORM345_URL.format(q=q))
        with open(path + ".tmp", "wb") as fh:
            fh.write(body)
        os.replace(path + ".tmp", path)
        print(f"form345: {q} {len(body) / 1e6:.1f} MB", flush=True)


def load_loc():
    """{cik: loc} (see build_loc)."""
    with open(LOC, encoding="utf-8") as fh:
        return {int(c): v for c, v in json.load(fh)["loc"].items()}


def build_loc(fund_pkl=None):
    """The location the SEC's frames give each company ("US-CA", "CN", ...), which the frames API adds from the SEC's
    company records and the bulk companyfacts file lacks. It is today's location, not the one of each date: first the
    loc of every company in the live pipeline's last load_fundamentals output (`fund_pkl`, the newest filing's), then,
    for companies not in it, the loc of a few live frames (LOC_FRAMES), newest first."""
    use_tools_code()
    from pipeline import net
    loc, source = {}, {}
    if fund_pkl and os.path.exists(fund_pkl):
        with open(fund_pkl, "rb") as fh:
            fund = pickle.load(fh)
        for cik, c in fund["companies"].items():
            if c.get("loc") and c["loc"] != "-":
                loc[int(cik)] = c["loc"]
        source["fund"] = len(loc)
        del fund
    for f in LOC_FRAMES:
        n = 0
        for d in net.sec_json(f"https://data.sec.gov/api/xbrl/frames/{f}.json")["data"]:
            if d.get("loc") and d["loc"] != "-" and int(d["cik"]) not in loc:
                loc[int(d["cik"])] = d["loc"]
                n += 1
        source[f] = n
    _write_json(LOC, {"built": dt.date.today().isoformat(), "sources": source, "loc": {str(c): v for c, v in loc.items()}})
    print(f"loc: {len(loc)} companies ({source})", flush=True)
    return loc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["companyfacts", "universe", "charts", "form345", "loc", "all"])
    ap.add_argument("--fund-pkl", help="the live run's pickled load_fundamentals output, for build_loc")
    args = ap.parse_args()
    if args.what in ("companyfacts", "all"):
        fetch_companyfacts()
    if args.what in ("universe", "all"):
        snapshot_universe()
    if args.what in ("form345", "all"):
        fetch_form345()
    if args.what in ("loc", "all"):
        build_loc(args.fund_pkl)
    if args.what in ("charts", "all"):
        uni = load_universe()
        for _ in range(2):  # a second round for charts that failed in the first
            if not fetch_charts(sorted(uni)):
                break


if __name__ == "__main__":
    sys.exit(main())
