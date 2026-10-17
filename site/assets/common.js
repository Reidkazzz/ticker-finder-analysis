// Shared helpers: data loading, number formatting, icons, the score tape, header, footer and ticker search.

const cache = new Map();
export async function load(name) {
  if (!cache.has(name)) {
    cache.set(name, fetch(`data/${name}`, { cache: "no-cache" }).then((r) => {
      if (!r.ok) throw Object.assign(new Error(`${name}: ${r.status}`), { status: r.status });
      return r.json();
    }).catch((e) => {
      cache.delete(name); // a network blip must not stick until the page is reloaded
      throw e;
    }));
  }
  return cache.get(name);
}

export const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// An icon from the sprite (assets/icons.svg), sized by the text around it and hidden from screen readers.
export const icon = (name, cls = "") =>
  `<svg class="i${cls ? ` ${cls}` : ""}" aria-hidden="true" focusable="false"><use href="assets/icons.svg#i-${name}"></use></svg>`;

// The site's name and its mark: a score tape, two parts earned and one not.
export const BRAND = "TFA";
export const BRAND_LONG = "Ticker Finder Analysis";
export const markSvg = (cls = "brand-mark") => `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
  <rect width="32" height="32" rx="7" fill="#2563eb"/><rect x="5.5" y="13" width="10" height="6" rx="1.5" fill="#ffffff"/>
  <rect x="17" y="13" width="5.5" height="6" rx="1.5" fill="#ffffff"/><rect x="24" y="13" width="2.5" height="6" rx="1.25" fill="#ffffff" fill-opacity=".4"/></svg>`;

// Rounds down, so $9,999,999 reads $9.9M and never looks like it crossed a $10M threshold.
export function money(n, { digits } = {}) {
  if (n == null || Number.isNaN(n)) return "n/a";
  const a = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  const cut = (v, d) => (Math.floor(v * 10 ** d + 1e-9) / 10 ** d).toFixed(d);
  if (a >= 1e12) return `${sign}$${cut(a / 1e12, digits ?? 2)}T`;
  if (a >= 1e9) return `${sign}$${cut(a / 1e9, digits ?? 2)}B`;
  if (a >= 1e6) return `${sign}$${cut(a / 1e6, digits ?? 1)}M`;
  if (a >= 1e3) return `${sign}$${cut(a / 1e3, digits ?? 0)}K`;
  return `${sign}$${Math.floor(a)}`;
}

export function price(n) {
  if (n == null) return "n/a";
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: n < 1 ? 4 : 2 });
}

// A value that rounds to zero reads "0%" or "0.0%", with no sign: toFixed would give -0.0002 as "-0.0".
export function pct(n, digits = 1, signed = false) {
  if (n == null || Number.isNaN(n)) return "n/a";
  const v = (n * 100).toFixed(digits);
  if (Number(v) === 0) return (0).toFixed(digits) + "%";
  return (signed && n > 0 ? "+" : "") + v + "%";
}

export const times = (n) => (n == null ? "n/a" : `${n.toFixed(2)}x`);

// A company name the SEC lists in capitals and of three or more long words ("STRATTEC SECURITY CORPORATION"), in title
// case like the others ("Strattec Security Corporation"). Shorter all-capital names are often brands that are written in
// capitals (PAMT CORP, AECOM) and are left as they are, as is any word with a digit or "&", "." or "-" (AT&T, CMB.TECH),
// and short joining words are lower case after the first ("Bank of America"). The original is kept for tooltips.
const SMALL_WORDS = new Set(["of", "and", "the", "for", "in", "on", "de", "du", "la"]);
export function displayName(name) {
  const s = String(name ?? "").replace(/\s{2,}/g, " ");
  if (s !== s.toUpperCase() || (s.match(/\b[A-Z]{4,}\b/g) || []).length < 3) return s;
  return s.replace(/[A-Z0-9][A-Z0-9'’&.-]*/g, (w, at) => {
    if (/[\d&.-]/.test(w.replace(/\.$/, "")) || w.length <= 3 && !/^(INC|CO|LTD|THE|AND|OF|FOR|DE)\.?$/.test(w)) return w;
    const lower = w.toLowerCase();
    return at > 0 && SMALL_WORDS.has(lower) ? lower : w[0] + lower.slice(1);
  });
}

export function date(iso, opts = { month: "short", day: "numeric" }) {
  if (!iso) return "n/a";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return d.toLocaleDateString("en-US", opts);
}
export const longDate = (iso) => date(iso, { month: "short", day: "numeric", year: "numeric" });

// The viewer's own calendar date. Data is built late in the evening, so "today" must mean the day it is read.
export function localToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function relDay(iso, today = localToday()) {
  if (!iso) return "";
  if (iso === today) return "Today";
  const d = Math.round((new Date(today + "T12:00:00") - new Date(iso + "T12:00:00")) / 864e5); // DST days are 23 or 25 hours
  if (d === 1) return "Yesterday";
  return date(iso, { weekday: "long", month: "short", day: "numeric" });
}

export const signClass = (n) => (n == null ? "" : n > 0 ? "up" : n < 0 ? "down" : "");
// A fraction as pct() shows it at `digits`: one that reads 0% is 0, so its color and words carry no direction either.
export const asShown = (n, digits = 1) => (n == null || Number((n * 100).toFixed(digits)) !== 0 ? n : 0);
// Today's price against the average price insiders paid, from the two prices, since the data's vs_paid is stored
// already rounded and rounding it again can move the last digit shown. None where the data gives none, as it may for a
// listing whose shares aren't the ones insiders report buying. The insiders page, the report and the home page agree.
export function vsPaid(price, avgPaid, stored) {
  if (stored == null) return null;
  return price && avgPaid ? price / avgPaid - 1 : stored;
}
export const bandClass = (b) => ({ strong: "strong", fair: "fair", weak: "weak" }[b] || "");
// The health score's bands (pipeline/report.py): 70 and over strong, 40 and over fair. Not used for the screener's score,
// whose own scale has no bands.
export const healthBand = (s) => (s == null ? null : s >= 70 ? "strong" : s >= 40 ? "fair" : "weak");

// `label` names the measure for screen readers ("Net profit margin: score 38 out of 100").
export function meter(score, band, empty = "Not enough data", label = "") {
  if (score == null) return `<div class="meter"><span class="meter-none">${esc(empty)}</span></div>`;
  return `<div class="meter" role="img" aria-label="${label ? `${esc(label)}: score` : "Score"} ${score} out of 100">
    <div class="meter-track"><div class="meter-fill ${bandClass(band)}" style="width:${score}%"></div></div>
    <span class="meter-val">${score}</span></div>`;
}

// A screener score as its tape: one slot per part, as wide as the part's maximum and filled to the points it earned,
// in the order the pipeline lists them (screener.SCORE_LABELS). The parts add up to the score.
export function tape(parts, cls = "") {
  if (!Array.isArray(parts) || !parts.length) return "";
  const cols = parts.map((p) => `${Math.max(p.max, 0.0001)}fr`).join(" ");
  return `<div class="tape${cls ? ` ${cls}` : ""}" style="grid-template-columns:${cols}" aria-hidden="true">${parts.map((p) =>
    `<span style="--f:${p.max ? Math.max(0, Math.min(1, p.points / p.max)).toFixed(3) : 0}"></span>`).join("")}</div>`;
}

const NAV = [
  ["screener.html", "Screener"],
  ["insiders.html", "Insider buying"],
  ["report.html", "Ticker report"],
];

// The page's header and footer. `search: false` leaves the header's search out (a page with its own search box).
export function chrome({ search = true } = {}) {
  const here = location.pathname.split("/").pop().replace(/\.html$/, "") || "index";
  const nav = document.querySelector("[data-nav]");
  if (nav) {
    nav.innerHTML = `<a class="skip" href="#main">Skip to content</a><div class="wrap">
      <a class="brand" href="./" aria-label="${BRAND}, ${BRAND_LONG}, home">${markSvg()}<span class="brand-name">${BRAND}</span><span class="brand-long">${BRAND_LONG}</span></a>
      <nav class="nav-links" aria-label="Tools">${NAV.map(([h, l]) =>
        `<a href="${h}"${h.replace(/\.html$/, "") === here ? ' aria-current="page"' : ""}>${l}</a>`).join("")}</nav>
      <div class="nav-end"><span class="data-status" data-updated></span>${search ? searchMarkup("nav-search", "Search a ticker", { compact: true }) : ""}</div></div>`;
    const form = nav.querySelector("form");
    if (form) attachSearch(form);
  }
  const foot = document.querySelector("[data-foot]");
  if (foot) {
    foot.innerHTML = `<div class="wrap">
      <div>
        <div class="foot-brand">${markSvg()}${BRAND} <span class="faint" style="font-weight:400">${BRAND_LONG}</span></div>
        <p>${BRAND} is an educational research tool. Nothing here is investment advice, a recommendation, or a price target.
        Scores and fair-value ranges are estimates built from public filings and can be wrong. Do your own research.</p>
        <p>Data: SEC EDGAR filings and financial statements, Nasdaq listings, Yahoo Finance prices, and headlines from Yahoo Finance, Google News and companies' press releases.
        Refreshed every weekday evening after the SEC's 10pm filing cutoff.</p>
      </div>
      <nav aria-label="Footer">${NAV.map(([h, l]) => `<a href="${h}">${l}</a>`).join("")}<a href="index.html#method">How it works</a></nav>
    </div>`;
  }
  load("meta.json").then(paintDataStatus).catch(() => {
    document.querySelectorAll("[data-source-line]").forEach((el) => { el.textContent = "The data's date could not be loaded."; });
  });
}

// What the data is, from meta.json: end-of-day prices from one trading day (price_date, the day most quotes closed on)
// and SEC filings read through a day, refreshed each weekday night, never live. Data from before price_date was recorded
// names only the update's day, not a close. Data more than STALE_DAYS older than today (a weekend and a holiday are
// fewer) says how old it is, in the header and in every page's source line.
const STALE_DAYS = 4;
export function daysOld(iso) {
  const [y, m, d] = String(iso).split("-").map(Number);
  const t = new Date();
  return Math.round((Date.UTC(t.getFullYear(), t.getMonth(), t.getDate()) - Date.UTC(y, m - 1, d)) / 864e5);
}
// When the update that built the data finished, in New York time, as the SEC's filing day is.
export function updatedAt(m) {
  const d = m?.generated_at ? new Date(m.generated_at) : null;
  if (!d || Number.isNaN(d.getTime())) return null;
  return `${d.toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" })} ET`;
}
// "Closing prices as of" the prices' day: each stock's latest close on or before it (one that didn't trade that day keeps
// its last, and its row says when). Older data, without price_date, names only the update's day.
export const closeWords = (m) => (m?.price_date ? `Closing prices as of ${longDate(m.price_date)}` : m?.market_date ? `Prices as of the update of ${longDate(m.market_date)}` : "Prices as of the latest update");
// How long ago the update ran, in whole days, or null without its time.
function updateAge(m) {
  const d = m?.generated_at ? new Date(m.generated_at) : null;
  return d && !Number.isNaN(d.getTime()) ? Math.floor((Date.now() - d.getTime()) / 864e5) : null;
}
export function paintDataStatus(m) {
  if (!m?.market_date) return;
  const day = longDate(m.price_date || m.market_date);
  const filed = longDate(m.last_filing_day || m.market_date);
  const at = updatedAt(m);
  // Stale when the update itself hasn't run for longer than a weekend and a holiday, whatever day the prices are from.
  const age = updateAge(m);
  const stale = age != null && age > STALE_DAYS;
  const title = `End-of-day data, not live. ${closeWords(m)}; SEC filings read through ${filed}.${at ? ` Updated ${at}.` : ""} Refreshed each weekday night.`;
  document.querySelectorAll("[data-updated]").forEach((el) => {
    el.classList.toggle("stale", stale);
    el.title = title;
    el.innerHTML = stale ? `${icon("warning")}Updated ${age} days ago` : `<span class="ds-dot" aria-hidden="true"></span>${m.price_date ? `Prices as of ${esc(day)}` : `Data as of ${esc(day)}`}`;
  });
  document.querySelectorAll("[data-source-line]").forEach((el) => {
    el.innerHTML = [stale ? `<span class="stale-note">${icon("warning")}Last updated ${age} days ago. The nightly update may not have run since.</span>` : "",
      `<span>${esc(closeWords(m))}</span>`, `<span>SEC filings through ${esc(filed)}</span>`,
      at ? `<span>Updated ${esc(at)}</span>` : "", `<span>End of day, not live</span>`].filter(Boolean).join("");
  });
}

// "/" anywhere outside a text field jumps to the header's search.
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
  const t = e.target;
  if (t.closest && t.closest("input, textarea, select, [contenteditable='true']")) return;
  const input = document.querySelector("#nav-search") || document.querySelector(".search-row input");
  if (input && input.offsetParent !== null) { e.preventDefault(); input.focus(); }
});

// On a phone a row of sector pills, or the report's row of section links, scrolls sideways, and the browser leaves one
// that is partly hidden where it is. Keyboard focus brings it fully into view, clear of the row's faded edge (its
// scroll-padding). A click does not, so nothing moves out from under the pointer before the click lands.
document.addEventListener("focusin", (e) => {
  const pill = e.target.closest?.(".pills.scroll .pill, .r-rail a");
  if (pill?.matches(":focus-visible")) pill.scrollIntoView({ block: "nearest", inline: "nearest" });
});

// Ticker search: a combobox whose suggestions are picked with the arrow keys and Enter, or with a click. The list of
// stocks (universe.json, about 400 KB) loads when the box is first used, and suggestions update once it arrives.
let searchSeq = 0;
export async function attachSearch(form, { onPick } = {}) {
  const input = form.querySelector("input");
  const list = form.querySelector(".suggest");
  const id = `suggest-${++searchSeq}`;
  list.id = id;
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");
  input.setAttribute("aria-controls", id);
  let rows = null;
  let loading = null;
  let active = -1;
  let matches = [];
  const ensure = () => (loading ||= load("universe.json").then((u) => { rows = u; render(); return u; }).catch(() => { loading = null; return null; }));

  const go = (sym) => (onPick ? onPick(sym) : (location.href = `report.html?t=${encodeURIComponent(sym)}`));
  const close = () => { list.classList.remove("open"); input.setAttribute("aria-expanded", "false"); input.removeAttribute("aria-activedescendant"); };
  const open = () => { list.classList.add("open"); input.setAttribute("aria-expanded", "true"); };

  function find(q) {
    const ql = q.toLowerCase();
    const exact = [], starts = [], named = [];
    for (const r of rows || []) {
      if (r[0] === q) exact.push(r);
      else if (r[0].startsWith(q)) starts.push(r);
      else if (r[1].toLowerCase().includes(ql)) named.push(r);
    }
    const byCap = (a, b) => (b[3] || 0) - (a[3] || 0);
    return [...exact, ...starts.sort(byCap), ...named.sort(byCap)].slice(0, 8);
  }
  function render() {
    const q = input.value.trim().toUpperCase();
    if (!q || document.activeElement !== input) { close(); return; }
    if (!rows) {
      list.innerHTML = `<div class="s-empty">Loading the list of stocks…</div>`;
      open();
      return;
    }
    matches = find(q);
    active = matches.length ? 0 : -1;
    list.innerHTML = matches.length
      ? matches.map((r, i) => `<div role="option" id="${id}-${i}" data-sym="${esc(r[0])}" aria-selected="${i === active}">
          <span class="s-sym">${esc(r[0])}</span><span class="s-name">${esc(displayName(r[1]))}</span><span class="s-sec">${esc(r[2])}</span></div>`).join("")
      : `<div class="s-empty">No US-listed stock matches "${esc(input.value.trim())}".</div>`;
    open();
    highlight();
  }
  function highlight() {
    [...list.querySelectorAll("[role=option]")].forEach((b, i) => b.setAttribute("aria-selected", i === active));
    if (active >= 0) {
      input.setAttribute("aria-activedescendant", `${id}-${active}`);
      list.querySelector(`#${id}-${active}`)?.scrollIntoView({ block: "nearest" });
    } else input.removeAttribute("aria-activedescendant");
  }
  input.addEventListener("focus", () => { ensure(); render(); });
  input.addEventListener("input", () => { ensure(); render(); });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { if (list.classList.contains("open")) { close(); e.preventDefault(); } else input.blur(); return; }
    if (!list.classList.contains("open") || !matches.length) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, matches.length - 1); highlight(); e.preventDefault(); }
    if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); highlight(); e.preventDefault(); }
  });
  list.addEventListener("mousedown", (e) => {
    const b = e.target.closest("[data-sym]");
    if (b) { e.preventDefault(); close(); go(b.dataset.sym); }
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = input.value.trim().toUpperCase();
    if (!q) { input.focus(); return; }
    // Before the list arrives, wait for it, so "apple" finds AAPL rather than a ticker named APPLE.
    if (!rows) { await ensure(); matches = rows ? find(q) : []; active = matches.length ? 0 : -1; }
    const pick = matches[active]?.[0] || q;
    close();
    go(pick);
  });
}

export function searchMarkup(id, placeholder = "Ticker or company, e.g. AAPL", { compact = false } = {}) {
  return `<form class="${compact ? "nav-search" : "search-row"}" role="search" autocomplete="off">
    <div class="field">
      <label class="sr-only" for="${id}">Search for a stock by ticker or company name</label>
      ${icon("search")}
      <input class="input" id="${id}" name="q" placeholder="${placeholder}" spellcheck="false" autocapitalize="characters">${compact ? `<kbd aria-hidden="true">/</kbd>` : ""}
      <div class="suggest" role="listbox" aria-label="Matching stocks"></div>
    </div>
    ${compact ? "" : `<button class="btn btn-primary" type="submit">Analyze</button>`}
  </form>`;
}
