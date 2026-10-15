"""Companies' own press releases, from the 8-K and 6-K reports they file with the SEC (EDGAR's daily index).

A US company that puts out news it must disclose (results, a deal, a new chief executive) files an 8-K, usually with
the press release attached as exhibit 99.1; a foreign one files a 6-K, with the release attached or as the report
itself. The release's headline, read from the exhibit, is shown among the report's headlines (news.headlines) as the
company's own word, linked to the filing and dated by the release's own dateline. Only what reads as a press release
counts: a headline that names the company above a dateline ("LATHAM, N.Y.--(BUSINESS WIRE)-- Oct. 8, 2026--"), so the
slides, debt schedules and transcripts companies also attach are left out."""
import datetime as dt
import html
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import news
from .insiders import _index_lines, _posted_days
from .net import NotFound, Throttled, sec_get

FORMS = ("8-K", "6-K")
PARSER = 1  # saved with the cache; a cache from another version of release() is read again from EDGAR
# How the site names a press release's source.
SOURCE = "Press release (SEC filing)"
# An exhibit larger than this is a slide deck or a set of financial statements, not a press release, and isn't read. A
# results release with its tables can run to over 1MB (PepsiCo's for the third quarter of 2026, 1.1MB).
MAX_EXHIBIT = 5_000_000
# The most exhibits read from one filing: a company that puts out its results and its dividend the same day files both
# releases on one 8-K (as exhibits 99.1 and 99.2); a third exhibit is almost always slides or a transcript.
MAX_RELEASES = 2
# How far into an exhibit the dateline is looked for, in blocks of text: a release's headline, subheadlines and
# dateline come first, after at most a logo, an "Exhibit 99.1" mark and a contact block.
HEAD_LINES = 40
# The longest first line of a headline set over two blocks, at a headline's type size (ConnectOne Bancorp's 50
# characters, Evogene's 69): a longer block in the same type is a whole headline, and the one after it a subheadline set
# the same way (AIB Data Centers' 71-character headline over "Optional Renewals Could Increase Total Contract Value").
SET_WIDTH = 70
# The most days a release's dateline can come before its filing, within which the release keeps its own date: a 6-K can
# gather a month's releases (TotalEnergies' of October 1, 2026 attached ones from September 1 to 7). A dateline further
# back is a misread, and the filing then dates the release.
DATELINE_DAYS = 60

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?"
          r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
# A press release's dateline: the wire service that sent it ("--(BUSINESS WIRE)--", "/PRNewswire/", "(GLOBE NEWSWIRE)"),
# or a place and a date followed by a dash ("HOUSTON, Oct. 8, 2026 -", "PURCHASE, N.Y. - October 8, 2026 -",
# "October 8, 2026 --").
_WIRE = re.compile(r"\((?:business wire|globe ?newswire|accesswire|access newswire|marketwired|newsfile|newmediawire"
                   r"|prnewswire|nasdaq|ein presswire)\)|/\s*(?:pr ?newswire|cnw|prweb)\s*/", re.I)
_DATED = re.compile(rf"^(?:[A-Z][\w.' ]{{1,40}}(?:,\s*[A-Z][\w.' ]{{1,30}})?\s*[,–—-]+\s*)?(?:\w+day,?\s+)?{_MONTH}\s+"
                    rf"\d{{1,2}},?\s+\d{{4}}\s*(?:/[^/]{{1,40}}/\s*)?[-–—]", re.I)
_DATE = re.compile(rf"\b({_MONTH})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I)
# The last line of a 6-K's cover page, which comes before the report itself where it is the press release.
_COVER_END = re.compile(r"rule 12g3-2\(b\)|form 20-f\b.*\bform 40-f\b", re.I)
# Words a headline set over two lines can break after ("... Announces Appointment of Eric Honroth as" / "Executive Vice
# President & CFO"), whose next line is then the rest of it.
_RUNS_ON = re.compile(r"(?:\b(?:a|an|and|as|at|by|for|from|in|its|of|on|the|to|with)|[,:;&–-])$", re.I)
# A headline's verb with nothing after it is cut too ("Bioceres Crop Solutions Reports" / "Fiscal Year 2027 Results"),
# where the next block reads as the rest of a headline (_title_case): these words are also nouns ("Acme Comments on
# Recent Media Reports" over "Company reaffirms full-year guidance").
_VERB_END = re.compile(r"\b(?:reports|announces|provides|declares|completes|enters|receives|signs|launches|appoints|names)$",
                       re.I)
# Blocks above the headline that aren't it: an exhibit mark, a release's labels, a page number or the file's own name,
# which the SEC's documents carry; contacts, wherever the word comes ("American Outdoor Brands Investor Contact: Liz
# Sharp, VP"), with a phone number, email or web address; and a letterhead's street address.
_NOT_HEAD = re.compile(r"^(?:ex(?:hibit)?[- ]?99(?:\.\d+)?|for immediate release|immediate release|news release|press "
                       r"release|news\s*$|source:?\s*$|\S+\.html?$|document$|\d+$|page \d+|(?:nasdaq|nyse|tsx|asx|otcqx)\s*:"
                       r"|(?:investor|media|public) relations\b)|\b(?:contacts?|logo)\b", re.I)
_CONTACT = re.compile(r"@|\(\d{3}\)|\b\d{3}[-.]\d{3}[-.]\d{4}\b|\bwww\.|https?://", re.I)
# Case matters here, so "of 10000" isn't a state and a ZIP code: a number, up to three capitalized words and a street's
# kind ("1800 North Route Z,"), or a state's code or name before a ZIP code ("Columbia, Missouri 65202").
_ADDRESS = re.compile(r"\b\d{1,6}\s+(?:[A-Z][\w.]*\s+){0,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr"
                      r"|Route|Lane|Ln|Parkway|Pkwy|Plaza|Suite|Floor|Way)\b\.?(?:\s+[A-Z0-9]{1,3}\b)?(?:,|\s*$|\s+P\.?\s?O\.? Box)"
                      r"|\bP\.?\s?O\.? Box \d"
                      r"|\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b|\b(?:Alabama|Arizona|California|Colorado|Florida|Georgia|Illinois"
                      r"|Massachusetts|Missouri|New Jersey|New York|Ohio|Pennsylvania|Texas|Virginia|Washington)\s+\d{5}\b")
# A filing's own words about what it attaches, which a 6-K's text can put above the release ("Attached hereto is
# Registrant's press release dated October 7, 2026, announcing that Gilat Secures ..."), or its form in a page header
# ("ANGEL OAK FINANCIAL STRATEGIES INCOME TERM TRUST 8-K").
_FILING_TEXT = re.compile(r"\b(attached hereto|registrant|furnished (herewith|as exhibit)|incorporated (herein )?by "
                          r"reference|this report on form|[86]-k|enclosure|material change|item \d+\.)\b"
                          r"|\(the [\"“]?company[\"”]?\)", re.I)
# A paragraph of sentences, not a headline: a Canadian material change report's summary above the release it attaches
# ("On October 1, 2026, the Company filed articles of amendment ... The Name Change was approved", Helus Pharma's). A
# period after a short form doesn't end a sentence ("Xco Inc. Announces", "U.S. Steel"), and a headline is shorter.
_ABBREVIATIONS = {"inc", "corp", "co", "ltd", "plc", "llc", "lp", "no", "st", "mr", "mrs", "ms", "dr", "jr", "sr", "vs",
                  "approx", "intl", "bros", "mfg", "u.s", "s.a", "n.v", "l.p", "u.k", "a.g", "s.p.a"}


def _sentences(text):
    """Whether a block is a paragraph of two or more sentences (_ABBREVIATIONS aside), which no headline is."""
    if len(text) < 100:
        return False
    # A filing's own report of what happened opens on its date and calls the company "the Company".
    if re.match(r"(On|As of|Effective)\s+\w+\.?\s+\d{1,2},\s+\d{4},", text) or re.search(r"\bthe Company\b", text):
        return True
    for m in re.finditer(r"(\S+)\.\s+[A-Z]", text):
        word = m.group(1).lower().strip("(\"'“")
        if word not in _ABBREVIATIONS and not re.fullmatch(r"[a-z]", word):
            return True
    return False
# Words a line under a logo gives besides the company's name and ticker ("American Outdoor Brands, Inc. (NASDAQ:
# AOUT)"), which make it no headline.
_LISTING_WORDS = {"nasdaq", "nyse", "american", "amex", "tsx", "tsxv", "asx", "otc", "otcqx", "otcqb", "global",
                  "select", "market", "capital", "stock", "exchange", "symbol", "ticker", "lse", "aim", "no"}
_BLOCK_TAG = re.compile(r"(?i)(</?(?:p|div|tr|h\d|li|td|th|table|center)\b[^>]*>)")
_FONT = re.compile(r"(?i)\bfont-(?:size|weight|style)\s*:\s*([^;\"']+)|\bfont\s*:\s*([^;\"']+)")


def _blocks(doc):
    """[(text, look)]: a document's blocks of text, for HTML each paragraph, heading or table cell, its source's own line
    wrapping and line breaks undone (Grupo Aeroportuario del Sureste's headline runs over three lines of its file,
    Viatris' is broken over two), and for plain text each paragraph, the lines up to a blank one. A block's look is how
    it is set, from its own tag and what is inside it: its font size, weight and style (or the `font:` that gives them
    all), bold, italic or underlined; empty where the document sets none."""
    if not re.search(r"(?i)<(?:p|div|td|br|h\d)\b", doc):
        return [(x, ()) for x in (re.sub(r"\s+", " ", b).strip() for b in re.split(r"\n\s*\n", doc)) if x]
    body = re.sub(r"(?is)<(script|style|head|title)\b.*?</\1>", " ", doc)
    body = re.sub(r"\s+", " ", body)
    out, opening = [], ""
    for part in _BLOCK_TAG.split(body):
        if _BLOCK_TAG.fullmatch(part):
            opening = "" if part.startswith("</") else part
            continue
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", part)).replace("\xa0", " ")).strip()
        if text:
            raw = opening + part
            styles = {(a or b).strip().lower() for a, b in _FONT.findall(raw)}
            tags = {t.lower() for t in re.findall(r"(?i)<(b|strong|i|em|u)\b", raw)}
            out.append((text, tuple(sorted(styles | tags))))
    return out


def _lines(doc):
    """The text of a document's blocks (_blocks), one per line."""
    return [text for text, _ in _blocks(doc)]


def names(title, name, symbols=()):
    """Whether a headline names the company, by its ticker or name as a headline the site shows must (news._about):
    a filing gives its company's name, `name`, and the listings are its tickers, `symbols`."""
    key = news._name_key(name)
    # Without a ticker, a character no headline holds stands in for one, so only the name is looked for.
    return any(news._about(title, s, key) for s in symbols or ["\x01"])


def _title_case(text):
    """Whether a block is set in title case or capitals, as a headline's lines are: most of its longer words begin with a
    capital."""
    long = [w for w in re.findall(r"[A-Za-z][\w'’-]*", text) if len(w) > 3]
    return bool(long) and sum(w[0].isupper() for w in long) >= 0.8 * len(long)


def _dated(text):
    """The date a dateline gives ("HOUSTON, Oct. 8, 2026 --"), or None."""
    m = _DATE.search(text)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2)))
    except ValueError:
        return None


def release(doc, name, symbols=(), cover=False):
    """{"title", "dated"}: a press release's headline from its document `doc`, and the date its dateline gives (None
    where it gives none), or None where the document holds no release this can read.

    The headline is the first block above the dateline that names the company (names) and reads as one: four words or
    more, at most 300 characters, not a label, contact, address or filing's own words, nor the company's name and
    listing alone. With it go the blocks after it that are the rest of it: a block it runs on to (_RUNS_ON), or, after a
    first line no longer than SET_WIDTH, one set just as it is (American Outdoor Brands' "American Outdoor Brands Board
    of Directors" over "Approves $10 Million Share Repurchase Program", both in the same bold type), where a subheadline
    is set apart (PepsiCo's "Third-Quarter and Year-to-Date Results" in smaller type). Where the document sets the type
    of neither, which of the two the next block is can't be told, so the release isn't shown rather than shown cut short.

    None where the document has no dateline near its top, as slides, transcripts, debt schedules and letters don't, and
    where no block above it names the company: the one there is then a contact's title ("Executive Vice President &
    CFO", above AngioDynamics' headline) or part of a headline set in pieces. A 6-K that is itself the release (`cover`)
    is read from after its cover page."""
    blocks = _blocks(doc)
    if cover:
        end = next((i for i, (x, _) in enumerate(blocks[:60]) if _COVER_END.search(x)), None)
        blocks = blocks[end + 1:] if end is not None else []
    blocks = blocks[:HEAD_LINES]
    at = next((i for i, (x, _) in enumerate(blocks) if _WIRE.search(x) or _DATED.search(x)), None)
    if not at:
        return None
    # A block's words, numbers aside, so a letterhead's "TSX, NYSE - HBM 2026 No. 25" (Hudbay's) is its listing alone.
    word = lambda s: set(re.findall(r"[a-z]+[a-z0-9]*", s.lower()))
    listing = word(name) | news._LEGAL | _LISTING_WORDS | {s.lower() for s in symbols}
    for i, (x, look) in enumerate(blocks[:at]):
        if _NOT_HEAD.search(x) or _CONTACT.search(x) or _ADDRESS.search(x) or _FILING_TEXT.search(x) or _sentences(x) \
                or len(x.split()) < 4 or len(x) > 300 or word(x) <= listing or not names(x, name, symbols):
            continue
        first = x
        while i + 1 < at and len(x) + len(blocks[i + 1][0]) <= 300 and not re.search(r"[.!?]$", x) \
                and not re.match(r"[•·▪\-–*]", blocks[i + 1][0]):
            nxt, nlook = blocks[i + 1]
            if _RUNS_ON.search(x) or re.match(r"[a-z]", nxt) or _VERB_END.search(x) and _title_case(nxt):
                pass  # it runs on ("... Cape Station Ahead" / "of Schedule, ...", Fervo Energy's)
            elif len(first) > SET_WIDTH or not _title_case(nxt):
                break  # a subheadline ("Mike Nuzzo steps down; David Barse to head Audit Committee", KinderCare's)
            elif not look and not nlook:
                return None  # the next block could be the rest of the headline or a subheadline
            elif nlook != look:
                break
            i += 1
            x = f"{x} {nxt}"
        # The dateline's date, or one on its own line beside a dateline that gives none (Crescent Energy's "October 8,
        # 2026" above "HOUSTON --(BUSINESS WIRE)--").
        # The dateline's own date first, so a date above it ("effective August 30") doesn't stand in for it (Buckle's).
        dated = next((d for d in map(_dated, (blocks[k][0] for k in (at, at - 1, at + 1) if 0 <= k < len(blocks))) if d),
                     None)
        return {"title": x.rstrip(" ."), "dated": dated}
    return None


def _text(raw):
    """A document's text: UTF-8 where it is, else Windows-1252, which many filing agents' HTML still is (its curly
    quotes, read as UTF-8, would show as "Acme�s")."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _acc(path):
    return path.rsplit("/", 1)[-1].removesuffix(".txt")


def _documents(index_html):
    """[(document type, its path in the archive, its size in bytes)] from a filing's index page, in its order."""
    out = []
    for row in re.findall(r"(?is)<tr[^>]*>(.*?)</tr>", index_html):
        cells = re.findall(r"(?is)<td[^>]*>(.*?)</td>", row)
        link = re.search(r'href="(?:/ix\?doc=)?(/Archives/edgar/data/[^"]+)"', row)
        if len(cells) >= 4 and link:
            kind = re.sub(r"<[^>]+>", "", cells[3]).strip().upper()
            size = re.sub(r"\D", "", re.sub(r"<[^>]+>", "", cells[4])) if len(cells) > 4 else ""
            out.append((kind, link.group(1), int(size) if size else 0))
    return out


def _new_york(local):
    """A New York time with no zone as a UTC time."""
    try:
        from zoneinfo import ZoneInfo
        return local.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(dt.timezone.utc)
    except Exception:
        # Without the time zone database: Eastern Daylight Time from March to October, a few days off at the turns,
        # which can't move a time between 9am and 10pm to another New York date.
        return (local + dt.timedelta(hours=4 if 3 < local.month < 11 else 5)).replace(tzinfo=dt.timezone.utc)


def _accepted(index_html, filed):
    """When the SEC accepted the filing, as a UTC time ("Accepted" on its index page, New York time), else noon in New
    York on the day it was filed (`filed`, YYYYMMDD)."""
    m = re.search(r"Accepted</div>\s*<div[^>]*>\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", index_html)
    return _new_york(dt.datetime.fromisoformat(m.group(1)) if m else
                     dt.datetime.strptime(filed, "%Y%m%d").replace(hour=12))


def read_filing(path, form, filed, name, symbols=()):
    """{"date": the UTC time it was accepted, "releases": [{"title", "url", "date"}]} for one 8-K or 6-K, from its index
    page and the press releases it attaches (or, for a 6-K that attaches none, the report itself). A release is dated by
    its dateline, at noon in New York, where that is earlier than the filing and within DATELINE_DAYS of it: an 8-K can
    come days after the release it attaches, which was news on its own date. Otherwise by when the filing was accepted."""
    folder, acc = path.removesuffix(".txt").rsplit("/", 1)
    index = sec_get(f"https://www.sec.gov/Archives/{folder}/{acc.replace('-', '')}/{acc}-index.htm").decode("latin-1")
    accepted = _accepted(index, filed)
    filed_day = dt.datetime.strptime(filed, "%Y%m%d").date()
    docs = _documents(index)
    picks = [d for d in docs if d[0].startswith("EX-99")] or [d for d in docs[:1] if form == "6-K" and d[0] == "6-K"]
    releases = []
    for kind, doc_path, size in picks:
        if len(releases) >= MAX_RELEASES or size > MAX_EXHIBIT:
            continue
        try:
            got = release(_text(sec_get("https://www.sec.gov" + doc_path)), name, symbols,
                          cover=kind == "6-K")
        except NotFound:
            continue
        if got:
            d = got["dated"]
            when = _new_york(dt.datetime.combine(d, dt.time(12))) \
                if d and filed_day - dt.timedelta(days=DATELINE_DAYS) <= d < filed_day else accepted
            releases.append({"title": got["title"], "url": "https://www.sec.gov" + doc_path, "date": when.isoformat()})
    return {"date": accepted.isoformat(), "releases": releases}


def load_cache(cache_path):
    try:
        with open(cache_path, encoding="utf-8") as fh:
            cache = json.load(fh)
        if cache.get("parser") == PARSER:
            return cache
    except (OSError, ValueError):
        pass  # a missing or corrupt cache is rebuilt from EDGAR rather than failing the run
    return {"parser": PARSER, "days_done": [], "filings": {}}


def _save(cache, cache_path, keep):
    cache["days_done"] = sorted(d for d in cache["days_done"] if d >= keep)
    cache["filings"] = {a: f for a, f in cache["filings"].items() if f["filed"] >= keep}
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh)
    os.replace(tmp, cache_path)  # never leave a half-written cache behind


def update_cache(cache_path, companies, days, today=None, time_budget=None):
    """Adds the press releases of the 8-K and 6-K reports filed in the last `days` days by the companies `companies`
    ({CIK: (name, [tickers])}) to the on-disk cache, and drops older ones. Like insiders.update_cache: newest day first,
    saved after each, a day marked done only once each of its filings was read, today and yesterday always read again
    for late filings, an unposted day taken for a holiday once a later day's index is up, and stopping when the SEC
    refuses or the time budget runs out. A filing is kept for each company in the universe that filed it (a joint 8-K
    for each), and one whose index the SEC doesn't have is kept with no releases rather than asked for every night.
    Returns (cache, days left unread)."""
    today = today or dt.date.today()
    started = time.monotonic()
    cache = load_cache(cache_path)
    done = set(cache["days_done"])
    keep = f"{today - dt.timedelta(days=days):%Y%m%d}"
    todo = [d for d in (today - dt.timedelta(days=n) for n in range(days + 1))
            if d.weekday() < 5 and not (f"{d:%Y%m%d}" in done and d < today - dt.timedelta(days=1))]
    stop = threading.Event()

    def halted():
        if time_budget and time.monotonic() - started > time_budget:
            stop.set()
        return stop.is_set()

    def grab(line):
        if halted():
            return line, None
        try:
            return line, read_filing(line["path"], line["form"], line["date"], *companies[line["cik"]])
        except NotFound:
            return line, {"date": _accepted("", line["date"]).isoformat(), "releases": []}
        except Throttled:
            stop.set()
        except Exception:
            pass  # counted as failed, so the day is read again next run
        return line, None

    posted, left = {}, []

    def listed(day):
        q = (day.year, (day.month - 1) // 3)
        if q not in posted:
            posted[q] = _posted_days(day)
        return posted[q]

    for i, day in enumerate(todo):
        key = f"{day:%Y%m%d}"
        lines = known = None
        try:
            if halted():
                left = [f"{d:%Y-%m-%d}" for d in todo[i:]]
                break
            listed(today)
            known = listed(day)
            if known is None or key in known:
                lines = _index_lines(day)
        except Throttled:
            left = [f"{d:%Y-%m-%d}" for d in todo[i:]]
            break
        except Exception as e:
            print(f"  {day}: could not read the index ({e!r}); it will be read again next run", flush=True)
            continue
        if lines is None:
            # EDGAR posts each weekday's index around 10 pm New York time, and none on holidays. An unlisted day is a
            # holiday once a later day's index is up, in its quarter or a later one; until then it may not be posted yet.
            if known is None:
                holiday = day < today - dt.timedelta(days=1)
            else:
                holiday = key not in known and any(k > key for ks in posted.values() if ks for k in ks)
            if holiday:
                done.add(key)
            continue
        new = {f"{_acc(x['path'])}:{x['cik']}": x for x in lines if x["form"] in FORMS and x["cik"] in companies}
        new = [x for k, x in sorted(new.items()) if k not in cache["filings"]]
        failed = 0
        with ThreadPoolExecutor(max_workers=4) as pool:
            for line, got in pool.map(grab, new):
                if got is None:
                    failed += 1
                    continue
                cache["filings"][f"{_acc(line['path'])}:{line['cik']}"] = {
                    "cik": line["cik"], "form": line["form"], "filed": key, **got}
        print(f"  {day}: {len(new)} new 8-K and 6-K reports, {failed} not read", flush=True)
        if not failed and not stop.is_set():
            done.add(key)
        cache["days_done"] = sorted(done)
        _save(cache, cache_path, keep)
        if stop.is_set():
            left = [f"{d:%Y-%m-%d}" for d in todo[i:]]
            break
    return cache, left


def by_cik(cache):
    """{CIK: [{"title", "url", "source", "date"}]}: each company's press releases in the cache, as news.headlines takes
    its headlines, the date a UTC datetime."""
    out = {}
    for f in cache["filings"].values():
        for r in f["releases"]:
            out.setdefault(f["cik"], []).append({"title": r["title"], "url": r["url"], "source": SOURCE,
                                                 "date": dt.datetime.fromisoformat(r.get("date") or f["date"])})
    return out
