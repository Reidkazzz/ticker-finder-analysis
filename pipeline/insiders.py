"""Open-market insider purchases (Form 4) and new 5%+ stakes (Schedule 13D) from EDGAR's daily index."""
import datetime as dt
import json
import math
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, Throttled, sec_get

TIER1 = 10_000_000
TIER2 = 1_000_000
TIER2_STAKE = 0.20

# Securities other than common stock: what was paid for them says nothing about the listed shares. A US
# filer's depositary shares are slices of a preferred.
_NOT_COMMON = re.compile(r"prefer|\bpfd\b|\bnotes?\b|debenture|\bbonds?\b|warrant|\brights?\b|deposit[ao]ry", re.I)


def _not_common(title):
    # Only the lead counts: 'Common Stock and associated Preferred Stock Purchase Rights' is common stock.
    return bool(_NOT_COMMON.search(re.split(r"[(,]|\b(?:and|with|including)\b", title, maxsplit=1, flags=re.I)[0]))


def _index_url(day, name):
    return f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{(day.month - 1) // 3 + 1}/{name}"


def _posted_days(day):
    """Days in `day`'s quarter whose form index EDGAR lists as posted, or None if the listing can't be read."""
    try:
        items = json.loads(sec_get(_index_url(day, "index.json")))["directory"]["item"]
    except NotFound:
        return set()  # nothing posted in this quarter yet
    except Throttled:
        raise
    except Exception as e:
        print(f"  Could not read EDGAR's list of daily indexes ({e!r}); requesting each day directly", flush=True)
        return None
    return {m.group(1) for it in items if (m := re.fullmatch(r"form\.(\d{8})\.idx", it.get("name") or ""))}


def _index_lines(day):
    try:
        text = sec_get(_index_url(day, f"form.{day:%Y%m%d}.idx")).decode("latin-1")
    except NotFound:
        return None
    out = []
    for line in text.splitlines():
        m = re.match(r"^(\S+(?: \S+)?)\s{2,}(.+?)\s{2,}(\d+)\s+(\d{8})\s+(edgar/\S+\.txt)", line)
        if m:
            out.append({"form": m.group(1).strip(), "cik": int(m.group(3)), "date": m.group(4), "path": m.group(5)})
    return out


def _txt(el, path):
    node = el.find(path)
    return (node.text or "").strip() if node is not None and node.text else ""


def _num(el, path):
    try:
        return float(_txt(el, path))
    except ValueError:
        return None


def _words(s):
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def _holding(t):
    """Which of the owner's holdings a row adds to: the security, direct or indirect, and through whom. Funds often
    write 'See footnotes' for every holding, so the footnotes are what tell two funds' holdings apart."""
    title = re.split(r",|\(|\bpar value\b", _txt(t, "./securityTitle/value"), maxsplit=1, flags=re.I)[0]
    notes = sorted(n.get("id", "") for n in t.findall("./ownershipNature/natureOfOwnership/footnoteId"))
    return "|".join(_words(x) for x in (title, _txt(t, "./ownershipNature/directOrIndirectOwnership/value"),
                                         _txt(t, "./ownershipNature/natureOfOwnership/value"), " ".join(notes)))


def _coarse(holding):
    """The holding without its footnote numbers."""
    return holding and holding.rsplit("|", 1)[0]


def parse_form4(raw):
    """Returns the filing's open-market purchases (transaction code P) or []."""
    i, j = raw.find("<ownershipDocument>"), raw.find("</ownershipDocument>")
    if i < 0 or j < 0:
        return []
    doc = ET.fromstring(raw[i : j + len("</ownershipDocument>")])
    buys = []
    for t in doc.findall("./nonDerivativeTable/nonDerivativeTransaction"):
        if _txt(t, "./transactionCoding/transactionCode") != "P":
            continue
        if _not_common(_txt(t, "./securityTitle/value")):
            continue
        if _txt(t, "./transactionAmounts/transactionAcquiredDisposedCode/value") not in ("A", ""):
            continue
        shares = _num(t, "./transactionAmounts/transactionShares/value")
        price = _num(t, "./transactionAmounts/transactionPricePerShare/value")
        if not shares or not price or price <= 0:
            continue
        buys.append({
            "date": _txt(t, "./transactionDate/value")[:10],
            "shares": shares,
            "price": price,
            "after": _num(t, "./postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
            "holding": _holding(t),
        })
    if not buys:
        return []
    owners = []
    for o in doc.findall("./reportingOwner"):
        rel = o.find("./reportingOwnerRelationship")
        roles = []
        if rel is not None:
            title = _txt(rel, "./officerTitle")
            if _txt(rel, "./isOfficer") in ("1", "true"):
                roles.append(title or "Officer")
            if _txt(rel, "./isDirector") in ("1", "true"):
                roles.append("Director")
            if _txt(rel, "./isTenPercentOwner") in ("1", "true"):
                roles.append("10% owner")
            if not roles and _txt(rel, "./isOther") in ("1", "true"):
                roles.append(_txt(rel, "./otherText") or "Other")
        owners.append({"name": _txt(o, "./reportingOwnerId/rptOwnerName"), "roles": roles})
    return [{
        "issuer_cik": int(_txt(doc, "./issuer/issuerCik") or 0),
        "issuer": _txt(doc, "./issuer/issuerName"),
        "symbol": _txt(doc, "./issuer/issuerTradingSymbol").upper(),
        "owner": " / ".join(o["name"] for o in owners if o["name"]),
        "owners": [o["name"] for o in owners if o["name"]],
        "roles": sorted({r for o in owners for r in o["roles"]}),
        "buys": buys,
    }]


def parse_13d(raw):
    if "<edgarSubmission" not in raw:
        return None
    cls = re.search(r"<securitiesClassTitle>(.*?)</securitiesClassTitle>", raw, re.S)
    if cls and _not_common(cls.group(1)):
        return None  # a stake in a preferred or a note, not in the listed shares
    pct = [float(x) for x in re.findall(r"<percentOfClass>\s*([\d.]+)\s*</percentOfClass>", raw)]
    name = re.search(r"<issuerName>(.*?)</issuerName>", raw, re.S)
    cik = re.search(r"<issuerCIK>(\d+)</issuerCIK>", raw)
    filer = re.search(r"FILED BY:.*?COMPANY CONFORMED NAME:\s*(.+?)\n", raw, re.S)
    event = re.search(r"<dateOfEvent>(.*?)</dateOfEvent>", raw)
    purpose = re.search(r"<transactionPurpose>(.*?)</transactionPurpose>", raw, re.S)
    people = re.findall(r"<reportingPersonName>(.*?)</reportingPersonName>", raw)
    if not cik:
        return None
    clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&[a-z#0-9]+;", " ", s)).strip()
    return {
        "issuer_cik": int(cik.group(1)),
        "issuer": clean(name.group(1)) if name else "",
        "filer": clean(filer.group(1)) if filer else (clean(people[0]) if people else ""),
        "percent": max(pct) if pct else None,
        "event_date": event.group(1).strip() if event else None,
        "purpose": clean(purpose.group(1))[:280] if purpose else "",
    }


def load_cache(cache_path):
    if os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
            cache.setdefault("days_done", [])
            cache.setdefault("form4", [])
            cache.setdefault("13d", [])
            return cache
        except (OSError, ValueError):
            pass  # a corrupt cache is rebuilt from EDGAR rather than failing the run
    return {"days_done": [], "form4": [], "13d": []}


def pending_days(cache, days, today):
    """Weekdays in the window, today included, that have not been fully scanned yet, newest first."""
    done = set(cache["days_done"])
    out = []
    for n in range(0, days + 1):
        day = today - dt.timedelta(days=n)
        if day.weekday() < 5 and f"{day:%Y%m%d}" not in done:
            out.append(f"{day:%Y-%m-%d}")
    return out


def _save(cache, cache_path, done, no_buys, keep):
    cache["no_buys"] = {a: d for a, d in no_buys.items() if d >= keep}
    cache["days_done"] = sorted(d for d in done if d >= keep)
    cache["form4"] = [x for x in cache["form4"] if x["filed"] >= keep]
    cache["13d"] = [x for x in cache["13d"] if x["filed"] >= keep]
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    os.replace(tmp, cache_path)  # never leave a half-written cache behind


def update_cache(cache_path, days, today=None, time_budget=None):
    """Adds new filings to the on-disk cache and drops anything older than `days`.

    Scans the newest days first and saves after each one, so a slow or throttled run still keeps
    the most recent filings. A day is only marked done when every filing on it was downloaded;
    anything that failed is retried on the next run. Returns (cache, days_skipped).
    """
    today = today or dt.date.today()
    started = time.monotonic()
    cache = load_cache(cache_path)
    done = set(cache["days_done"])
    no_buys = dict(cache.get("no_buys") or {})  # accession -> filing day, for filings with no purchases
    keep = f"{today - dt.timedelta(days=days):%Y%m%d}"

    todo = []
    for n in range(0, days + 1):  # newest first
        day = today - dt.timedelta(days=n)
        key = f"{day:%Y%m%d}"
        # Today and yesterday are always rechecked: late filings land in them after the first scan.
        if day.weekday() < 5 and not (key in done and day < today - dt.timedelta(days=1)):
            todo.append(day)

    stop = threading.Event()  # set once the SEC keeps refusing or the time budget runs out

    def over_budget():
        return bool(time_budget) and time.monotonic() - started > time_budget

    def halted():
        if over_budget():
            stop.set()
        return stop.is_set()

    def grab(path):
        if halted():
            return path, None
        try:
            return path, sec_get("https://www.sec.gov/Archives/" + path).decode("latin-1")
        except Throttled:
            stop.set()
        except Exception:
            pass  # counted as failed, so the day is retried next run
        return path, None

    posted = {}  # (year, quarter) -> days whose index is posted, or None when the listing could not be read

    def listed(day):
        q = (day.year, (day.month - 1) // 3)
        if q not in posted:
            posted[q] = _posted_days(day)
        return posted[q]

    skipped = []
    for i, day in enumerate(todo):
        key = f"{day:%Y%m%d}"
        lines = known = None
        try:
            if not halted():
                listed(today)
                known = listed(day)
                if known is None or key in known:
                    lines = _index_lines(day)
        except Throttled:
            stop.set()
        except Exception as e:  # one unreadable index should not end the whole scan
            print(f"  {day}: could not read the index ({e!r}); it will be retried next run", flush=True)
            continue
        if stop.is_set():
            skipped = [f"{d:%Y-%m-%d}" for d in todo[i:]]
            break
        if lines is None:
            # EDGAR posts each weekday's index around 10 pm New York time, and none on holidays. An unlisted
            # day is a holiday once a later day's index is up; until then it may simply not be posted yet.
            if known is None:
                holiday = day < today - dt.timedelta(days=1)
            else:
                holiday = key not in known and any(k > key for ks in posted.values() if ks for k in ks)
            if holiday:
                done.add(key)
            continue
        # The index lists each filing once per party (company and every filer), so dedupe by accession number.
        seen = {_acc(x["path"]) for x in cache["form4"]} | {_acc(x["path"]) for x in cache["13d"]} | set(no_buys)
        f4 = {_acc(l["path"]): l["path"] for l in lines if l["form"] == "4"}
        d13 = {_acc(l["path"]): l["path"] for l in lines if l["form"] in ("SC 13D", "SCHEDULE 13D")}
        f4_paths = [p for a, p in sorted(f4.items()) if a not in seen]
        d13_paths = [p for a, p in sorted(d13.items()) if a not in seen]
        print(f"  {day}: {len(f4_paths)} new Form 4, {len(d13_paths)} new 13D filings", flush=True)

        failed = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            for path, raw in pool.map(grab, f4_paths):
                if not raw:
                    failed += 1
                    continue
                try:
                    found = parse_form4(raw)
                except ET.ParseError:
                    found = []
                for p in found:
                    cache["form4"].append({**p, "path": path, "filed": key})
                if not found:
                    no_buys[_acc(path)] = key  # remember it so a rerun of the same day skips it
            for path, raw in pool.map(grab, d13_paths):
                if not raw:
                    failed += 1
                    continue
                rec = parse_13d(raw)
                if rec:
                    cache["13d"].append({**rec, "path": path, "filed": key})
                else:
                    no_buys[_acc(path)] = key
        if not failed:
            done.add(key)
        elif not stop.is_set():
            print(f"    {failed} filings could not be downloaded; they will be retried next run", flush=True)
        _save(cache, cache_path, done, no_buys, keep)
        if stop.is_set():  # the rest of this day's filings were never requested
            skipped = [f"{d:%Y-%m-%d}" for d in todo[i:]]
            break

    if skipped:
        why = "Time limit reached" if over_budget() else "SEC is still refusing requests"
        print(f"  {why}; stopping here. {len(skipped)} days will be scanned on the next run", flush=True)
    _save(cache, cache_path, done, no_buys, keep)
    return cache, skipped


def _acc(path):
    """edgar/data/123/0001234567-26-000123.txt -> 0001234567-26-000123"""
    return path.rsplit("/", 1)[-1].removesuffix(".txt")


_SUFFIXES = re.compile(r"\b(Llc|Lp|Llp|Ltd|Inc|Plc|Ii|Iii|Iv|Nv|Sa|Ag|Ab|Us|Usa|Mgmt)\b\.?")


_ENTITY = re.compile(
    r"\b(LLC|LP|LLP|L\.P\.|LTD|INC|CORP|CORPORATION|CO|COMPANY|PLC|FUND|FUNDS|CAPITAL|PARTNERS|HOLDINGS?|TRUST|GROUP|"
    r"MANAGEMENT|MGMT|ADVISORS|ADVISERS|INVESTMENTS?|BANK|FOUNDATION|ASSOCIATES|VENTURES|EQUITY|SA|AG|NV|AB|LIMITED|"
    r"FINANCIAL|INSURANCE|ASSET|OPPORTUNITIES|MASTER|OFFSHORE|ESTATE|FAMILY)\b",
    re.I,
)


_GEN = {"JR", "JR.", "SR", "SR.", "II", "III", "IV"}
_PARTICLES = {"DI", "DE", "DA", "DEL", "DELLA", "DOS", "DU", "VAN", "VON", "DER", "LA", "ST", "ST."}


def _pretty(name):
    """EDGAR lists people as 'LAST FIRST MIDDLE', often in capitals.
    'GORDON CARL L' -> 'Carl L Gordon'; 'Smith John A Jr' -> 'John A Smith Jr'; 'ORBIMED ADVISORS LLC' -> 'Orbimed Advisors LLC';
    'DI BARTOLO JAMES P' -> 'James P Di Bartolo'."""
    if not name:
        return name
    parts = name.split()
    et_al = [p.upper() for p in parts[-2:]] == ["ET", "AL"]
    if et_al:
        parts = parts[:-2]
    if not _ENTITY.search(name) and 2 <= len(parts) <= 5 and "," not in name:
        gen = [parts.pop()] if parts[-1].upper() in _GEN and len(parts) > 2 else []
        n = 1  # surname words; particles stay with it as long as a surname and a first name remain after them
        while n < len(parts) - 1 and parts[n - 1].upper() in _PARTICLES:
            n += 1
        parts = parts[n:] + parts[:n] + gen
    if name.isupper():
        parts = [p.title() if len(p) > 1 and p.upper() not in _GEN - {"JR", "JR.", "SR", "SR."} else p for p in parts]
    t = " ".join(parts) + (" and others" if et_al else "")
    if not name.isupper():
        return t
    return _SUFFIXES.sub(lambda m: {"Inc": "Inc", "Ltd": "Ltd", "Mgmt": "Mgmt"}.get(m.group(1), m.group(1).upper()) + m.group(0)[len(m.group(1)):], t)


def _iso(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _link(path):
    # edgar/data/CIK/0001234567-26-000123.txt -> filing index page
    m = re.match(r"edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt", path)
    if not m:
        return "https://www.sec.gov/Archives/" + path
    return f"https://www.sec.gov/Archives/edgar/data/{m.group(1)}/{m.group(2).replace('-', '')}/{m.group(2)}-index.htm"


def _listing(by_cik, cik, symbol="", paid=None):
    """The listing a filing belongs to: the filing's own ticker when it is one of the issuer's listings. Failing that,
    or when the filing names several ('BRK.A, BRK.B'), the class priced nearest to what was paid, else the shortest ticker."""
    listings = by_cik.get(cik) or []
    named = {s.replace("-", ".").replace("/", ".") for s in re.split(r"[\s,;]+", symbol or "")}
    gap = lambda u: abs(math.log(u["price"] / paid)) if paid and u.get("price") else 0
    return min([u for u in listings if u["symbol"] in named] or listings,
               key=lambda u: (gap(u), len(u["symbol"]), u["symbol"]), default=None)


def build(cache, universe, window_days, today=None):
    today = today or dt.date.today()
    since = f"{today - dt.timedelta(days=window_days):%Y%m%d}"
    by_cik = {}
    for u in universe.values():
        by_cik.setdefault(u["cik"], []).append(u)

    # A fund, its manager and its partners often each file a Form 4 for the same purchase: identical trades that
    # leave the same holding. Merge those filers instead of adding them up. People who each report identical
    # trades (a bank's executives buying under the same plan) stay apart unless one filer is an entity.
    merged, seen_acc = {}, set()
    for f in sorted(cache["form4"], key=lambda f: (f["filed"], _acc(f["path"]))):
        acc = _acc(f["path"])
        if f["filed"] < since or acc in seen_acc:
            continue
        seen_acc.add(acc)
        # A late filing can report trades from before the window; only purchases inside it count.
        buys = [t for t in f["buys"] if t["date"] >= _iso(since)]
        if not buys:
            continue
        names = f.get("owners") or [n for n in (f["owner"] or "").split(" / ") if n]
        trades = tuple(sorted((t["date"], round(t["shares"], 2), round(t["price"], 4), t.get("after") or 0) for t in buys))
        group = merged.setdefault((f["issuer_cik"], trades), [])
        m = next((m for m in group if set(names) & set(m["owners"]) or any(_ENTITY.search(n) for n in names + m["owners"])), None)
        if m:
            m["owners"] += [n for n in names if n not in m["owners"]]
            m["roles"] = sorted(set(m["roles"]) | set(f["roles"]))
        else:
            group.append({**f, "buys": buys, "owners": list(names)})

    # Oldest purchases first, so each holding's starting size comes from the owner's first buy in the window.
    filings = sorted((m for g in merged.values() for m in g),
                     key=lambda f: (min(t["date"] for t in f["buys"]), f["filed"], _acc(f["path"])))
    companies = {}
    for f in filings:
        shares = sum(t["shares"] for t in f["buys"])
        paid = sum(t["shares"] * t["price"] for t in f["buys"]) / shares if shares else None
        u = _listing(by_cik, f["issuer_cik"], f.get("symbol"), paid)
        if not u:
            continue  # funds, private companies and OTC listings are out of scope
        c = companies.setdefault(u["symbol"], {
            "symbol": u["symbol"],
            "name": u["name"],
            "sector": u["sector"],
            "industry": u["industry"],
            "price": u["price"],
            "mcap": u["mcap"],
            "buyers": {},
        })
        owner = " / ".join(_pretty(o) for o in f["owners"][:2]) + (f" +{len(f['owners']) - 2}" if len(f["owners"]) > 2 else "")
        b = c["buyers"].setdefault(owner, {"name": owner, "roles": set(), "rows": [], "filed": "", "filing": None,
                                           "split": set()})
        b["roles"].update(f["roles"])
        if f["filed"] >= b["filed"]:  # link the most recent filing
            b["filed"], b["filing"] = f["filed"], _link(f["path"])
        b["rows"] += sorted(f["buys"], key=lambda t: t["date"])
        # Footnote numbers change from one filing to the next, so they only tell holdings apart where one filing
        # describes two of them alike (two funds that each 'See footnotes').
        hs = {t.get("holding") for t in f["buys"]}
        b["split"] |= {_coarse(h) for h in hs if sum(_coarse(x) == _coarse(h) for x in hs) > 1}

    out = []
    for c in companies.values():
        buyers = []
        for b in c["buyers"].values():
            rows = sorted(b["rows"], key=lambda t: t["date"])
            shares = sum(t["shares"] for t in rows)
            value = sum(t["shares"] * t["price"] for t in rows)
            # Each holding (direct, a trust, a spouse...) starts from what it held before its first purchase here.
            base, bought = {}, {}
            for t in rows:
                h = t.get("holding")
                h = h if _coarse(h) in b["split"] else _coarse(h)
                bought[h] = bought.get(h, 0) + t["shares"]
                if base.get(h) is None:
                    base[h] = None if t.get("after") is None else max(t["after"] - bought[h], 0)
            before = None if None in base.values() else sum(base.values())
            avg = value / shares if shares else None
            stake = shares / before if before else None
            buyers.append({
                "name": b["name"],
                "roles": sorted(b["roles"]),
                "shares": round(shares),
                "value": round(value),
                "avg_price": round(avg, 4) if avg else None,
                "new_position": before == 0,
                "stake_increase": round(stake, 4) if stake is not None else None,
                "first": rows[0]["date"],
                "last": rows[-1]["date"],
                "filed": _iso(b["filed"]),
                "filing": b["filing"],
            })
        buyers.sort(key=lambda x: (x["filed"], x["value"]), reverse=True)
        total = sum(x["value"] for x in buyers)
        big_stake = any(x["new_position"] or (x["stake_increase"] or 0) >= TIER2_STAKE for x in buyers)
        tier = 1 if total >= TIER1 else 2 if (total >= TIER2 or big_stake) else 3
        shares = sum(x["shares"] for x in buyers)
        avg = total / shares if shares else None
        c.update({
            "buyers": buyers,
            "total_value": total,
            "avg_price": round(avg, 4) if avg else None,
            "vs_paid": (c["price"] / avg - 1) if c["price"] and avg else None,
            "tier": tier,
            "big_stake": big_stake,
            "last": max(x["last"] for x in buyers if x["last"]),
            "filed": max(x["filed"] for x in buyers),
        })
        out.append(c)
    # Newest filings first; within a day, the biggest purchases lead.
    out.sort(key=lambda c: (c["filed"], -c["tier"], c["total_value"]), reverse=True)

    stakes, seen_acc = [], set()
    for s in cache["13d"]:
        acc = _acc(s["path"])
        if s["filed"] < since or acc in seen_acc:
            continue
        seen_acc.add(acc)
        u = _listing(by_cik, s["issuer_cik"])
        if not u:
            continue
        stakes.append({
            "symbol": (u or {}).get("symbol"),
            "name": (u or {}).get("name") or s["issuer"],
            "sector": (u or {}).get("sector") or "Other",
            "price": (u or {}).get("price"),
            "filer": _pretty(s["filer"]),
            "percent": s["percent"],
            "event_date": s["event_date"],
            "filed": _iso(s["filed"]),
            "purpose": s["purpose"],
            "filing": _link(s["path"]),
        })
    stakes.sort(key=lambda s: s["filed"], reverse=True)
    return {"window_days": window_days, "tiers": {"tier1": TIER1, "tier2": TIER2, "tier2_stake": TIER2_STAKE},
            "companies": out, "stakes": stakes}
