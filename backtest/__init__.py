"""A point-in-time backtest of the screener: it reruns the pipeline's own code as it would have run on past dates, on the
SEC facts filed before each date and that week's prices, and measures how the companies it picked did over the next
52 weeks against the average listed stock. backtest/README.md explains the method, the data and what is not point in
time.

    python -m backtest.screener run --code <folder holding a pipeline package> --label <name>
    python -m backtest.screener compare <base label> <other label> ...
    python -m backtest.validate frames|market|today|past

Everything downloaded or worked out is kept under .cache/backtest/ (git-ignored), so a second run of a variant that
only changes pipeline/screener.py takes seconds.
"""
import io
import os
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache", "backtest")
# The SEC's bulk file of every company's XBRL facts (https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip),
# downloaded once. Its members are read one at a time and never extracted: 20,418 companies, 19 GB uncompressed.
COMPANYFACTS = os.path.join(CACHE, "companyfacts.zip")
FACTS_DIR = os.path.join(CACHE, "facts")          # the tag index built from it (facts.py)
CHARTS_DIR = os.path.join(CACHE, "charts")        # Yahoo weekly charts, one file per listing (data.py)
FORM345_DIR = os.path.join(CACHE, "form345")      # the SEC's insider transaction data sets (data.py)
SHARED_DIR = os.path.join(CACHE, "shared")        # today's SEC company records, shared by every date (asof.py)
STATE_DIR = os.path.join(CACHE, "state")          # per-date inputs and metrics (asof.py)
RESULTS_DIR = os.path.join(CACHE, "results")      # per-variant, per-date results and reports (screener.py)
CODE_DIR = os.path.join(CACHE, "code")            # code roots made from past commits (code_root)
UNIVERSE = os.path.join(CACHE, "universe.json")   # the snapshot of today's listings (data.py)

# The last Friday of September of each year tested: the screener as it would have run that evening, and the 52 weeks
# after it. The fourth date's 52 weeks end on 2026-09-25.
DATES = ["2022-09-30", "2023-09-29", "2024-09-27", "2025-09-26"]
# The code the tool's own downloads use (pipeline.net's SEC user agent and rate limits, universe.load_universe), fixed
# so a pipeline being edited alongside never breaks a download.
TOOLS_COMMIT = "719a878"


def code_root(commit):
    """The folder holding `commit`'s pipeline package (made with git archive the first time), for a variant's --code."""
    path = os.path.join(CODE_DIR, commit)
    if not os.path.isfile(os.path.join(path, "pipeline", "__init__.py")):
        os.makedirs(path, exist_ok=True)
        tar = subprocess.run(["git", "-C", ROOT, "archive", commit, "pipeline"], check=True, capture_output=True).stdout
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            t.extractall(path, filter="data")
    return path


def use_code(root):
    """Puts `root` first on sys.path, so `import pipeline` loads that copy, and checks that it did. Must run before any
    pipeline module is imported."""
    root = os.path.abspath(root)
    if any(m == "pipeline" or m.startswith("pipeline.") for m in sys.modules):
        loaded = os.path.dirname(os.path.dirname(os.path.abspath(sys.modules["pipeline"].__file__)))
        if os.path.normcase(loaded) != os.path.normcase(root):
            raise RuntimeError(f"pipeline already imported from {loaded}, not {root}")
        return
    sys.path.insert(0, root)
    import pipeline  # noqa: F401
    loaded = os.path.dirname(os.path.dirname(os.path.abspath(pipeline.__file__)))
    if os.path.normcase(loaded) != os.path.normcase(root):
        raise RuntimeError(f"pipeline imported from {loaded}, not {root}")


def use_tools_code():
    """The fixed pipeline copy the downloads use (TOOLS_COMMIT)."""
    use_code(code_root(TOOLS_COMMIT))
