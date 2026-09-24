// Shared helpers: data loading, number formatting, header, footer and ticker search.

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

export function pct(n, digits = 1, signed = false) {
  if (n == null || Number.isNaN(n)) return "n/a";
  const v = (n * 100).toFixed(digits);
  return (signed && n > 0 ? "+" : "") + v + "%";
}

export const times = (n) => (n == null ? "n/a" : `${n.toFixed(2)}x`);

export function date(iso, opts = { month: "short", day: "numeric" }) {
  if (!iso) return "n/a";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return d.toLocaleDateString("en-US", opts);
}

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
export const bandClass = (b) => ({ strong: "strong", fair: "fair", weak: "weak" }[b] || "");

export function meter(score, band) {
  if (score == null) return `<div class="meter"><span class="faint" style="font-size:13px">Not enough data</span></div>`;
  return `<div class="meter" role="img" aria-label="Score ${score} out of 100">
    <div class="meter-track"><div class="meter-fill ${bandClass(band)}" style="width:${score}%"></div></div>
    <span class="meter-val">${score}</span></div>`;
}

const NAV = [
  ["screener.html", "Screener"],
  ["insiders.html", "Insider buying"],
  ["report.html", "Ticker report"],
];

export function chrome() {
  const here = location.pathname.split("/").pop() || "index.html";
  const nav = document.querySelector("[data-nav]");
  if (nav) {
    nav.innerHTML = `<div class="wrap">
      <a class="brand" href="./"><span class="brand-mark" aria-hidden="true"><span></span></span><span class="brand-name">Ticker Finder</span></a>
      <nav class="nav-links" aria-label="Tools">${NAV.map(([h, l]) =>
        `<a href="${h}"${h === here ? ' aria-current="page"' : ""}>${l}</a>`).join("")}</nav>
      <span class="nav-meta" data-updated></span></div>`;
  }
  const foot = document.querySelector("[data-foot]");
  if (foot) {
    foot.innerHTML = `<div class="wrap">
      <div>
        <p>Ticker Finder is an educational research tool. Nothing here is investment advice, a recommendation, or a price target.
        Scores and fair-value ranges are estimates built from public filings and can be wrong. Do your own research.</p>
        <p>Data: SEC EDGAR filings and financial statements, Nasdaq listings, Yahoo Finance prices and headlines.
        Refreshed every weekday evening after the SEC's 10pm filing cutoff.</p>
      </div>
      <nav aria-label="Footer">${NAV.map(([h, l]) => `<a href="${h}">${l}</a>`).join("")}<a href="index.html#method">How it works</a></nav>
    </div>`;
  }
  load("meta.json").then((m) => {
    const el = document.querySelector("[data-updated]");
    if (el) el.textContent = `Updated ${date(m.market_date, { month: "short", day: "numeric", year: "numeric" })}`;
  }).catch(() => {});
}

// Ticker search with keyboard-navigable suggestions.
export async function attachSearch(form, { onPick } = {}) {
  const input = form.querySelector("input");
  const list = form.querySelector(".suggest");
  let rows = [];
  let active = -1;
  let matches = [];
  load("universe.json").then((u) => (rows = u)).catch(() => {});

  const go = (sym) => (onPick ? onPick(sym) : (location.href = `report.html?t=${encodeURIComponent(sym)}`));

  function render() {
    const q = input.value.trim().toUpperCase();
    if (!q) { list.classList.remove("open"); return; }
    const ql = q.toLowerCase();
    const exact = [], starts = [], named = [];
    for (const r of rows) {
      if (r[0] === q) exact.push(r);
      else if (r[0].startsWith(q)) starts.push(r);
      else if (r[1].toLowerCase().includes(ql)) named.push(r);
    }
    const byCap = (a, b) => (b[3] || 0) - (a[3] || 0);
    matches = [...exact, ...starts.sort(byCap), ...named.sort(byCap)].slice(0, 8);
    active = matches.length ? 0 : -1;
    list.innerHTML = matches.length
      ? matches.map((r, i) => `<button type="button" role="option" data-sym="${esc(r[0])}" aria-selected="${i === active}">
          <span class="s-sym">${esc(r[0])}</span><span class="s-name">${esc(r[1])}</span><span class="s-sec">${esc(r[2])}</span></button>`).join("")
      : `<div class="s-empty">No US-listed stock matches "${esc(input.value.trim())}".</div>`;
    list.classList.add("open");
  }
  function highlight() {
    [...list.querySelectorAll("button")].forEach((b, i) => b.setAttribute("aria-selected", i === active));
  }
  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("keydown", (e) => {
    if (!list.classList.contains("open")) return;
    if (e.key === "ArrowDown") { active = Math.min(active + 1, matches.length - 1); highlight(); e.preventDefault(); }
    if (e.key === "ArrowUp") { active = Math.max(active - 1, 0); highlight(); e.preventDefault(); }
    if (e.key === "Escape") list.classList.remove("open");
  });
  list.addEventListener("mousedown", (e) => {
    const b = e.target.closest("button[data-sym]");
    if (b) { e.preventDefault(); go(b.dataset.sym); list.classList.remove("open"); }
  });
  document.addEventListener("click", (e) => { if (!form.contains(e.target)) list.classList.remove("open"); });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim().toUpperCase();
    if (!q) { input.focus(); return; }
    const pick = matches[active]?.[0] || q;
    list.classList.remove("open");
    go(pick);
  });
}

export function searchMarkup(id, placeholder = "Ticker or company, e.g. AAPL") {
  return `<form class="search-row" role="search" autocomplete="off">
    <div class="field">
      <label class="sr-only" for="${id}">Search for a stock</label>
      <i class="ph ph-magnifying-glass" aria-hidden="true"></i>
      <input class="input" id="${id}" name="q" placeholder="${placeholder}" spellcheck="false" autocapitalize="characters">
      <div class="suggest" role="listbox"></div>
    </div>
    <button class="btn btn-primary" type="submit">Analyze</button>
  </form>`;
}
