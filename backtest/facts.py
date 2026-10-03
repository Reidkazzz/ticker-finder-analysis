"""What the SEC's XBRL APIs would have answered on a past date, rebuilt from its bulk companyfacts file.

The pipeline reads its financial figures from two SEC APIs: frames (one line item for one period, every company's
latest figure) and companyconcept (one company's every figure for one line item). The bulk file companyfacts.zip holds,
for each company, every fact of every filing it made, each with the date it was filed; the frames API's own choice is
marked on the fact it shows today by a "frame" label (CY2023, CY2023Q2, CY2023Q2I). So what either API would have
answered on date D can be rebuilt from the facts filed strictly before D:

- companyconcept: the company's facts under that tag filed before D, in the file's order.
- frames: each fact gets the frame period the SEC labels it with. The label sits on the last-filed copy of a span
  (start and end dates) only, so it is carried to every earlier copy of the same span in the same tag; a span no copy
  of which is labeled in its tag takes the label another tag of the company gives the same span, and failing that the
  rule the labels follow (RULES below). Per company and period the last filed fact wins, ties going to the larger
  accession number, as the frames API does; a span the SEC labels in that tag today goes before one it doesn't, since
  today's frame shows that it is the span the SEC takes for the period when both are filed.

It also gives the share counts each listing's market value on D is worked out from (Facts.share_counts, read by
prices.share_count): cover pages, balance sheets, diluted counts, earnings per share and book value filed before D.

Only the tags the pipeline asks for (discovered by running it with its requests recorded, asof.discover) and those of
the share counts (SHARE_TAGS) are indexed, in one pass over the zip for each batch of new tags, into FACTS_DIR: one file
of fixed-size records per tag, grouped by company, and an index of where each company's records start.
"""
import array
import bisect
import datetime as dt
import json
import os
import re
import struct
import sys
import time
import zipfile
from multiprocessing import Pool

from . import COMPANYFACTS, FACTS_DIR

# One fact: company (CIK), unit (UNITS), start and end (day ordinals; start 0 for an instant), value, accession number
# (its 18 digits), date filed (ordinal), fiscal year, fiscal period, form, frame label (label_code), how the label was
# found (PRIO) and the fact's place in the company's list for that tag and unit.
REC = struct.Struct("<iBiidqii2s10sIBI")
UNITS = ("USD", "shares", "pure", "USD/shares")  # the units the pipeline asks the APIs for, and earnings per share
# (indexed for the tags of share_counts only: a tag indexed before it was added has none of its facts)
# How a fact's frame label was found: 3 the SEC labels this very fact, 2 it labels another copy of the same span in the
# same tag, 1 another tag of the company labels the same span, 0 the rule (or no label).
EXACT, SAME_TAG, OTHER_TAG, RULE = 3, 2, 1, 0
# Facts ending before this are left out of the index: the earliest figures the pipeline reads for the first date
# (2022-09-30) are its three years of working capital history before 2018, the calendar year 2015 frame, whose spans end
# from mid-2015.
MIN_END = dt.date(2014, 1, 1).toordinal()
MANIFEST = os.path.join(FACTS_DIR, "manifest.json")
INDEX = os.path.join(FACTS_DIR, "index.pkl")
ENTITIES = os.path.join(FACTS_DIR, "entities.json")
# The share counts the market values on each date are worked out from (Facts.share_counts, prices.share_count).
COVER_TAG = "dei/EntityCommonStockSharesOutstanding"
BALANCE_TAG = "us-gaap/CommonStockSharesOutstanding"
DILUTED_TAG = "us-gaap/WeightedAverageNumberOfDilutedSharesOutstanding"
# Earnings per share and the profit it is worked out on, whose ratio is a share count to check the others against.
EPS_TAGS = ("us-gaap/EarningsPerShareDiluted", "us-gaap/EarningsPerShareBasicAndDiluted", "us-gaap/EarningsPerShareBasic")
PROFIT_TAGS = ("us-gaap/NetIncomeLossAvailableToCommonStockholdersBasic", "us-gaap/NetIncomeLoss")
# Stockholders' equity, the book value a market value far under is taken for a share count given in thousands.
EQUITY_TAG = "us-gaap/StockholdersEquity"
# The public float: the market value of the shares held by non-affiliates as of the last day of the company's second
# fiscal quarter, on the cover page of its 10-K. The SEC's own record of a price level, which the price gate
# (prices.clean) checks each chart's closes against.
FLOAT_TAG = "dei/EntityPublicFloat"
SHARE_TAGS = (COVER_TAG, BALANCE_TAG, DILUTED_TAG) + EPS_TAGS + PROFIT_TAGS + (EQUITY_TAG, FLOAT_TAG)
EPS_MIN = 0.05  # dollars a share either way; rounded to the cent, a smaller one gives the count to 10% or worse
_LABEL = re.compile(r"CY(\d{4})(?:Q([1-4]))?(I)?")
_ACCN = re.compile(r"(\d{10})-(\d{2})-(\d{6})")


def label_code(label):
    """'CY2023' -> 1_02023_0 style integer (kind * 100000 + year * 10 + quarter); 0 for no label. Kind 1 is a year, 2 a
    quarter, 3 an instant."""
    m = _LABEL.fullmatch(label or "")
    if not m:
        return 0
    y, q, i = int(m.group(1)), int(m.group(2) or 0), bool(m.group(3))
    return (3 if i else 2 if q else 1) * 100000 + y * 10 + q


def label_text(code):
    kind, rest = divmod(code, 100000)
    y, q = divmod(rest, 10)
    return f"CY{y}" if kind == 1 else f"CY{y}Q{q}" + ("I" if kind == 3 else "")


def _quarter(day):
    return day.year, (day.month - 1) // 3 + 1


def rule_label(start, end):
    """The frame period the SEC's labels follow for a span (dates), or None: an instant belongs to the calendar quarter
    that holds the day 45 days before it (CY2023Q2I for June 30, and for August 14, but CY2023Q3I for August 15); a
    duration of 80 to 100 days to the calendar quarter holding the day 45 days before its end (CY2023Q2); one of 335 to
    395 days to the calendar year of the day 180 days before its end (CY2023). On a sample of 1,500 companies in the
    September 2026 file these rules gave the SEC's own label for every one of 2.0M labeled instants, 1.08M labeled
    quarters and 0.90M labeled years; the SEC also labels a few spans outside those lengths (0.1%), which only carry
    their own label."""
    if start is None:
        y, q = _quarter(end - dt.timedelta(days=45))
        return f"CY{y}Q{q}I"
    days = (end - start).days
    if 80 <= days <= 100:
        y, q = _quarter(end - dt.timedelta(days=45))
        return f"CY{y}Q{q}"
    if 335 <= days <= 395:
        return f"CY{(end - dt.timedelta(days=180)).year}"
    return None


def accn_int(accn):
    m = _ACCN.fullmatch(accn or "")
    return int("".join(m.groups())) if m else -1


def accn_text(n):
    s = f"{n:018d}"
    return f"{s[:10]}-{s[10:12]}-{s[12:]}"


def ordinal(day):
    return dt.date.fromisoformat(day).toordinal()


def iso(n):
    return dt.date.fromordinal(n).isoformat() if n else None


def tag_path(key):
    tax, tag = key.split("/", 1)
    return os.path.join(FACTS_DIR, tax, tag + ".bin")


# ---------------------------------------------------------------------------------------------------------------------
# Building the index: one pass over the zip for a batch of tags, in worker processes.

_zip = None


def _open_zip(path):
    global _zip
    _zip = zipfile.ZipFile(path)


def _index_members(job):
    """Worker: the records of `tags` (["us-gaap/Revenues", ...]) in a batch of the zip's members, as
    ({tag: [(cik, bytes)]}, {cik: entity name}, check counts)."""
    names, tags = job
    out, entities = {}, {}
    checks = {"labeled": 0, "rule_agrees": 0, "odd_accn": 0}
    wanted = {}
    for key in tags:
        tax, tag = key.split("/", 1)
        wanted.setdefault(tax, {})[tag] = key
    pack = REC.pack
    for name in names:
        try:
            d = json.loads(_zip.read(name))
        except (KeyError, ValueError):
            continue
        cik = d.get("cik")
        if not cik:
            continue
        cik = int(cik)
        entities[cik] = d.get("entityName") or ""
        facts = d.get("facts") or {}
        # The label each span carries in any of the company's tags.
        spans = {}
        for body in facts.values():
            for t in body.values():
                for fl in (t.get("units") or {}).values():
                    for f in fl:
                        if f.get("frame"):
                            spans[(f.get("start"), f["end"])] = f["frame"]
        for tax, tags_of in wanted.items():
            for tag, key in tags_of.items():
                body = (facts.get(tax) or {}).get(tag)
                if not body:
                    continue
                chunk = []
                for u, unit in enumerate(UNITS):
                    fl = (body.get("units") or {}).get(unit)
                    if not fl:
                        continue
                    same = {(f.get("start"), f["end"]): f["frame"] for f in fl if f.get("frame")}
                    for seq, f in enumerate(fl):
                        end = f.get("end")
                        val = f.get("val")
                        if not end or val is None:
                            continue
                        e = dt.date.fromisoformat(end)
                        if e.toordinal() < MIN_END:
                            continue
                        s = dt.date.fromisoformat(f["start"]) if f.get("start") else None
                        span = (f.get("start"), end)
                        if f.get("frame"):
                            lab, prio = f["frame"], EXACT
                            checks["labeled"] += 1
                            checks["rule_agrees"] += rule_label(s, e) == lab
                        elif span in same:
                            lab, prio = same[span], SAME_TAG
                        elif span in spans:
                            lab, prio = spans[span], OTHER_TAG
                        else:
                            lab, prio = rule_label(s, e), RULE
                        a = accn_int(f.get("accn"))
                        if a < 0:
                            checks["odd_accn"] += 1
                            continue
                        try:
                            filed = ordinal(f["filed"])
                        except (KeyError, TypeError, ValueError):
                            continue
                        chunk.append(pack(cik, u, s.toordinal() if s else 0, e.toordinal(), float(val), a, filed,
                                          _fy(f.get("fy")), (f.get("fp") or "").encode()[:2],
                                          (f.get("form") or "").encode()[:10], label_code(lab), prio, seq))
                if chunk:
                    out.setdefault(key, []).append((cik, b"".join(chunk)))
    return out, entities, checks


def _fy(v):
    try:
        v = int(v or 0)
    except (TypeError, ValueError):
        return 0
    return v if -2 ** 31 < v < 2 ** 31 else 0


def load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"tags": [], "passes": []}


def _zip_id():
    st = os.stat(COMPANYFACTS)
    return {"size": st.st_size, "mtime": int(st.st_mtime)}


def build_index(tags, workers=None, batch=150):
    """Indexes the tags ("taxonomy/tag") not indexed yet, in one pass over the zip. Returns the tags added."""
    import pickle
    man = load_manifest()
    if man.get("zip") and man["zip"] != _zip_id():
        raise RuntimeError("companyfacts.zip changed since the index was built: delete .cache/backtest/facts to rebuild")
    new = sorted(set(tags) - set(man["tags"]))
    if not new:
        return []
    workers = workers or max(1, min(8, (os.cpu_count() or 2) - 2))
    started = time.time()
    print(f"facts: indexing {len(new)} tags from {COMPANYFACTS} with {workers} workers", flush=True)
    names = [i.filename for i in zipfile.ZipFile(COMPANYFACTS).infolist() if i.filename.endswith(".json")]
    jobs = [(names[i:i + batch], new) for i in range(0, len(names), batch)]
    try:
        with open(INDEX, "rb") as fh:
            index = pickle.load(fh)
    except (OSError, ValueError, EOFError):
        index = {}
    try:
        with open(ENTITIES, encoding="utf-8") as fh:
            entities = {int(c): n for c, n in json.load(fh).items()}
    except (OSError, ValueError):
        entities = {}
    where = {k: {} for k in new}   # tag -> {cik: (first record, count)}
    sizes = dict.fromkeys(new, 0)
    for k in new:
        os.makedirs(os.path.dirname(tag_path(k)), exist_ok=True)
        open(tag_path(k), "wb").close()
    checks = {"labeled": 0, "rule_agrees": 0, "odd_accn": 0}
    with Pool(workers, initializer=_open_zip, initargs=(COMPANYFACTS,)) as pool:
        for n, (out, ents, ch) in enumerate(pool.imap_unordered(_index_members, jobs), 1):
            entities.update(ents)
            for k, v in ch.items():
                checks[k] += v
            for key, parts in out.items():
                with open(tag_path(key), "ab") as fh:
                    for cik, blob in parts:
                        count = len(blob) // REC.size
                        where[key][cik] = (sizes[key], count)
                        sizes[key] += count
                        fh.write(blob)
            if n % 20 == 0 or n == len(jobs):
                print(f"  {n} of {len(jobs)} batches, {time.time() - started:.0f}s", flush=True)
    for key in new:
        ciks = sorted(where[key])
        index[key] = (array.array("i", ciks), array.array("q", (where[key][c][0] for c in ciks)),
                      array.array("i", (where[key][c][1] for c in ciks)))
    with open(INDEX + ".tmp", "wb") as fh:
        pickle.dump(index, fh, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(INDEX + ".tmp", INDEX)
    with open(ENTITIES + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({str(c): n for c, n in entities.items()}, fh)
    os.replace(ENTITIES + ".tmp", ENTITIES)
    man["tags"] = sorted(set(man["tags"]) | set(new))
    man["zip"] = _zip_id()
    man["record"] = REC.format
    man["min_end"] = iso(MIN_END)
    man.setdefault("passes", []).append({"when": dt.datetime.now().isoformat(timespec="seconds"), "tags": len(new),
                                         "records": sum(sizes.values()), "seconds": round(time.time() - started),
                                         "empty_tags": [k for k in new if not sizes[k]], **checks})
    with open(MANIFEST + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=1)
    os.replace(MANIFEST + ".tmp", MANIFEST)
    print(f"facts: {sum(sizes.values()):,} records for {len(new)} tags in {time.time() - started:.0f}s; the rule gave "
          f"the SEC's label for {checks['rule_agrees']:,} of {checks['labeled']:,} labeled facts", flush=True)
    return new


# ---------------------------------------------------------------------------------------------------------------------
# Reading it.

class Facts:
    """The index, read as of a date: frames and companyconcept answers built from facts filed before `day`."""

    def __init__(self):
        import pickle
        man = load_manifest()
        if man.get("zip") and man["zip"] != _zip_id():
            raise RuntimeError("companyfacts.zip changed since the index was built: delete .cache/backtest/facts")
        self.tags = set(man["tags"])
        try:
            with open(INDEX, "rb") as fh:
                self.index = pickle.load(fh)
        except OSError:
            self.index = {}
        try:
            with open(ENTITIES, encoding="utf-8") as fh:
                self.entities = {int(c): n for c, n in json.load(fh).items()}
        except OSError:
            self.entities = {}

    def records(self, key, cik):
        """A company's records under one tag, in the file's order (by unit, then as the company's list has them)."""
        ix = self.index.get(key)
        if not ix:
            return []
        ciks, offsets, counts = ix
        i = bisect.bisect_left(ciks, cik)
        if i == len(ciks) or ciks[i] != cik:
            return []
        with open(tag_path(key), "rb") as fh:
            fh.seek(offsets[i] * REC.size)
            return list(REC.iter_unpack(fh.read(counts[i] * REC.size)))

    def dated_records(self, key, cik):
        """records(), with a fact dated after the day it was filed (a typo in its date) dated on its filing date; each
        record gains a 14th field, the date as filed (an ordinal) where it was moved, else None."""
        return [r + (None,) if r[3] <= r[6] else r[:3] + (r[6],) + r[4:] + (r[3],) for r in self.records(key, cik)]

    def public_float(self, cik, day, days=550):
        """(date, value) of the newest public float the filings made before `day` give, dated within `days` before it
        (a 10-K gives the float of its second quarter's end, so the newest filed before a day can be 17 months old),
        or None: what the share count used on `day` may be checked against (prices.share_count, WHOLE_SHARE)."""
        best = None
        for r in self.dated_records(FLOAT_TAG, cik):
            if r[1] != 0 or r[2] or not r[4] or r[4] <= 0 or r[6] >= day.toordinal()                     or r[3] < day.toordinal() - days:
                continue
            if best is None or (r[3], r[6], r[5]) > (best[3], best[6], best[5]):
                best = r
        return (dt.date.fromordinal(best[3]), best[4]) if best else None

    def history(self, cik):
        """What the price gate (prices.clean) reads of a company, from every filing in the file whenever it was filed:
        {"floats": [(date, value, filed)], "counts": [(source, start, date, value, filed)]} (dates as ordinals).

        - floats: the public float (FLOAT_TAG, in dollars) for each date it is given for, from the last filing that
          gives it;
        - counts: every share count the cover pages ("cover"), balance sheets ("balance") and diluted weighted averages
          ("diluted", with the start of its period) give, each as given in each filing, so that a count a later filing
          gives again for the same date shows whether that filing restated it for a stock split.

        A fact dated after the day it was filed (a typo in its date) is taken as of its filing date. This reads facts
        filed after a backtest date as well: the gate only checks that a chart's closes agree with its splits, a matter
        of how the data were recorded, never of how a stock did."""
        floats = {}
        for r in self.dated_records(FLOAT_TAG, cik):
            if r[1] != 0 or r[2] or not r[4] or r[4] <= 0:
                continue
            if r[3] not in floats or (r[6], r[5]) > floats[r[3]][1]:
                floats[r[3]] = (r[4], (r[6], r[5]))
        floats = {d: (v, k) for d, (v, k) in floats.items()}
        counts = []
        for source, tag in (("cover", COVER_TAG), ("balance", BALANCE_TAG), ("diluted", DILUTED_TAG)):
            for r in self.dated_records(tag, cik):
                if r[1] != 1 or not r[4] or r[4] <= 0 or bool(r[2]) != (source == "diluted"):
                    continue
                counts.append((source, r[2], r[3], r[4], r[6]))
        return {"floats": sorted((d, v, k[0]) for d, (v, k) in floats.items()), "counts": counts}

    @staticmethod
    def fact(r):
        """A record as the SEC's APIs list a fact."""
        cik, u, start, end, val, accn, filed, fy, fp, form, lab, prio, seq = r
        out = {"start": iso(start)} if start else {}
        out.update(end=iso(end), val=_num(val), accn=accn_text(accn), fy=fy or None,
                   fp=fp.rstrip(b"\0").decode() or None, form=form.rstrip(b"\0").decode(), filed=iso(filed))
        return out

    def concept(self, cik, taxonomy, tag, day):
        """The companyconcept answer for a company and tag as of `day` (facts filed before it), or None where there
        is none (the API's 404)."""
        before = day.toordinal()
        units = {}
        for r in self.records(f"{taxonomy}/{tag}", cik):
            if r[6] < before:
                units.setdefault(UNITS[r[1]], []).append(self.fact(r))
        if not units:
            return None
        return {"cik": cik, "taxonomy": taxonomy, "tag": tag, "entityName": self.entities.get(cik, ""), "units": units}

    def frames(self, day, jobs, loc, log=print):
        """{(taxonomy, tag, unit, period): [(cik, accn, start, end, val)]} for each job, the frames API's answer as of
        `day` (facts filed before it). One pass over each tag's records."""
        before = day.toordinal()
        by_tag = {}
        for tax, tag, unit, period in jobs:
            by_tag.setdefault(f"{tax}/{tag}", {})[(UNITS.index(unit), label_code(period))] = (tax, tag, unit, period)
        out = {}
        started = time.time()
        for key, want in by_tag.items():
            best = {}
            if key in self.index:
                with open(tag_path(key), "rb") as fh:
                    blob = fh.read()
                for r in REC.iter_unpack(blob):
                    if r[6] >= before or (r[1], r[10]) not in want:
                        continue
                    # A span the SEC labels in this tag today goes first, then the last filed, the larger accession
                    # number, the very fact the SEC labels, and the later place in the company's list.
                    rank = (r[11] >= SAME_TAG, r[6], r[5], r[11] == EXACT, r[12])
                    k = (r[1], r[10], r[0])
                    if k not in best or rank > best[k][0]:
                        best[k] = (rank, r)
                del blob
            for job in want.values():
                out[job] = []
            for (u, lab, cik), (_, r) in best.items():
                out[want[(u, lab)]].append((cik, accn_text(r[5]), iso(r[2]) if r[2] else None, iso(r[3]), _num(r[4])))
        log(f"  frames as of {day}: {len(out)} frames, {sum(len(v) for v in out.values()):,} rows in "
            f"{time.time() - started:.0f}s")
        return out

    def share_counts(self, cik, day, covers=3):
        """The share counts a company's filings gave before `day`, for prices.share_count: [{"source", "rank", "val",
        "date", "filed", "form", "accn"}], newest first within each source.

        - "cover": the cover page's count of shares outstanding (dei:EntityCommonStockSharesOutstanding) of each of the
          newest `covers` filings that give one (rank 0 the newest), as of the latest date the filing gives it for. The
          SEC's bulk file keeps only counts given without a share class, so a filing that gives one count per class
          (most companies with several) has none here; none of the 143,962 cover pages indexed for today's listed
          companies gives two counts for its latest date.
        - "balance": the newest balance-sheet count (us-gaap:CommonStockSharesOutstanding), by its date, then the last
          filed.
        - "diluted": the newest diluted weighted average count (us-gaap:WeightedAverageNumberOfDilutedSharesOutstanding),
          by the end of its period, the shortest period ending then (a quarter before the year to date), then the last
          filed; its date is the end of the period.
        - "eps": the count the newest earnings per share imply (_eps_count), to check the others against.
        - "book": the newest stockholders' equity (us-gaap:StockholdersEquity, in dollars), by its date, then the last
          filed: the book value a market value far under is taken for a count given in thousands.

        A fact dated after the day it was filed has a typo in its date (Natural Alternatives' diluted count dated 2031,
        Amrep's cover page dated 2033): it is taken as of its filing date (dated_records), so that it ages like any
        other."""
        before = day.toordinal()
        out = []
        rows = [r for r in self.dated_records(COVER_TAG, cik) if r[6] < before and r[1] == 1]
        filings = {}
        for r in rows:
            filings.setdefault((r[6], r[5]), []).append(r)
        for rank, key in enumerate(sorted(filings, reverse=True)[:covers]):
            mine = filings[key]
            end = max(r[3] for r in mine)
            r = max((r for r in mine if r[3] == end), key=lambda r: r[4])
            out.append(self._count("cover", rank, r))
        rows = [r for r in self.dated_records(BALANCE_TAG, cik) if r[6] < before and r[1] == 1 and not r[2]]
        if rows:
            out.append(self._count("balance", 0, max(rows, key=lambda r: (r[3], r[6], r[5]))))
        rows = [r for r in self.dated_records(DILUTED_TAG, cik) if r[6] < before and r[1] == 1 and r[2]]
        if rows:
            out.append(self._count("diluted", 0, max(rows, key=lambda r: (r[3], -(r[3] - r[2]), r[6], r[5]))))
        eps = self._eps_count(cik, before)
        if eps:
            out.append(eps)
        rows = [r for r in self.dated_records(EQUITY_TAG, cik) if r[6] < before and r[1] == 0 and not r[2]]
        if rows:
            r = max(rows, key=lambda r: (r[3], r[6], r[5]))
            out.append({"source": "book", "rank": 0, "val": r[4], "date": iso(r[3]), "filed": iso(r[6]),
                        "form": r[9].rstrip(b"\0").decode(), "accn": accn_text(r[5])})
        return out

    def _eps_count(self, cik, before):
        """{"source": "eps", ...}: the profit of the newest period that gives earnings per share (diluted, else basic
        and diluted, else basic) and a profit (to common shareholders, else net income) for the same span, over that
        earnings per share, the shortest such period ending then and the last filed before `before` (an ordinal).
        Never a source of the count used (prices.share_count), only one to check the others against. None where
        earnings per share is under EPS_MIN either way (rounded to the cent, it says too little) or the two have
        opposite signs."""
        for tag in EPS_TAGS:
            rows = [r for r in self.dated_records(tag, cik) if r[6] < before and r[1] == UNITS.index("USD/shares")
                    and r[2] and abs(r[4]) >= EPS_MIN]
            if not rows:
                continue
            spans = {}
            for r in rows:
                if (r[2], r[3]) not in spans or (r[6], r[5]) > (spans[(r[2], r[3])][6], spans[(r[2], r[3])][5]):
                    spans[(r[2], r[3])] = r
            profits = {}
            for ptag in PROFIT_TAGS:  # each span's profit from the first tag that gives one for it
                mine = {}
                for r in self.dated_records(ptag, cik):
                    k = (r[2], r[3])
                    if r[6] < before and r[1] == 0 and r[2] and k in spans and k not in profits:
                        if k not in mine or (r[6], r[5]) > (mine[k][6], mine[k][5]):
                            mine[k] = r
                profits.update(mine)
            both = [k for k in spans if k in profits and profits[k][4] and (profits[k][4] > 0) == (spans[k][4] > 0)]
            if not both:
                continue
            k = max(both, key=lambda k: (k[1], -(k[1] - k[0]), spans[k][6]))
            e, p = spans[k], profits[k]
            return {"source": "eps", "rank": 0, "val": p[4] / e[4], "date": iso(k[1]), "filed": iso(max(e[6], p[6])),
                    "form": e[9].rstrip(b"\0").decode(), "accn": accn_text(e[5]), "tag": tag}
        return None

    @staticmethod
    def _count(source, rank, r):
        return {"source": source, "rank": rank, "val": r[4], "date": iso(r[3]), "filed": iso(r[6]),
                "form": r[9].rstrip(b"\0").decode(), "accn": accn_text(r[5]),
                "date_typo": iso(r[13]) if len(r) > 13 and r[13] else None}


def _num(v):
    return int(v) if v == v and v.is_integer() and abs(v) < 2 ** 53 else v


def frame_answer(rows, facts, loc, taxonomy, tag, unit, period):
    """A frames API answer from Facts.frames rows: {"taxonomy", "tag", "ccp", "uom", "data": [...]}."""
    data = []
    for cik, accn, start, end, val in rows:
        d = {"accn": accn, "cik": cik, "entityName": facts.entities.get(cik, ""), "loc": loc.get(cik) or "-"}
        if start:
            d["start"] = start
        d.update(end=end, val=val)
        data.append(d)
    return {"taxonomy": taxonomy, "tag": tag, "ccp": period, "uom": unit, "pts": len(data), "data": data}


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Index tags of companyfacts.zip (normally done by the backtest itself)")
    ap.add_argument("tags", nargs="*", help="taxonomy/tag, e.g. us-gaap/Revenues")
    ap.add_argument("--from-file", help="a JSON list of taxonomy/tag")
    ap.add_argument("--workers", type=int)
    args = ap.parse_args()
    tags = list(args.tags)
    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            tags += json.load(fh)
    build_index(tags, workers=args.workers)


if __name__ == "__main__":
    sys.exit(main())
