"""Open-market insider purchases (Form 4) and new 5%+ stakes (Schedule 13D) from EDGAR's daily index."""
import datetime as dt
import json
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, sec_get

TIER1 = 10_000_000
TIER2 = 1_000_000
TIER2_STAKE = 0.20


def _index_lines(day):
    q = (day.month - 1) // 3 + 1
    url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"
    try:
        text = sec_get(url).decode("latin-1")
    except NotFound:
        return None  # weekend, holiday, or not published yet
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


def update_cache(cache_path, days, today=None):
    """Adds new filings to the on-disk cache, then drops anything older than `days`."""
    today = today or dt.date.today()
    cache = {"days_done": [], "form4": [], "13d": []}
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
    done = set(cache["days_done"])
    no_buys = dict(cache.get("no_buys") or {})  # accession -> filing day, for filings with no purchases
    cutoff = today - dt.timedelta(days=days)

    for n in range(days, -1, -1):
        day = today - dt.timedelta(days=n)
        key = f"{day:%Y%m%d}"
        if day.weekday() >= 5 or (key in done and day < today - dt.timedelta(days=1)):
            continue
        lines = _index_lines(day)
        if lines is None:
            continue
        # The index lists each filing once per party (company and every filer), so dedupe by accession number.
        seen = {_acc(x["path"]) for x in cache["form4"]} | {_acc(x["path"]) for x in cache["13d"]} | set(no_buys)
        f4 = {_acc(l["path"]): l["path"] for l in lines if l["form"] == "4"}
        d13 = {_acc(l["path"]): l["path"] for l in lines if l["form"] in ("SC 13D", "SCHEDULE 13D")}
        f4_paths = [p for a, p in sorted(f4.items()) if a not in seen]
        d13_paths = [p for a, p in sorted(d13.items()) if a not in seen]
        print(f"  {day}: {len(f4_paths)} new Form 4, {len(d13_paths)} new 13D filings", flush=True)

        def grab(path):
            try:
                return path, sec_get("https://www.sec.gov/Archives/" + path).decode("latin-1")
            except Exception:
                return path, None

        with ThreadPoolExecutor(max_workers=8) as pool:
            for path, raw in pool.map(grab, f4_paths):
                if not raw:
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
                rec = parse_13d(raw) if raw else None
                if rec:
                    cache["13d"].append({**rec, "path": path, "filed": key})
        done.add(key)

    keep = f"{cutoff:%Y%m%d}"
    # Only the last two days are ever rescanned, so older "no purchase" markers can go.
    recent = f"{today - dt.timedelta(days=4):%Y%m%d}"
    cache["no_buys"] = {a: d for a, d in no_buys.items() if d >= recent}
    cache["days_done"] = sorted(d for d in done if d >= keep)
    cache["form4"] = [x for x in cache["form4"] if x["filed"] >= keep]
    cache["13d"] = [x for x in cache["13d"] if x["filed"] >= keep]
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    return cache


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


def _pretty(name):
    """EDGAR lists people as 'LAST FIRST MIDDLE', often in capitals.
    'GORDON CARL L' -> 'Carl L Gordon'; 'Smith John A Jr' -> 'John A Smith Jr'; 'ORBIMED ADVISORS LLC' -> 'Orbimed Advisors LLC'."""
    if not name:
        return name
    parts = name.split()
    et_al = [p.upper() for p in parts[-2:]] == ["ET", "AL"]
    if et_al:
        parts = parts[:-2]
    if not _ENTITY.search(name) and 2 <= len(parts) <= 5 and "," not in name:
        gen = [parts.pop()] if parts[-1].upper() in _GEN and len(parts) > 2 else []
        parts = parts[1:] + parts[:1] + gen
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


def build(cache, universe, window_days, today=None):
    today = today or dt.date.today()
    since = f"{today - dt.timedelta(days=window_days):%Y%m%d}"
    by_cik = {u["cik"]: u for u in universe.values()}

    # A fund, its manager and its partners often each file a Form 4 for the same purchase.
    # Identical trades in the same stock are one purchase: merge the filers instead of adding them up.
    merged, seen_acc = {}, set()
    for f in cache["form4"]:
        acc = _acc(f["path"])
        if f["filed"] < since or acc in seen_acc:
            continue
        seen_acc.add(acc)
        trades = tuple(sorted((t["date"], round(t["shares"], 2), round(t["price"], 4)) for t in f["buys"]))
        k = (f["issuer_cik"], trades)
        names = f.get("owners") or [n for n in (f["owner"] or "").split(" / ") if n]
        if k in merged:
            m = merged[k]
            m["owners"] += [n for n in names if n not in m["owners"]]
            m["roles"] = sorted(set(m["roles"]) | set(f["roles"]))
            m["filed"] = min(m["filed"], f["filed"])
        else:
            merged[k] = {**f, "owners": list(names)}

    companies = {}
    for f in merged.values():
        u = by_cik.get(f["issuer_cik"]) or universe.get(f["symbol"])
        if not u:
            continue  # funds, private companies and OTC listings are out of scope
        key = f["issuer_cik"]
        c = companies.setdefault(key, {
            "symbol": u["symbol"],
            "name": u["name"],
            "sector": u["sector"],
            "industry": u["industry"],
            "price": u["price"],
            "mcap": u["mcap"],
            "buyers": {},
        })
        owner = " / ".join(_pretty(o) for o in f["owners"][:2]) + (f" +{len(f['owners']) - 2}" if len(f["owners"]) > 2 else "")
        b = c["buyers"].setdefault(owner, {"name": owner, "roles": set(), "shares": 0.0, "value": 0.0,
                                           "before": None, "after": None, "first": None, "last": None, "filings": [],
                                           "filed": f["filed"]})
        b["roles"].update(f["roles"])
        b["filed"] = max(b["filed"], f["filed"])
        for t in sorted(f["buys"], key=lambda t: t["date"]):
            b["shares"] += t["shares"]
            b["value"] += t["shares"] * t["price"]
            if t["after"] is not None:
                if b["before"] is None:
                    b["before"] = max(t["after"] - t["shares"], 0)
                b["after"] = t["after"]
            b["first"] = min(filter(None, [b["first"], t["date"]]))
            b["last"] = max(filter(None, [b["last"], t["date"]]))
        b["filings"].append(_link(f["path"]))

    out = []
    for c in companies.values():
        buyers = []
        for b in c["buyers"].values():
            avg = b["value"] / b["shares"] if b["shares"] else None
            stake = None
            if b["before"] is not None:
                stake = None if b["before"] == 0 else b["shares"] / b["before"]
            buyers.append({
                "name": b["name"],
                "roles": sorted(b["roles"]),
                "shares": round(b["shares"]),
                "value": round(b["value"]),
                "avg_price": round(avg, 4) if avg else None,
                "new_position": b["before"] == 0,
                "stake_increase": round(stake, 4) if stake is not None else None,
                "first": b["first"],
                "last": b["last"],
                "filed": _iso(b["filed"]),
                "filing": b["filings"][-1],
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
        u = by_cik.get(s["issuer_cik"])
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
