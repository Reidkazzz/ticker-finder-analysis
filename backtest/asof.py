"""The pipeline's own code, run as it would have run on a past date. One date of one variant per process:

    python -c "..." (screener.py starts it) --code <root> --date 2023-09-29 --label before [--set module.ATTR=<json>]

It puts the variant's code root first on sys.path, so `import pipeline` loads that copy, and replaces the network layer
(pipeline.net's sec_json, sec_get, web_json and web_get wherever a module imported them, and net._fetch under them) with
a router that answers:

- the SEC frames and companyconcept APIs (and companyfacts, the pipeline's fallback) from the facts filed before the
  date (facts.py), so fundamentals.load_fundamentals(today=date) reads what it would have read then;
- the SEC's company records (submissions: SIC codes, where a company is incorporated and based) as the pipeline does,
  with today's records, since the SEC keeps no history of them; each is requested once and kept in SHARED_DIR;
- anything else by raising, so no answer from today can slip in unseen. Every blocked request is counted and fails the
  run.

Every cache file a pipeline module reads or writes (module-level paths under the root's .cache/) goes to a folder for
that date and code, emptied before the run, so the live caches are never touched and dates never mix; the SEC company
record caches (registrations.json, sic.json) go to SHARED_DIR, since they are today's anyway.

Then it runs, for that date: fundamentals.load_fundamentals and build.py's metrics loop (its own statements, cut from
build.main from the load_fundamentals call through the loop that calls fundamentals.derive, and run as they are), the
insider purchases of the 30 days before (form345.py, through the pipeline's own insiders.parse_form4 and build), and
screener.run with the date's listings, market values and prices (prices.py, market_state) and an analyst counter that
knows no analysts. What does not depend on the variant's code is kept per date (STATE_DIR/<date>/): the listings,
prices and market values (market.json), under a key of the backtest code and data they are worked out from
(market_key: prices.py, facts.py, data.py, market_state itself, loc.json, the snapshot of today's listings, the facts
index and the charts), rebuilt when the key changes. The metrics are kept per key of everything they are worked out
from (metrics_key: the variant's fundamentals.py, report.py, filers.py, build.py and reit_types.py and any --set on
them, this harness's facts.py and asof.py, loc.json, the SIC codes kept for the listings' companies, and the listings
but for their prices and market values), and the insider purchases per hash of insiders.py, this harness's form345.py
and the date's listings and prices, so a variant that only changes screener.py reruns in seconds.

build.py's statements step (statements.py, from the main branch of September 2026: the revenue of a company that tags
it under no label the pipeline reads, read from the income statement pages of its filings on EDGAR) is never run: it
comes after the metrics loop the harness cuts from build.main, and its pages are fetched live from EDGAR as they stand
today, which nothing in companyfacts.zip can make point in time. Those companies stay without figures in the backtest,
as they were before that step (metrics_block says so in the run's log where the code has it).
"""
import argparse
import ast
import copy
import datetime as dt
import hashlib
import inspect
import json
import os
import pickle
import re
import shutil
import sys
import threading
import time
import traceback
import types
from collections import Counter

from . import RESULTS_DIR, ROOT, SHARED_DIR, STATE_DIR, use_code

EXIT_MISSING_TAGS = 3  # the process's exit code when the facts index lacks tags the code asks for (screener.py adds them)
METRIC_FILES = ("fundamentals.py", "report.py", "filers.py", "build.py", "reit_types.py")
METRIC_MODULES = {"fundamentals", "report", "filers", "build", "reit_types", "net"}
# The backtest's own code the metrics are worked out through: the rebuilt SEC answers (facts.py) and the harness that
# runs build.py's statements on them (asof.py). Part of the metrics' cache key with the files above.
HARNESS_FILES = ("facts.py", "asof.py")
# What the metrics read of a listing (load_fundamentals takes its market value only to order lookups, every one of which
# is made): everything but its price and market value, save the share count the two imply, which tells whether a net
# income read for a whole group is the listed shares' (build.company_metrics, fundamentals._owners_net_income).
LISTING_PRICE_FIELDS = ("price", "mcap", "mcap_basis")
INSIDER_FILES = ("insiders.py",)
# The backtest's own code the insider purchases are worked out through (the data sets turned back into Form 4s).
INSIDER_HARNESS_FILES = ("form345.py",)
# The backtest's own code the date's listings, prices and market values are worked out by (market_key).
MARKET_FILES = ("prices.py", "facts.py", "data.py")
SHARED_FILES = ("registrations.json", "sic.json")  # today's SEC company records, the same for every date
MODULES = ("net", "reit_types", "fundamentals", "report", "filers", "market", "universe", "insiders", "screener", "news",
           "statements", "build")
_SET = re.compile(r"(\w+)\.(\w+)(?:\[(?:\"([^\"]*)\"|'([^']*)'|([^\]]*))\])?=(.*)", re.S)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Blocked(Exception):
    """A request the backtest does not answer."""


def parse_set(text):
    """'screener.RULES["min_net_margin"]=0.03' or 'screener.SCORE_WEIGHTS={"Few analysts": 0}' ->
    (module, attribute, key or None, value). The value is JSON."""
    m = _SET.fullmatch(text.strip())
    if not m:
        raise ValueError(f"--set {text!r}: expected module.ATTR=<json> or module.ATTR[key]=<json>")
    mod, attr, k1, k2, k3, value = m.groups()
    key = next((k for k in (k1, k2, k3) if k is not None), None)
    return mod, attr, key, json.loads(value)


def apply_sets(sets, mods):
    """Sets each override on the imported modules. A key, or a JSON object given for a dict attribute, updates that dict
    in place (so every holder of it sees the change); any other value replaces the attribute."""
    for text in sets:
        mod, attr, key, value = parse_set(text)
        if mod not in mods or not hasattr(mods[mod], attr):
            raise ValueError(f"--set {text!r}: pipeline.{mod} has no {attr}")
        old = getattr(mods[mod], attr)
        if key is not None:
            if not isinstance(old, dict):
                raise ValueError(f"--set {text!r}: pipeline.{mod}.{attr} is not a dict")
            if key not in old:
                raise ValueError(f"--set {text!r}: pipeline.{mod}.{attr} has no key {key!r}")
            old[key] = value
        elif isinstance(old, dict) and isinstance(value, dict):
            unknown = set(value) - set(old)
            if unknown:
                raise ValueError(f"--set {text!r}: pipeline.{mod}.{attr} has no keys {sorted(unknown)}")
            old.update(value)
        else:
            setattr(mods[mod], attr, value)


def code_hash(root, files, sets, modules, extra=()):
    """A short hash of the variant's pipeline `files` (a file the code lacks counts as missing), the --set overrides
    of `modules`, and any `extra` (name, bytes) pairs."""
    h = hashlib.sha256()
    for n in files:
        try:
            with open(os.path.join(root, "pipeline", n), "rb") as fh:
                body = fh.read().replace(b"\r\n", b"\n")
        except FileNotFoundError:
            body = b"(missing)"
        h.update(n.encode() + b"\0" + body)
    for s in sorted(sets):
        if parse_set(s)[0] in modules:
            h.update(s.encode())
    for name, blob in extra:
        h.update(b"\0" + name.encode() + b"\0" + blob)
    return h.hexdigest()[:12]


def metrics_key(root, sets, uni):
    """The metrics' cache key: the variant's METRIC_FILES and --set overrides of METRIC_MODULES, the harness's own
    HARNESS_FILES, the locations the frames give (loc.json), the SIC codes kept for the listings' companies (the shared
    sic.json, codes only: its lookup dates change without changing a code) and the listings as the metrics read them
    (all but LISTING_PRICE_FIELDS, with the share count they imply)."""
    from .data import LOC
    extra = []
    for n in HARNESS_FILES:
        with open(os.path.join(ROOT, "backtest", n), "rb") as fh:
            extra.append((f"backtest/{n}", fh.read().replace(b"\r\n", b"\n")))
    with open(LOC, "rb") as fh:
        extra.append(("loc.json", fh.read()))
    try:
        with open(os.path.join(SHARED_DIR, "sic.json"), encoding="utf-8") as fh:
            sic = json.load(fh)
    except (OSError, ValueError):
        sic = {}
    ciks = sorted({str(u["cik"]) for u in uni.values()})
    extra.append(("sic", json.dumps({c: sic[c].get("sic") for c in ciks if c in sic}, sort_keys=True).encode()))
    listings = {s: {**{k: v for k, v in u.items() if k not in LISTING_PRICE_FIELDS},
                    "listed_shares": round(u["mcap"] / u["price"]) if u.get("mcap") and u.get("price") else None}
                for s, u in uni.items()}
    extra.append(("listings", json.dumps(listings, sort_keys=True, default=str).encode()))
    return code_hash(root, METRIC_FILES, sets, METRIC_MODULES, extra)


# ---------------------------------------------------------------------------------------------------------------------
# The network layer.

class Router:
    FRAMES = re.compile(r"https://data\.sec\.gov/api/xbrl/frames/([^/]+)/([^/]+)/([^/]+)/([^/]+)\.json")
    CONCEPT = re.compile(r"https://data\.sec\.gov/api/xbrl/companyconcept/CIK(\d{10})/([^/]+)/([^/]+)\.json")
    COMPANYFACTS = re.compile(r"https://data\.sec\.gov/api/xbrl/companyfacts/CIK(\d{10})\.json")
    SUBMISSIONS = re.compile(r"https://data\.sec\.gov/submissions/CIK(\d{10})\.json")

    def __init__(self, net, day, facts=None, frames=None, loc=None, facts_tags=(), record=False):
        self.net, self.day, self.facts, self.frames, self.loc = net, day, facts, frames or {}, loc or {}
        self.facts_tags, self.record = tuple(facts_tags), record
        self.frame_jobs, self.missing, self.blocked = [], set(), []
        self.counts = Counter()
        self.lock = threading.Lock()

    def _block(self, url):
        with self.lock:
            self.blocked.append(url)
        raise Blocked(url)

    def sec_json(self, url):
        m = self.FRAMES.fullmatch(url)
        if m:
            return self._frame(url, *m.groups())
        m = self.CONCEPT.fullmatch(url)
        if m:
            return self._concept(url, int(m.group(1)), m.group(2), m.group(3))
        m = self.COMPANYFACTS.fullmatch(url)
        if m:
            return self._companyfacts(url, int(m.group(1)))
        m = self.SUBMISSIONS.fullmatch(url)
        if m:
            return _submissions.get(int(m.group(1)), url)
        return self._block(url)

    def sec_get(self, url):
        m = self.SUBMISSIONS.fullmatch(url)
        if m:
            return json.dumps(_submissions.get(int(m.group(1)), url)).encode()
        return self._block(url)

    def web_json(self, url, opener=None, headers=None):
        return self._block(url)

    def web_get(self, url, opener=None, headers=None):
        return self._block(url)

    def _frame(self, url, tax, tag, unit, period):
        job = (tax, tag, unit, period)
        with self.lock:
            self.counts["frames"] += 1
            if self.record:
                self.frame_jobs.append(job)
        if self.record:
            raise self.net.NotFound(url)
        if job not in self.frames:
            if f"{tax}/{tag}" not in self.facts.tags:
                with self.lock:
                    self.missing.add(f"{tax}/{tag}")
                raise Blocked(url)
            with self.lock:
                self.frames.update(self.facts.frames(self.day, [job], self.loc, log=log))
        rows = self.frames[job]
        if not rows:
            raise self.net.NotFound(url)  # nobody had filed the line item for the period: the API's 404
        from .facts import frame_answer
        return frame_answer(rows, self.facts, self.loc, tax, tag, unit, period)

    def _concept(self, url, cik, tax, tag):
        with self.lock:
            self.counts["companyconcept"] += 1
        if self.record:
            raise self.net.NotFound(url)
        if f"{tax}/{tag}" not in self.facts.tags:
            with self.lock:
                self.missing.add(f"{tax}/{tag}")
            raise Blocked(url)
        answer = self.facts.concept(cik, tax, tag, self.day)
        if answer is None:
            raise self.net.NotFound(url)
        return answer

    def _companyfacts(self, url, cik):
        with self.lock:
            self.counts["companyfacts"] += 1
        if self.record:
            raise self.net.NotFound(url)
        gaap = {}
        for tag in self.facts_tags:
            if f"us-gaap/{tag}" not in self.facts.tags:
                with self.lock:
                    self.missing.add(f"us-gaap/{tag}")
                continue
            a = self.facts.concept(cik, "us-gaap", tag, self.day)
            if a:
                gaap[tag] = {"units": a["units"]}
        if not gaap:
            raise self.net.NotFound(url)
        return {"cik": cik, "entityName": self.facts.entities.get(cik, ""), "facts": {"us-gaap": gaap}}


class Submissions:
    """Today's SEC company records (data.sec.gov/submissions), requested live once each and kept in SHARED_DIR without
    their filing lists: the pipeline reads a company's SIC code, where it is incorporated and its business address."""
    PATH = os.path.join(SHARED_DIR, "submissions.json")

    def __init__(self):
        self.lock = threading.Lock()
        self.data, self.live, self.dirty = None, None, False

    def _load(self):
        if self.data is None:
            try:
                with open(self.PATH, encoding="utf-8") as fh:
                    self.data = json.load(fh)
            except (OSError, ValueError):
                self.data = {}

    def get(self, cik, url):
        with self.lock:
            self._load()
            rec = self.data.get(str(cik))
        if rec is None:
            if self.live is None:
                raise Blocked(url)
            try:
                full = self.live(url)
                rec = {k: v for k, v in full.items() if k != "filings"}
            except _net.NotFound:
                rec = {"missing": True}
            with self.lock:
                self.data[str(cik)] = rec
                self.dirty = True
        if rec.get("missing"):
            raise _net.NotFound(url)
        return rec

    def save(self):
        with self.lock:
            if self.dirty:
                os.makedirs(SHARED_DIR, exist_ok=True)
                with open(self.PATH + ".tmp", "w", encoding="utf-8") as fh:
                    json.dump(self.data, fh, separators=(",", ":"))
                os.replace(self.PATH + ".tmp", self.PATH)
                self.dirty = False


_submissions = Submissions()
_net = None
_router = None


def install(mods):
    """Points every pipeline module's network functions at the current router (_router), and net._fetch at a guard
    that lets only the live submissions requests through."""
    global _net
    _net = net = mods["net"]
    if getattr(net, "_backtest_installed", False):
        return
    net._backtest_installed = True
    # The live path for company records: net's own sec_get (its user agent, rate limit and backoff), whose _fetch is
    # the guard below, which lets these through to the real one.
    live_fetch, live_sec_get = net._fetch, net.sec_get
    _submissions.live = lambda url: json.loads(live_sec_get(url))

    def fetch(url, headers, opener=None, timeout=30, retries=4):
        if Router.SUBMISSIONS.fullmatch(url):
            return live_fetch(url, headers, opener=opener, timeout=timeout, retries=retries)
        with _router.lock:
            _router.blocked.append(url)
        raise Blocked(url)

    route = {
        "sec_json": lambda url: _router.sec_json(url),
        "sec_get": lambda url: _router.sec_get(url),
        "web_json": lambda url, opener=None, headers=None: _router.web_json(url, opener, headers),
        "web_get": lambda url, opener=None, headers=None: _router.web_get(url, opener, headers),
    }
    for m in list(sys.modules.values()):
        if (getattr(m, "__name__", None) or "").split(".")[0] == "pipeline":
            for name, fn in route.items():
                if name in vars(m):
                    setattr(m, name, fn)
    net._fetch = fetch


def redirect_caches(mods, root, folder):
    """Points each module-level path under the root's .cache/ at `folder` (the SEC company record caches at SHARED_DIR,
    seeded once from this worktree's own caches), and build.py's output folder at a scratch folder. Returns
    {module.attribute: new path}."""
    root_cache = os.path.normcase(os.path.join(os.path.abspath(root), ".cache"))
    done = {}
    for name, m in mods.items():
        # From the paths the module had at import, so a second redirect (discovery, then the run) starts from them.
        orig = _original_paths.setdefault(name, {a: v for a, v in vars(m).items() if isinstance(v, str)})
        for attr, val in orig.items():
            if not os.path.isabs(val):
                continue
            norm = os.path.normcase(os.path.abspath(val))
            if norm == root_cache:
                new = folder
            elif norm.startswith(root_cache + os.sep):
                base = os.path.basename(val)
                if base in SHARED_FILES:
                    new = os.path.join(SHARED_DIR, base)
                    live = os.path.join(ROOT, ".cache", base)
                    if not os.path.exists(new) and os.path.exists(live):
                        os.makedirs(SHARED_DIR, exist_ok=True)
                        shutil.copyfile(live, new)
                else:
                    new = os.path.join(folder, os.path.relpath(val, os.path.join(os.path.abspath(root), ".cache")))
            elif name == "build" and attr == "OUT":
                new = os.path.join(folder, "site-data")
            else:
                continue
            setattr(m, attr, new)
            done[f"{name}.{attr}"] = new
    # A default argument bound to a cache path at import would escape the redirect: fail loudly instead.
    for name, m in mods.items():
        for attr, fn in vars(m).items():
            if isinstance(fn, types.FunctionType) and fn.__module__ == m.__name__:
                for d in fn.__defaults__ or ():
                    if isinstance(d, str) and os.path.normcase(os.path.abspath(d)).startswith(root_cache) \
                            and os.path.isabs(d):
                        raise RuntimeError(f"pipeline.{name}.{attr} has a cache path as a default argument: {d}")
    return done


_original_paths = {}


class NoAnalysts:
    """Analyst counts are not point in time (Yahoo gives today's only), so the backtest knows none: "Few analysts"
    gives nobody points and nobody is flagged undiscovered, in every variant alike."""

    def counts(self, symbols, workers=4):
        return {}

    def count(self, sym):
        return None


# ---------------------------------------------------------------------------------------------------------------------
# One date.

# The calls that mark build.main's loop over the listings that works out each one's figures: fundamentals.derive
# itself (the code at 719a878 and this worktree's), or company_metrics, the function the main branch of September 2026
# moved the derive call and its checks into.
METRICS_CALLS = ("fundamentals.derive(", "company_metrics(")
# The statements step of the main branch (statements.read: revenue from the income statement pages of a filing,
# fetched live from EDGAR), which the harness never runs (see the module's docstring).
STATEMENTS_CALL = "statements.read("


def metrics_block(build):
    """build.main's statements from the load_fundamentals call through the loop that works out each listing's figures
    (METRICS_CALLS), compiled to run as they are (in build's own globals, with `uni` and `today` given), and the
    block's text. Anything after that loop is not run: build.main's statements step (STATEMENTS_CALL), where the code
    has one, comes after it, so the companies whose revenue only that step reads have no figures here, as before the
    step was added; the text says so."""
    src = inspect.getsource(build)
    tree = ast.parse(src)
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
    seg = lambda n: ast.get_source_segment(src, n) or ""
    body = main.body
    try:
        i0 = next(i for i, n in enumerate(body) if "load_fundamentals(" in seg(n))
        i1 = next(i for i, n in enumerate(body) if i >= i0 and isinstance(n, ast.For)
                  and any(c in seg(n) for c in METRICS_CALLS))
    except StopIteration:
        raise RuntimeError("build.main no longer has a load_fundamentals call followed by the loop working out each "
                           f"listing's figures (calling one of {METRICS_CALLS}): update asof.metrics_block")
    if any(STATEMENTS_CALL in seg(n) for n in body[i0:i1 + 1]):
        raise RuntimeError("build.main reads income statements (statements.read) inside the metrics block the harness "
                           "runs; it can't be made point in time: update asof.metrics_block to cut it out")
    skipped = [i for i, n in enumerate(body) if i > i1 and STATEMENTS_CALL in seg(n)]
    text = "\n".join(seg(n) for n in body[i0:i1 + 1])
    if skipped:
        text += ("\n# (not run: build.main's statements step, statements.read, which reads revenue from income "
                 "statement pages fetched live from EDGAR and can't be made point in time)")
    mod = ast.Module(body=body[i0:i1 + 1], type_ignores=[])
    return compile(mod, build.__file__, "exec"), text, bool(skipped)


def discover(mods, day, folder):
    """The frames load_fundamentals asks for on `day` [(taxonomy, tag, unit, period)], found by running it with every
    frame answered as missing (so it has no companies and makes no other request)."""
    global _router
    _router = Router(mods["net"], day, record=True)
    redirect_caches(mods, _root, folder)
    out = io_quiet(lambda: mods["fundamentals"].load_fundamentals(day, {}, reits=set()))
    del out
    jobs = list(dict.fromkeys(_router.frame_jobs))
    return jobs


def io_quiet(fn):
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn()


def needed_tags(mods, jobs):
    f = mods["fundamentals"]
    from .facts import SHARE_TAGS
    return ({f"{t}/{g}" for t, g, _, _ in jobs} | {f"us-gaap/{t}" for t in getattr(f, "FACTS_TAGS", ())}
            | set(SHARE_TAGS))


def _file_bytes(path):
    with open(path, "rb") as fh:
        return fh.read().replace(b"\r\n", b"\n")


def market_key():
    """The key market.json and returns.json are kept under: a hash of the backtest code they are worked out by
    (MARKET_FILES and market_state itself), loc.json, the snapshot of today's listings, the facts index (its tags and
    the companyfacts.zip it was built from) and the charts (how many, their total size and newest change). A mismatch
    rebuilds them (the old file kept beside, under its own key)."""
    from . import CHARTS_DIR, UNIVERSE
    from .data import LOC
    from .facts import load_manifest
    h = hashlib.sha256()
    for n in MARKET_FILES:
        h.update(n.encode() + b"\0" + _file_bytes(os.path.join(ROOT, "backtest", n)))
    h.update(inspect.getsource(market_state).encode())
    for path in (LOC, UNIVERSE):
        h.update(b"\0" + _file_bytes(path))
    man = load_manifest()
    h.update(json.dumps({"tags": man.get("tags"), "zip": man.get("zip")}, sort_keys=True).encode())
    n = size = newest = 0
    for e in os.scandir(CHARTS_DIR):
        st = e.stat()
        n, size, newest = n + 1, size + st.st_size, max(newest, int(st.st_mtime))
    h.update(f"charts {n} {size} {newest}".encode())
    return h.hexdigest()[:12]


def load_keyed(path, key):
    """A state file's contents if it was made under `key`, else None (the file is then moved aside, under its own key,
    to be rebuilt)."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    if state.get("key") == key:
        return state
    stem, ext = os.path.splitext(path)
    os.replace(path, f"{stem}.{state.get('key') or 'unkeyed'}{ext}")
    log(f"  {os.path.basename(path)} was made under another key: rebuilding it")
    return None


def market_state(day, facts, snapshot=None):
    """The listings on `day` and their prices (STATE_DIR/<day>/market.json, made once per market_key): today's
    listings that had a weekly close that week, with that close and the market value then (prices.market_value), on
    charts passed through the price gate (prices.clean, with the company's public floats and share counts from
    facts.Facts.history). A listing whose chart has a split that touches the date's year of closes or its 52 weeks
    after and that no test can read (prices.unclear_for) is left out, with the reason in "left_out_listings". A listing
    whose price level on the date can't be told (prices.level_unknown_for: an unreadable split after the 52 weeks, or
    an unlisted adjustment the closes' digits couldn't place) keeps its return but gets no market value
    ("none_level_unknown"). "shares" holds how each listing's share count was chosen (prices.share_count),
    "splits_fixed" what the gate did to each chart, and "basis" how many listings took each source of share count or had
    no market value, and why."""
    from . import data, prices
    path = os.path.join(STATE_DIR, day.isoformat(), "market.json")
    key = market_key()
    state = load_keyed(path, key)
    if state is not None:
        return state
    snapshot = snapshot or data.load_universe()
    loc = data.load_loc()
    uni, px, basis, missing, left, fixed, shares = {}, {}, Counter(), Counter(), {}, {}, {}
    for sym, u in sorted(snapshot.items()):
        raw = data.load_chart(sym)
        if not raw:
            missing["no chart"] += 1
            continue
        chart, rep = prices.clean(raw, facts.history(u["cik"]))
        p = prices.prices_at(chart, day)
        if not p:
            missing["no close that week"] += 1
            continue
        bad = prices.unclear_for(rep, day)
        if bad:
            missing["a split no test can read, in the year before or after"] += 1
            left[sym] = {"why": "split_unclear", "splits": bad}
            continue
        if rep["fixed"] or rep["bars"] or rep["adjustments"] or rep["level_unknown"]:
            fixed[sym] = {k: rep[k] for k in ("fixed", "bars", "adjustments", "level_unknown") if rep[k]}
        level = prices.level_unknown_for(rep, day)
        if level:
            mcap, how, detail = None, "none_level_unknown", level
        else:
            mcap, how, detail = prices.market_value(u, chart, p, facts.share_counts(u["cik"], day), day,
                                                    loc.get(u["cik"]), facts.public_float(u["cik"], day))
        basis[how] += 1
        uni[sym] = {**u, "price": p["price"], "mcap": mcap, "mcap_basis": how}
        if detail:
            shares[sym] = detail
        px[sym] = {k: p[k] for k in ("price", "high52", "low52", "weekly", "splits")}
    state = {"date": day.isoformat(), "key": key, "universe": uni, "prices": px, "basis": dict(basis),
             "left_out": dict(missing), "left_out_listings": left, "splits_fixed": fixed, "shares": shares,
             "rules": {"max_age_days": prices.MAX_AGE_DAYS, "agree": prices.AGREE, "slip_tol": prices.SLIP_TOL,
                       "split_checked": prices.SPLIT_CHECKED, "split_margin": prices.SPLIT_MARGIN,
                       "split_margin_share": prices.SPLIT_MARGIN_SHARE, "split_max": prices.SPLIT_MAX,
                       "jump_weeks": prices.JUMP_WEEKS, "grid_weeks": prices.GRID_WEEKS,
                       "float_typical": prices.FLOAT_TYPICAL, "float_plausible": prices.FLOAT_PLAUSIBLE,
                       "whole_share": prices.WHOLE_SHARE}}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(state, fh, separators=(",", ":"))
    os.replace(path + ".tmp", path)
    log(f"  listings on {day}: {len(uni)} of {len(snapshot)} today's; market value from "
        + ", ".join(f"{k} {v}" for k, v in sorted(basis.items())) + f"; left out: {dict(missing)}; "
        f"charts with closes corrected for a split: {len(fixed)}")
    return state


_root = None


def metrics_state(mods, day, uni, facts, loc, jobs, sets, tag):
    """(the metrics of `day`, their cache key): kept in STATE_DIR/<day>/metrics_<tag>_<key>.pkl (metrics_key), worked
    out where missing. A run that looks up SIC codes not kept yet changes the key it was made under (the codes are part
    of it), so the metrics are kept under the key as it stands after the run, which the next run computes."""
    state_dir = os.path.join(STATE_DIR, day.isoformat())
    mhash = metrics_key(_root, sets, uni)
    mpath = os.path.join(state_dir, f"metrics_{tag}_{mhash}.pkl")
    if os.path.exists(mpath):
        with open(mpath, "rb") as fh:
            m = pickle.load(fh)
        log(f"  metrics for {day} from {os.path.basename(mpath)}")
        return m, mhash
    m = _metrics(mods, day, uni, facts, loc, jobs, os.path.join(state_dir, "pipeline_cache", f"{tag}_{mhash}"))
    after = metrics_key(_root, sets, uni)
    if after != mhash:
        log(f"  SIC codes looked up in the run: metrics kept under {after}, not {mhash}")
        mhash = after
    mpath = os.path.join(state_dir, f"metrics_{tag}_{mhash}.pkl")
    with open(mpath + ".tmp", "wb") as fh:
        pickle.dump(m, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(mpath + ".tmp", mpath)
    return m, mhash


def run(root, day, label, sets=(), out=None, universe_pickle=None, no_prices=False, metrics_only=False):
    """Runs one date of one variant and writes its payload (RESULTS_DIR/<label>_<date>.payload.json, or `out`)."""
    global _router, _root
    _root = os.path.abspath(root)
    use_code(_root)
    import importlib
    mods = {}
    for name in MODULES:
        try:
            mods[name] = importlib.import_module(f"pipeline.{name}")
        except ModuleNotFoundError:
            pass
    apply_sets(sets, mods)
    install(mods)
    from . import data
    from .facts import Facts
    facts, loc = Facts(), data.load_loc()
    state_dir = os.path.join(STATE_DIR, day.isoformat())
    os.makedirs(state_dir, exist_ok=True)
    started = time.time()
    tag = "today" if universe_pickle else "asof"

    # The tags the code asks for must all be indexed before anything else reads the index.
    scratch = os.path.join(state_dir, "discover")
    shutil.rmtree(scratch, ignore_errors=True)
    jobs = discover(mods, day, scratch)
    shutil.rmtree(scratch, ignore_errors=True)
    missing = needed_tags(mods, jobs) - facts.tags
    if missing:
        _missing_exit(state_dir, missing)

    if universe_pickle:
        with open(universe_pickle, "rb") as fh:
            uni = pickle.load(fh)
        market = {"universe": uni, "prices": {}, "basis": {"listing": len(uni)}, "left_out": {}}
    else:
        market = market_state(day, facts)
    uni, px = market["universe"], ({} if no_prices else market["prices"])

    m, mhash = metrics_state(mods, day, uni, facts, loc, jobs, sets, tag)
    metrics = m["metrics"]
    if metrics_only:
        log(f"  metrics for {day} kept under {mhash} ({len(metrics)} listings with figures)")
        return {"label": label, "date": day.isoformat(), "metrics": mhash, "with_figures": len(metrics)}

    # The insider purchases of the 30 days before, for code whose screener.run takes them (insiders=).
    screener = mods["screener"]
    takes = "insiders" in inspect.signature(screener.run).parameters
    # The insider purchases depend on insiders.py and on the date's listings and prices (which listing of a company a
    # filing goes to): keyed by both.
    ihash = code_hash(_root, INSIDER_FILES, sets, {"insiders"},
                      [(f"backtest/{n}", _file_bytes(os.path.join(ROOT, "backtest", n))) for n in INSIDER_HARNESS_FILES]
                      + [("listings", json.dumps({s: [u.get("cik"), u.get("price")] for s, u in uni.items()},
                                                 sort_keys=True).encode())])
    # Worked out for every variant (a second a date), so a report can show how the picks of code that doesn't use them
    # stood; only a missing data set is allowed to skip them, and only for such code.
    try:
        insiders = insiders_state(mods, day, uni, os.path.join(state_dir, f"insiders_{tag}_{ihash}.json"))
    except FileNotFoundError as e:
        if takes:
            raise
        log(f"  insider purchases skipped: {e}")
        insiders = None

    # The screener, with the SEC company records live where not kept yet (registrations).
    _router = Router(mods["net"], day, facts=facts, loc=loc)
    redirect_caches(mods, _root, os.path.join(state_dir, "pipeline_cache", f"screener_{tag}"))
    kw = {"insiders": insiders} if takes else {}
    log(f"Running the screener as of {day} ({label})" + (" with insider purchases" if kw else ""))
    scr = screener.run(copy.deepcopy(uni), metrics, px, NoAnalysts(), **kw)
    _submissions.save()
    if _router.blocked:
        raise RuntimeError(f"{len(_router.blocked)} requests were blocked while screening, e.g. {_router.blocked[:3]}")
    log(f"  {len(scr['results'])} companies passed, {len(scr.get('unverified') or [])} unverified")
    payload = {"label": label, "date": day.isoformat(), "root": _root, "sets": list(sets),
               "hashes": {"metrics": mhash, "insiders": ihash, "market": market.get("key"),
                          "screener": code_hash(_root, ("screener.py",), sets, {"screener"})},
               "insiders_passed": bool(kw), "insiders": insiders, "with_figures": len(metrics), "listings": len(uni),
               "market_basis": market.get("basis"), "metrics_log": m.get("counts"), "payload": scr,
               "seconds": round(time.time() - started)}
    out = out or os.path.join(RESULTS_DIR, f"{label}_{day.isoformat()}.payload.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, default=_jsonable, separators=(",", ":"))
    os.replace(out + ".tmp", out)
    return payload


def insiders_state(mods, day, uni, path):
    """{symbol: {"tier", "avg_price", "total_value"}} of the listed companies with open-market purchases on Form 4s
    filed in the 30 days before `day`, as build.py hands them to screener.run (kept in `path`)."""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            ins = json.load(fh)
    else:
        from . import form345
        built, forms = form345.insiders_at(mods["insiders"], day, uni)
        ins = {"forms": forms, "companies": built["companies"]}
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump(ins, fh, default=_jsonable, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        log(f"  insider purchases before {day}: {forms} Form 4s read, {len(ins['companies'])} listed companies "
            f"with purchases ({sum(1 for c in ins['companies'] if c['tier'] <= 2)} in tiers 1 and 2)")
    return {c["symbol"]: {"tier": c["tier"], "avg_price": c["avg_price"], "total_value": c["total_value"]}
            for c in ins["companies"]}


def _metrics(mods, day, uni, facts, loc, jobs, folder):
    """load_fundamentals and build.py's metrics loop as of `day`, on the facts filed before it."""
    global _router
    log(f"Rebuilding the SEC frames as of {day}")
    frames = facts.frames(day, jobs, loc, log=log)
    _router = Router(mods["net"], day, facts=facts, frames=frames, loc=loc,
                     facts_tags=getattr(mods["fundamentals"], "FACTS_TAGS", ()))
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder, exist_ok=True)
    moved = redirect_caches(mods, _root, folder)
    log(f"  caches redirected: {', '.join(f'{k} -> {os.path.relpath(v, ROOT)}' for k, v in sorted(moved.items()))}")
    code, text, skipped = metrics_block(mods["build"])
    if skipped:
        log("  build.main's statements step (statements.read: revenue from income statement pages fetched live from "
            "EDGAR) is not run: it can't be made point in time")
    ns = dict(vars(mods["build"]))
    ns.update(uni=copy.deepcopy(uni), today=day, step=log,
              args=types.SimpleNamespace(limit=0, insider_days=30, insider_minutes=75, skip_news=True))
    log(f"Loading SEC financial statements as of {day}")
    exec(code, ns)
    _submissions.save()
    if _router.missing:
        _missing_exit(os.path.join(STATE_DIR, day.isoformat()), _router.missing)
    if _router.blocked:
        raise RuntimeError(f"{len(_router.blocked)} requests were blocked, e.g. {_router.blocked[:5]}")
    fund, metrics = ns["fund"], ns["metrics"]
    counts = {"companies": len(fund["companies"]), "with_figures": len(metrics),
              "banks": sum(1 for x in metrics.values() if x.get("bank")),
              "reits": sum(1 for x in metrics.values() if x.get("reit")), "requests": dict(_router.counts),
              "frames": len(frames), "frame_rows": sum(len(v) for v in frames.values()), "years": fund.get("years")}
    log(f"  financials for {len(metrics)} listings ({counts})")
    counts["statements_step_skipped"] = skipped
    return {"metrics": metrics, "sic": ns.get("sic"), "counts": counts, "block": text}


def _missing_exit(state_dir, missing):
    with open(os.path.join(state_dir, "missing_tags.json"), "w", encoding="utf-8") as fh:
        json.dump(sorted(missing), fh)
    log(f"  {len(missing)} tags not indexed yet: {sorted(missing)[:10]}...")
    sys.exit(EXIT_MISSING_TAGS)


def _jsonable(o):
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    return str(o)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--out")
    ap.add_argument("--universe-pickle", help="a pickled universe dict to use instead of the date's listings")
    ap.add_argument("--no-prices", action="store_true", help="give screener.run no price histories")
    ap.add_argument("--metrics-only", action="store_true", help="work out and keep the metrics, then stop")
    args = ap.parse_args()
    try:
        run(args.code, dt.date.fromisoformat(args.date), args.label, args.set, args.out, args.universe_pickle,
            args.no_prices, args.metrics_only)
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
