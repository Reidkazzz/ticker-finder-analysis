"""HTTP helpers: SEC-compliant requests with rate limiting, plus a browser-style client."""
import gzip
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.request

# The SEC asks every automated client to identify itself with a name and a contact email.
# Set SEC_USER_AGENT (a GitHub Actions secret works) to "Your Name you@yourdomain.com".
# The placeholder below is accepted today, but a real contact is what the SEC asks for.
SEC_UA = os.environ.get("SEC_USER_AGENT") or "Ticker Finder Analysis contact@example.com"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class RateLimiter:
    """Allows at most `rate` calls per second across threads."""

    def __init__(self, rate):
        self.interval = 1.0 / rate
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if self.next_at > now:
                time.sleep(self.next_at - now)
                now = self.next_at
            self.next_at = now + self.interval


_sec_limiter = RateLimiter(8)  # SEC fair-access limit is 10 requests/second
_web_limiter = RateLimiter(12)


class NotFound(Exception):
    pass


def _fetch(url, headers, opener=None, timeout=30, retries=4):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", **headers})
    for attempt in range(retries):
        try:
            resp = (opener or urllib.request.build_opener()).open(req, timeout=timeout)
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from e
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt * 1.5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def sec_get(url):
    _sec_limiter.wait()
    return _fetch(url, {"User-Agent": SEC_UA})


def sec_json(url):
    return json.loads(sec_get(url))


def web_get(url, opener=None, headers=None):
    _web_limiter.wait()
    return _fetch(url, {"User-Agent": WEB_UA, "Accept": "*/*", **(headers or {})}, opener=opener)


def web_json(url, opener=None, headers=None):
    return json.loads(web_get(url, opener=opener, headers=headers))


def cookie_opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
