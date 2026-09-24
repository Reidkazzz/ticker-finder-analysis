"""HTTP helpers: SEC-compliant requests with rate limiting, plus a browser-style client."""
import gzip
import http.client
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.request
import zlib

# The SEC asks every automated client to identify itself with a name and a contact email.
# Set SEC_USER_AGENT (a GitHub Actions secret works) to "Your Name you@yourdomain.com".
# The placeholder below is accepted today, but a real contact is what the SEC asks for.
SEC_UA = os.environ.get("SEC_USER_AGENT") or "Ticker Finder Analysis contact@example.com"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class RateLimiter:
    """Allows at most `rate` calls per second across threads, and can pause every thread at once."""

    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.paused_until = 0.0

    def wait(self, cancel=None):
        # Sleeps outside the lock in short steps, so a pause() from another thread takes effect at once
        # instead of queueing behind a sleeper and stacking on top of its wait. Returns early once `cancel` is set.
        while True:
            with self.lock:
                now = time.monotonic()
                at = max(self.next_at, self.paused_until)
                if at <= now:
                    self.next_at = now + self.interval
                    return
            if cancel is not None and cancel.is_set():
                return
            time.sleep(min(at - now, 1.0))

    def pause(self, seconds):
        """Holds back every thread's next request, not just the one that hit the limit."""
        with self.lock:
            self.paused_until = max(self.paused_until, time.monotonic() + seconds)


# The SEC's published limit is 10 requests/second. Shared cloud IPs (like GitHub's runners) get
# throttled sooner, so stay well under it.
_sec_limiter = RateLimiter(5)
_web_limiter = RateLimiter(12)

# When the SEC throttles, it answers 403 or 429 for roughly ten minutes. Waiting it out beats failing.
SEC_BACKOFF = [20, 40, 80, 160, 300, 300]


class NotFound(Exception):
    pass


class Throttled(Exception):
    """The SEC kept refusing requests even after waiting out its block."""


# Set once the SEC is still refusing after the whole backoff. From then on every SEC request raises Throttled
# at once, so a lasting block ends the run's SEC work instead of each request sitting through its own backoff.
sec_blocked = threading.Event()

# The backoff is shared: a block is one streak of pauses, however many threads run into it.
_streak_lock = threading.Lock()
_streak = {"step": 0, "resume": 0.0}


def _fetch(url, headers, opener=None, timeout=30, retries=4):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", **headers})
    for attempt in range(retries):
        try:
            with (opener or urllib.request.build_opener()).open(req, timeout=timeout) as resp:
                body = resp.read()
                gz = resp.headers.get("Content-Encoding") == "gzip"
            return gzip.decompress(body) if gz else body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                e.close()
                time.sleep(2 ** attempt * 1.5)
                continue
            raise
        # Connection errors and bodies cut off mid-read: IncompleteRead, SSL errors, truncated gzip.
        except (OSError, http.client.HTTPException, EOFError, zlib.error):
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def _missing(e):
    """EDGAR's archive answers a file that does not exist with S3's 403 AccessDenied, not 404."""
    try:
        body = e.read()
        if e.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return b"<Code>AccessDenied</Code>" in body
    except Exception:
        return False


def sec_get(url):
    for _ in range(2 * len(SEC_BACKOFF) + 2):
        _sec_limiter.wait(cancel=sec_blocked)
        if sec_blocked.is_set():
            raise Throttled(url)
        sent = time.monotonic()
        try:
            body = _fetch(url, {"User-Agent": SEC_UA})
        except urllib.error.HTTPError as e:
            if e.code == 403 and _missing(e):
                raise NotFound(url) from e
            if e.code not in (403, 429):
                raise
            with _streak_lock:
                if sent < _streak["resume"]:
                    continue  # sent before the latest pause ended, so it says nothing new
                if _streak["step"] == len(SEC_BACKOFF):
                    sec_blocked.set()
                    raise Throttled(url) from e
                wait = SEC_BACKOFF[_streak["step"]]
                _streak["step"] += 1
                _streak["resume"] = time.monotonic() + wait
                _sec_limiter.pause(wait)
            print(f"  SEC is throttling requests (HTTP {e.code}); pausing {wait}s", flush=True)
            continue
        with _streak_lock:
            _streak["step"] = 0
        return body
    raise Throttled(url)  # this request kept failing while others got through


def sec_json(url):
    return json.loads(sec_get(url))


def web_get(url, opener=None, headers=None):
    _web_limiter.wait()
    return _fetch(url, {"User-Agent": WEB_UA, "Accept": "*/*", **(headers or {})}, opener=opener)


def web_json(url, opener=None, headers=None):
    return json.loads(web_get(url, opener=opener, headers=headers))


def cookie_opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
