import { chrome, displayName, esc, icon, load, longDate, money, pct, price, tape, times } from "./common.js";
import { debtTag, flagDefs as sharedFlagDefs, flagIcons, flagTile, ledger, under } from "./ledger.js";

chrome();

const params = new URLSearchParams(location.search);
// The table's column sets, each its own view of the same companies.
const VIEWS = ["overview", "valuation", "financials", "price", "score"];
// Each sort's order is fixed: a score from best to worst, a ratio from cheapest, a margin from highest.
const SORT_NAMES = ["score", "ps", "pb", "net_margin", "fcf_yield", "revenue_growth", "drawdown", "analysts", "mcap"];
const state = {
  f: {}, // each filter in use, by its key (FILTERS below): the id of the option chosen
  sort: SORT_NAMES.includes(params.get("sort")) ? params.get("sort") : "score",
  q: (params.get("q") || "").trim(), // the panel's filter by ticker or company name
  view: VIEWS.includes(params.get("view")) ? params.get("view") : "overview",
  group: "descriptive", // the group of filters the panel shows
  open: null,
};
const host = document.querySelector("[data-results]");
const builder = document.querySelector("[data-builder]");
const filterEl = document.querySelector("[data-filter]");
const countEl = document.querySelector("[data-count]");
const sortEl = document.querySelector("[data-sort]");
const resetEl = document.querySelector("[data-reset]");
let data;
let meta = null;
let insiderDays = null; // the insider scan's window (meta.json), for "in the last 30 days"
let insiderPending = 0; // the insider scan's days not yet read (meta.json), which the filter's empty state names

// Missing values sort last whichever way a column sorts. Ties keep the score's order (the sort is stable).
const low = (v) => v ?? Number.MAX_VALUE;
const high = (v) => v ?? -Number.MAX_VALUE;
const SORTS = {
  score: (a, b) => b.score - a.score,
  ps: (a, b) => a.ps - b.ps,
  pb: (a, b) => low(a.pb) - low(b.pb),
  net_margin: (a, b) => b.net_margin - a.net_margin,
  fcf_yield: (a, b) => high(b.fcf_yield) - high(a.fcf_yield),
  revenue_growth: (a, b) => high(b.revenue_growth) - high(a.revenue_growth),
  drawdown: (a, b) => high(b.drawdown) - high(a.drawdown),
  analysts: (a, b) => low(a.analysts) - low(b.analysts),
  mcap: (a, b) => a.mcap - b.mcap,
};
const SORT_DIR = { score: "descending", ps: "ascending", pb: "ascending", net_margin: "descending", fcf_yield: "descending",
  revenue_growth: "descending", drawdown: "descending", analysts: "ascending", mcap: "ascending" };

// Older data (a screener.json from before a feature) lacks its keys, and the page hides what it can't show.
const hasPb = () => data.results.some((r) => "pb" in r);
const hasInsiders = () => data.insiders_checked === true;
const ofDrawdown = () => pct(data.rules?.out_of_favor_drawdown ?? 0.25, 0);
const window_ = () => (insiderDays ? `in the last ${insiderDays} days` : "recently");

// The ledger an opened row unfolds, shared with the ticker report (ledger.js).
const ctx = () => ({ rules: data.rules, industries: data.industries, insiderDays });
const flagDefs = () => sharedFlagDefs(ctx());
const flags = (r) => flagIcons(r, ctx());

// The flags any company on the list carries, each icon beside its name: the same whichever filter is shown, so the
// table doesn't move when they change.
function flagLegend() {
  const used = flagDefs().filter(([k]) => data.results.some((r) => r.flags.includes(k)));
  return used.length ? `<ul class="flag-legend" aria-label="What the flag icons mean">${used.map((d) => `<li>${flagTile(d)}<span>${esc(d[3])}</span></li>`).join("")}</ul>` : "";
}

const detailId = (sym) => `ledger-${sym.replace(/[^A-Za-z0-9]/g, "-")}`;
function detailRow(r, cols) {
  return `<tr class="detail" id="${detailId(r.symbol)}"><td colspan="${cols}">${ledger(r, ctx())}</td></tr>`;
}

// ---- The filters ----

// A filter tests a figure as the table shows it, so a margin shown as 10.0% passes "10% or more" and a price to sales
// shown as 1.00x doesn't pass "Under 1x". A missing figure passes no option but "Any".
const shownPct = (v, d = 1) => (v == null ? null : Number((v * 100).toFixed(d)));
const shownX = (v) => (v == null ? null : Number(v.toFixed(2)));
const opt = (id, label, test) => ({ id, label, test });
const R = () => data.rules || {};
const moreThan = (get, d, list, word = "or more") => list.map((n) => opt(`${n}-plus`, `${n}% ${word}`, (r) => { const v = shownPct(get(r), d); return v != null && v >= n; }));

// Each filter: its key (also its parameter in the page's address), the group the panel shows it in, its name, the name
// of its "any" choice, whether today's data can answer it, and its choices, each with the test a company must pass.
const FILTERS = [
  { key: "sector", group: "descriptive", label: "Sector", any: "All sectors",
    options: () => data.sectors.filter((s) => s.checked > 0 || s.excluded)
      .map((s) => ({ ...opt(s.name, s.excluded ? `${s.name}, not screened` : s.name, (r) => r.sector === s.name), excluded: s.excluded })) },
  { key: "industry", group: "descriptive", label: "Industry", any: "All industries",
    // The industries of the companies in the sector chosen, so each one listed has a company in it.
    options: () => [...new Set(data.results.filter((r) => !state.f.sector || r.sector === state.f.sector).map((r) => r.industry).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b)).map((n) => opt(n, n, (r) => r.industry === n)) },
  { key: "mcap", group: "descriptive", label: "Market value",
    options: () => [[250e6, "250m", "$250M"], [500e6, "500m", "$500M"], [1e9, "1b", "$1B"], [2e9, "2b", "$2B"]]
      .filter(([v]) => !R().max_mcap || v < R().max_mcap).map(([v, id, w]) => opt(`under-${id}`, `Under ${w}`, (r) => r.mcap != null && r.mcap < v)) },
  { key: "analysts", group: "descriptive", label: "Analysts",
    options: () => {
      const few = R().undiscovered_max_analysts ?? 2;
      return [opt("none", "None", (r) => r.analysts === 0), opt("few", `${few} or fewer`, (r) => r.analysts != null && r.analysts <= few),
        opt("more", `${few + 1} or more`, (r) => r.analysts != null && r.analysts > few)];
    } },
  { key: "ps", group: "valuation", label: "Price / sales",
    options: () => [0.5, 1, 1.5].map((n) => opt(`under-${n}`, `Under ${n}x`, (r) => { const v = shownX(r.ps); return v != null && v < n; })) },
  { key: "pb", group: "valuation", label: "Price / book", ok: () => hasPb(),
    // Under 1x is the Under book value flag's own test; the table shows a figure under 1 cut, not rounded, to 0.99x.
    options: () => [opt("under-1", "Under 1x, under book value", (r) => r.pb != null && r.pb < 1),
      ...[1.5, 2, 3].map((n) => opt(`under-${n}`, `Under ${n}x`, (r) => { const v = shownX(r.pb); return v != null && v < n; }))] },
  { key: "cy", group: "valuation", label: "Cash yield", options: () => moreThan((r) => r.fcf_yield, 1, [5, 10, 15]) },
  { key: "dd", group: "valuation", label: "Below 52-week high",
    options: () => [...new Set([10, Math.round((R().out_of_favor_drawdown ?? 0.25) * 100), 50])].sort((a, b) => a - b)
      .map((n) => opt(`${n}-plus`, `${n}% or more below`, (r) => { const v = shownPct(r.drawdown, 0); return v != null && v >= n; })) },
  { key: "margin", group: "financial", label: "Net margin", options: () => moreThan((r) => r.net_margin, 1, [5, 10, 15]) },
  { key: "growth", group: "financial", label: "Sales growth",
    options: () => [opt("shrinking", "Shrinking", (r) => { const v = shownPct(r.revenue_growth); return v != null && v < 0; }),
      opt("growing", "Growing", (r) => { const v = shownPct(r.revenue_growth); return v != null && v > 0; }),
      ...moreThan((r) => r.revenue_growth, 1, [10, 20], "or more growth")] },
  { key: "debt", group: "financial", label: "Debt",
    options: () => [opt("none", "No debt", (r) => r.debt_status === "none"), opt("net-cash", "Net cash, more cash than debt", (r) => r.debt_status === "net_cash"),
      opt("none-or-net-cash", "No debt or net cash", (r) => r.debt_status === "none" || r.debt_status === "net_cash"),
      opt("low", "Low", (r) => r.debt_status === "low")] },
  { key: "years", group: "financial", label: "Profitable years",
    // "3 of 3" where every company was checked over the same number of years, as today; in words otherwise.
    options: () => {
      const n = new Set(data.results.map((r) => r.years_checked));
      const y = n.size === 1 ? [...n][0] : null;
      return [opt("every", y ? `${y} of ${y}` : "Every year checked", (r) => r.profitable_years >= r.years_checked),
        opt("all-but-one", y ? `${y - 1} of ${y}` : "All but one year checked", (r) => r.profitable_years === r.years_checked - 1)];
    } },
  { key: "score", group: "signals", label: "Score", options: () => [60, 50, 40].map((n) => opt(`${n}-plus`, `${n} or more`, (r) => r.score >= n)) },
  { key: "insider", group: "signals", label: "Insider buying", ok: () => hasInsiders(),
    options: () => [opt("buying", "Tier 1 or 2 buying", (r) => r.flags.includes("insiders_buying")),
      opt("below", "Below insiders' price", (r) => r.flags.includes("below_insider_price"))] },
  { key: "flag", group: "signals", label: "Flag",
    options: () => flagDefs().filter(([k]) => !["insiders_buying", "below_insider_price", "above_upper_band"].includes(k))
      .map(([k, , , label]) => opt(k, label, (r) => r.flags.includes(k))) },
  { key: "band", group: "signals", label: "Upper band",
    options: () => [opt("no-warning", "No pullback warning", (r) => !r.flags.includes("above_upper_band")),
      opt("above", "Above it, wait for a pullback", (r) => r.flags.includes("above_upper_band"))] },
];
const GROUP_NAMES = { descriptive: "Descriptive", valuation: "Valuation", financial: "Financial", signals: "Signals", all: "All filters" };
const usable = () => FILTERS.filter((f) => !f.ok || f.ok());
const choice = (f) => (state.f[f.key] ? f.options().find((o) => o.id === state.f[f.key]) : null);
const inUse = () => usable().filter((f) => choice(f));
const sectorRow = () => (state.f.sector ? data.sectors.find((s) => s.name === state.f.sector) : null);

// Drops a choice the data can't answer: a filter today's data lacks, an option it doesn't list (an industry outside the
// sector chosen), or a parameter from an old link.
function validate() {
  for (const f of FILTERS) {
    if (!state.f[f.key]) { delete state.f[f.key]; continue; }
    if ((f.ok && !f.ok()) || !f.options().some((o) => o.id === state.f[f.key])) { delete state.f[f.key]; setParam(f.key, null); }
  }
}

// The companies that pass every filter in use but `skip`, before the panel's text filter.
const passing = (skip) => {
  const tests = inUse().filter((f) => f.key !== skip).map((f) => choice(f).test);
  return data.results.filter((r) => tests.every((t) => t(r)));
};

// The panel's filter: a ticker that starts with what is typed, or a company name that contains it.
const queryOk = (r) => {
  if (!state.q) return true;
  const q = state.q.toLowerCase();
  return r.symbol.toLowerCase().startsWith(q) || displayName(r.name).toLowerCase().includes(q) || String(r.name).toLowerCase().includes(q);
};

function setParam(key, value) {
  const u = new URL(location);
  value == null ? u.searchParams.delete(key) : u.searchParams.set(key, value);
  history.replaceState(null, "", u);
}

function paintReset() {
  resetEl.hidden = !inUse().length && !state.q;
  // With nothing in use the line under the dropdowns goes, so the list starts higher.
  document.querySelector(".builder-foot").hidden = resetEl.hidden;
}

// The panel's dropdowns for the group shown. Each choice counts the companies it would show with the other filters in
// use, before the text filter, so a dropdown says what picking it would leave.
function renderFields(focusKey) {
  const el = document.querySelector("[data-fields]");
  const shown = usable().filter((f) => state.group === "all" || f.group === state.group);
  el.classList.toggle("all", state.group === "all");
  el.innerHTML = shown.map((f) => {
    const pool = passing(f.key);
    const opts = f.options();
    const now = state.f[f.key] || "";
    return `<div class="ff${now ? " on" : ""}"><label for="f-${f.key}">${esc(f.label)}</label>
      <select class="select" id="f-${f.key}" data-f="${f.key}"><option value="">${esc(f.any || "Any")} (${pool.length})</option>${opts.map((o) =>
        `<option value="${esc(o.id)}"${o.id === now ? " selected" : ""}>${esc(o.label)}${o.excluded ? "" : ` (${pool.filter(o.test).length})`}</option>`).join("")}</select></div>`;
  }).join("");
  if (focusKey) el.querySelector(`[data-f="${focusKey}"]`)?.focus();
}

function renderGroups() {
  const used = inUse();
  document.querySelectorAll("[data-group]").forEach((b) => {
    const g = b.dataset.group;
    const n = g === "all" ? used.length : used.filter((f) => f.group === g).length;
    b.setAttribute("aria-pressed", String(g === state.group));
    b.querySelector("[data-group-count]").innerHTML = n ? `<span class="sr-only">, </span>${n}<span class="sr-only"> in use</span>` : "";
  });
  const all = document.querySelector("[data-active-count]");
  all.innerHTML = used.length ? `<span class="sr-only">, </span>${used.length}<span class="sr-only"> in use</span>` : "";
}

// The filters in use as chips, each removed by its own button.
function renderChips() {
  const el = document.querySelector("[data-chips]");
  el.innerHTML = inUse().map((f) => {
    const o = choice(f);
    return `<li><button class="chip" type="button" data-remove="${f.key}" aria-label="Remove the filter ${esc(f.label)}: ${esc(o.label)}">
      <span class="chip-k">${esc(f.label)}</span> ${esc(o.label)}${icon("x")}</button></li>`;
  }).join("");
  el.hidden = !el.children.length;
  document.querySelector("[data-chips-none]").hidden = !el.hidden;
  paintReset();
}

// After any filter changes: the dropdowns' counts, the chips, the groups' counts and the table.
function refresh(focusKey) {
  validate();
  state.open = null;
  renderFields(focusKey);
  renderGroups();
  renderChips();
  renderTable();
}

function setFilter(key, id) {
  if (id) state.f[key] = id; else delete state.f[key];
  setParam(key, id || null);
}

function setSort(key) {
  if (!SORTS[key] || (key === "pb" && !hasPb())) return;
  state.sort = key;
  state.open = null;
  sortEl.value = key;
  setParam("sort", key === "score" ? null : key);
  renderTable();
}

// Companies that passed every other check but were left out because a check could not be confirmed on this run:
// their US registration ("us") or their debt ("debt"), each listed in "unconfirmed" with its reason in "reason".
// Older data lists only registration problems, as symbols or as objects without "unconfirmed".
const its = (n) => (n === 1 ? "its" : "their");
const isLeftOut = (n) => (n === 1 ? "it is left out" : "they are left out");
const UNCONFIRMED = {
  us: (n) => `passed every financial check, but ${its(n)} US headquarters or incorporation could not be confirmed with the SEC today, so ${isLeftOut(n)}`,
  debt: (n) => `passed every other check, but ${its(n)} debt could not be read reliably from ${its(n)} filings, so the low-debt rule could not be confirmed and ${isLeftOut(n)}`,
  "debt us": (n) => `passed every other check, but ${its(n)} debt could not be read reliably from ${its(n)} filings and ${its(n)} US headquarters or incorporation could not be confirmed with the SEC today, so ${isLeftOut(n)}`,
};
const andList = (a) => (a.length < 2 ? a.join("") : `${a.slice(0, -1).join(", ")} and ${a[a.length - 1]}`);
const symLink = (s, title) => `<a class="mono" href="report.html?t=${encodeURIComponent(s)}"${title ? ` title="${esc(title)}"` : ""}>${esc(s)}</a>`;

// Shown under the list while no filter but the sector is in use: these companies are on neither side of any other.
function unverifiedNote() {
  if (inUse().some((f) => f.key !== "sector")) return "";
  const list = (data.unverified || []).map((u) => (typeof u === "string" ? { symbol: u } : u))
    .filter((u) => u && u.symbol && (!state.f.sector || !u.sector || u.sector === state.f.sector));
  if (!list.length) return "";
  const groups = new Map();
  for (const u of list) {
    const key = Array.isArray(u.unconfirmed) && u.unconfirmed.length ? [...u.unconfirmed].sort().join(" ") : "us";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(u);
  }
  const order = ["us", "debt", "debt us"];
  const keys = [...groups.keys()].sort((a, b) => (order.indexOf(a) + 1 || 9) - (order.indexOf(b) + 1 || 9));
  const sentences = keys.map((key) => {
    const g = groups.get(key);
    const n = g.length;
    const links = andList(g.map((u) => symLink(u.symbol, [u.name && u.name.replace(/\.$/, ""), u.reason].filter(Boolean).join(". "))));
    const why = UNCONFIRMED[key] ? UNCONFIRMED[key](n)
      : `passed every other check, but one check could not be confirmed today, so ${isLeftOut(n)}`;
    // A registration lookup that failed today may work tomorrow. A debt reading changes only with new filings.
    const again = key.split(" ").includes("us") ? " The next update checks again." : "";
    return `${n} ${n === 1 ? "company" : "companies"} ${why}: ${links}.${again}`;
  });
  return `<div class="notice unverified">${icon("info")}
    <div>${sentences.map((s) => `<p>${s}</p>`).join("")}</div></div>`;
}

// ---- The table's columns ----

// Each column: its key, its name, the sort it sets (if any), its label on a phone, whether it holds words, a title
// for its name, and its cell. A figure's color is its sign: emerald when it is up or in the company's favor.
const sign = (v) => (v == null ? "" : v > 0 ? " up" : v < 0 ? " down" : "");
const fig = (k, h, label, cell, more = {}) => ({ k, h, label, cell, ...more });
const C = {
  flags: fig("flags", "Flags", null, (r) => flags(r), { txt: true }),
  mcap: fig("mcap", "Market value", "Value", (r) => money(r.mcap), { sort: "mcap", cls: () => " ink" }),
  price: fig("price", "Price", "Price", (r) => price(r.price)),
  ps: fig("ps", "P/S", "P/S", (r) => times(r.ps), { sort: "ps", title: "Price to sales: market value over the latest fiscal year's sales" }),
  pb: fig("pb", "P/B", "P/B", (r) => under(r.pb), { sort: "pb", ok: () => hasPb(), title: "Price to book: market value over book value" }),
  book: fig("book", "Book value", "Book", (r) => money(r.book_value), { title: "Shareholders' equity less preferred stock", ok: () => hasPb() }),
  margin: fig("margin", "Net margin", "Margin", (r) => pct(r.net_margin), { sort: "net_margin", cls: (r) => sign(shownPct(r.net_margin)),
    tip: (r) => (r.one_time_note && r.net_margin_reported != null ? `Leaves out one-time items. Reported margin: ${pct(r.net_margin_reported)}` : "") }),
  reported: fig("reported", "Reported", "Reported", (r) => pct(r.net_margin_reported), { title: "Net margin as reported, one-time items included",
    cls: (r) => sign(shownPct(r.net_margin_reported)) }),
  debt: fig("debt", "Debt", "Debt", (r) => debtTag(r), { title: "No debt, more cash than debt (net cash), or long-term debt as a multiple of equity" }),
  cy: fig("fcf", "Cash yield", "Cash yield", (r) => pct(r.fcf_yield), { sort: "fcf_yield", cls: (r) => sign(shownPct(r.fcf_yield)) }),
  fcf: fig("fcfv", "Free cash flow", "Free cash", (r) => money(r.fcf), { cls: (r) => sign(r.fcf) }),
  sales: fig("sales", "Sales", "Sales", (r) => money(r.revenue), { title: "The latest fiscal year's sales" }),
  growth: fig("growth", "Sales growth", "Growth", (r) => (r.revenue_growth == null ? "n/a" : pct(r.revenue_growth, 1, true)),
    { sort: "revenue_growth", cls: (r) => sign(shownPct(r.revenue_growth)) }),
  cash: fig("cash", "Cash less debt", "Cash less debt", (r) => money(r.net_cash), { cls: (r) => sign(r.net_cash),
    title: "Cash, short-term investments and long-term securities, less debt" }),
  years: fig("years", "Profitable", "Profitable", (r) => `${r.profitable_years} of ${r.years_checked}`, { title: "Years profitable, without one-time items, of the years checked" }),
  dd: fig("dd", "Below high", "Below high", (r) => (r.drawdown == null ? "n/a" : pct(r.drawdown, 0)), { sort: "drawdown" }),
  high: fig("high", "52-week high", "52-wk high", (r) => price(r.high52)),
  band: fig("band", "Upper band", "Upper band", (r) => price(r.upper_band), { title: "The average of the last 20 weekly closes plus two standard deviations of them" }),
  an: fig("an", "Analysts", "Analysts", (r) => r.analysts ?? "n/a", { sort: "analysts" }),
  ins: fig("ins", "Tier 1 or 2 buying", "Insiders", (r) => (r.insiders ? `Tier ${esc(String(r.insiders.tier))}, ${money(r.insiders.total_value)}` : `<span class="faint">None</span>`),
    { ok: () => hasInsiders(), title: "Open-market insider buying of $1 million or more, or a buyer growing their stake by 20% or more" }),
};
const VIEW_COLUMNS = {
  overview: ["flags", "mcap", "ps", "pb", "margin", "debt", "cy", "dd", "an"],
  valuation: ["price", "mcap", "ps", "pb", "book", "sales", "fcf", "cy"],
  financials: ["sales", "growth", "margin", "reported", "fcf", "debt", "cash", "years"],
  price: ["price", "high", "dd", "band", "an", "ins", "flags"],
};
// The score's parts by their key, named as "How the 100-point score works" names them.
const PART_NAMES = { ps: "Cheap vs. sales", debt: "Debt", margin: "Profit margin", fcf: "Cash generation", growth: "Sales growth",
  small: "Under $2 billion", out_of_favor: "Out of favor", undiscovered: "Few analysts", under_book: "Under book value", insiders_buying: "Insiders buying" };
function partColumns() {
  const first = data.results[0]?.score_parts || [];
  // Whole points when the pipeline sends them; older data has one-decimal parts, which keep their decimal.
  const whole = data.results.every((r) => r.score_parts.every((p) => Number.isInteger(p.points)));
  const pts = (n) => (whole ? String(n) : (Math.round(n * 10) / 10).toFixed(1));
  return first.map((p0) => fig(`pt-${p0.key}`, esc(PART_NAMES[p0.key] || p0.label), PART_NAMES[p0.key] || p0.label, (r) => {
    const p = r.score_parts.find((x) => x.key === p0.key);
    return p ? `<b>${pts(p.points)}</b><span class="of">/${esc(String(p.max))}</span>` : "n/a";
  }, { cls: (r) => (r.score_parts.find((x) => x.key === p0.key)?.points ? "" : " zero"), part: true }));
}

const columns = () => [
  { k: "rank", h: `<span class="sr-only">Rank</span>#` },
  { k: "co", h: "Company", txt: true },
  { k: "score", h: "Score", sort: "score", txt: true },
  ...(state.view === "score" ? partColumns() : VIEW_COLUMNS[state.view].map((k) => C[k]).filter((c) => !c.ok || c.ok())),
  { k: "go", h: `<span class="sr-only">Report</span>` },
];

function head(cols) {
  return `<thead><tr>${cols.map((c) => {
    const cls = ` class="c-${c.k}${c.txt ? " txt" : ""}${c.part ? " part" : ""}"`;
    const title = c.title ? ` title="${esc(c.title)}"` : "";
    if (!c.sort) return `<th scope="col"${cls}${title}>${c.h}</th>`;
    const on = state.sort === c.sort;
    return `<th scope="col"${cls}${title}${on ? ` aria-sort="${SORT_DIR[c.sort]}"` : ""}><button class="sort" type="button" data-sort-key="${c.sort}">${c.h}${icon(on ? (SORT_DIR[c.sort] === "descending" ? "sort-down" : "sort-up") : "sort")}<span class="sr-only">${on ? ", sorted" : ", sort"}</span></button></th>`;
  }).join("")}</tr></thead>`;
}

function row(r, i, cols) {
  const sym = esc(r.symbol);
  return `<tr class="row" data-sym="${sym}">${cols.map((c) => {
    switch (c.k) {
      case "rank": return `<td class="c-rank">${i + 1}</td>`;
      case "co": return `<td class="txt c-co"><button class="row-toggle" type="button" aria-expanded="${state.open === r.symbol}" aria-controls="${detailId(r.symbol)}">
        ${icon("caret-right", "caret")}<span class="co"><span class="sym">${sym}</span> <span class="nm" title="${esc(r.name)}">${esc(displayName(r.name))}</span></span></button></td>`;
      case "score": return `<td class="txt c-score"><div class="score" role="img" aria-label="Score ${esc(String(r.score))} out of 100"><b>${esc(String(r.score))}</b>${tape(r.score_parts)}</div></td>`;
      case "go": return `<td class="c-go"><a class="go" href="report.html?t=${encodeURIComponent(r.symbol)}" aria-label="Open the ${sym} report">${icon("arrow-up-right")}</a></td>`;
      case "flags": return `<td class="txt c-flags">${flags(r)}</td>`;
      default: {
        const tip = c.tip ? c.tip(r) : "";
        return `<td class="c-${c.k}${c.cls ? c.cls(r) : ""}"${c.label ? ` data-label="${esc(c.label)}"` : ""}${tip ? ` title="${esc(tip)}"` : ""}>${c.cell(r)}</td>`;
      }
    }
  }).join("")}</tr>`;
}

// What the table's figures are, under it: which year, which currency, how many rows and the prices' date.
function renderStatus(rows) {
  const el = document.querySelector("[data-status]");
  const checked = data.sectors.reduce((a, s) => a + (s.checked || 0), 0);
  const items = ["Latest fiscal year and balance sheet", "US dollars",
    `${rows.length} of ${data.results.length} ${data.results.length === 1 ? "row" : "rows"}${checked ? `, ${checked.toLocaleString("en-US")} stocks checked` : ""}`,
    meta?.market_date ? `Prices from ${longDate(meta.market_date)}` : null].filter(Boolean);
  el.innerHTML = items.map((t) => `<li>${esc(t)}</li>`).join("");
  el.hidden = false;
}

// The notes under the table: what its figures mean in the view shown.
function tableNote() {
  const notes = {
    overview: `Open a row to see how its score adds up and its figure for each rule. Cash yield is free cash flow as a share of market value.${hasPb() ? " Price to book is market value over book value: shareholders' equity less preferred stock." : ""}`,
    valuation: `Sales and free cash flow are the latest fiscal year's. Cash yield is free cash flow as a share of market value.${hasPb() ? " Book value is shareholders' equity less preferred stock." : ""}`,
    financials: "Sales, growth, margins and free cash flow are the latest fiscal year's. Net margin leaves out one-time items; reported margin counts them. Cash less debt is cash, short-term investments and long-term securities less debt, from the latest balance sheet.",
    price: `Prices are the last close. The upper band is the average of the last 20 weekly closes plus two standard deviations of them.${hasInsiders() ? ` Tier 1 or 2 buying is open-market buying by insiders ${esc(window_())}: $1 million or more, or a buyer growing their stake by 20% or more.` : ""}`,
    score: "Each part's points out of its most. The parts add up to the score; open a row to see what each was measured on.",
  };
  return `<p class="table-note">${notes[state.view]}</p>`;
}

function renderTable() {
  host.setAttribute("aria-busy", "false");
  const after = document.querySelector("[data-after]");
  const sec = sectorRow();
  // The companies the sector leaves, those every filter leaves, then the rows the text filter keeps of them.
  const base = data.results.filter((r) => !state.f.sector || r.sector === state.f.sector);
  const listed = passing(null).sort(SORTS[state.sort]);
  const rows = listed.filter(queryOk);
  const others = inUse().filter((f) => f.key !== "sector").length;
  const scope = (n) => (state.f.sector ? `${esc(state.f.sector)} ${n === 1 ? "stock" : "stocks"}` : n === 1 ? "company" : "companies");
  // With a sector chosen it is the list's scope, so the rest are "the other" filters.
  const other = state.f.sector ? "other " : "";
  const asked = [others ? (others === 1 ? `the ${other}filter` : others === 2 ? `both ${other}filters` : `all ${others} ${other}filters`) : "", state.q ? `"${esc(state.q)}"` : ""].filter(Boolean).join(" and ");
  countEl.innerHTML = sec?.excluded ? `${esc(state.f.sector)} is not screened`
    : asked ? `<b>${rows.length}</b> of the ${base.length} ${scope(base.length)} that passed every check ${rows.length === 1 ? "matches" : "match"} ${asked}`
    : state.f.sector ? `<b>${rows.length}</b> of ${sec?.checked ?? 0} ${esc(state.f.sector)} stocks passed every check`
    : `<b>${rows.length}</b> ${rows.length === 1 ? "company" : "companies"} passed every check`;
  document.querySelector("[data-matches]").textContent = sec?.excluded ? "n/a" : String(rows.length);
  // The flag icons' legend sits on the count's line, beside the table it explains, in the views that show flags.
  document.querySelector("[data-result-meta]").innerHTML = rows.length && !sec?.excluded && VIEW_COLUMNS[state.view]?.includes("flags") ? flagLegend() : "";
  renderStatus(sec?.excluded ? [] : rows);
  after.innerHTML = "";

  if (sec?.excluded) {
    const see = Array.isArray(data.financials_list) ? ` <a href="#financials">See the banks and insurers under book value below.</a>` : "";
    host.innerHTML = `<div class="empty grid-empty"><h2>${esc(state.f.sector)} companies are not screened</h2><p>${esc(sec.excluded)}${see}</p></div>`;
    return;
  }
  if (!base.length) {
    host.innerHTML = `<div class="empty grid-empty"><h2>No ${esc(state.f.sector)} company passes today</h2>
      <p>That is normal. The checks are strict on purpose, and some sectors rarely have cheap, debt-light, profitable small caps.
      Try another sector or come back after the next update.</p></div>`;
    after.innerHTML = unverifiedNote();
    return;
  }
  if (listed.length && !rows.length) {
    // Only the text filter empties the list: say so, and point to the report, which covers every listed stock.
    const ticker = /^[A-Za-z][A-Za-z.]{0,5}$/.test(state.q) ? state.q.toUpperCase() : null;
    host.innerHTML = `<div class="empty grid-empty"><h2>No company on this list matches "${esc(state.q)}"</h2>
      <p>The filter looks at the tickers and company names of the ${listed.length} ${listed.length === 1 ? "company" : "companies"} on it.${ticker
        ? ` To see how ${esc(ticker)} looks against everything else, <a href="report.html?t=${encodeURIComponent(ticker)}">open its report</a>.` : ""}</p>
      <p class="empty-act"><button class="btn btn-ghost btn-sm" type="button" data-clear-filter>Clear the text filter</button></p></div>`;
    return;
  }
  if (!rows.length) {
    // The filters leave nothing. With the insider filter alone, say why: the scan's window and any days not read yet.
    const only = inUse().filter((f) => f.key !== "sector");
    const notYet = insiderPending ? ` (${insiderPending} filing day${insiderPending === 1 ? " is" : "s are"} not scanned yet)` : "";
    const why = only.length > 1 ? "Remove a filter above, or clear them all."
      : only[0]?.key === "insider" ? `None of them had Tier 1 or 2 insider buying ${esc(window_())}${state.f.insider === "below" ? " at an average price above today's price" : ""}${notYet}.`
      : "None of them passes it.";
    host.innerHTML = `<div class="empty grid-empty"><h2>No company on the list matches ${only.length === 1 ? "this filter" : "all of these filters"}</h2>
      <p>${base.length} ${scope(base.length)} passed every check. ${why}</p>
      <p class="empty-act"><button class="btn btn-ghost btn-sm" type="button" data-clear-all>Clear filters</button></p></div>`;
    return;
  }
  const cols = columns();
  host.innerHTML = `<div class="table-wrap"><table class="data screen-table view-${state.view}">
    <caption class="sr-only">Companies that passed every check, ${esc(sortEl.selectedOptions[0]?.textContent || "best score")} first. Open a company to see its score and rules.</caption>
    ${head(cols)}
    <tbody>${rows.map((r, i) => row(r, i, cols) + (state.open === r.symbol ? detailRow(r, cols.length) : "")).join("")}</tbody></table></div>`;
  after.innerHTML = `${tableNote()}${unverifiedNote()}`;
  watch(host.querySelector(".table-wrap"));
}

// Opens or closes one row's ledger in place, leaving the rest of the table (and its scroll position) as it is.
function toggle(sym) {
  const tbody = host.querySelector("tbody");
  if (!tbody) return;
  const prev = state.open;
  tbody.querySelector("tr.detail")?.remove();
  tbody.querySelectorAll("tr.row .row-toggle[aria-expanded='true']").forEach((b) => b.setAttribute("aria-expanded", "false"));
  state.open = prev === sym ? null : sym;
  if (!state.open) return;
  const tr = tbody.querySelector(`tr.row[data-sym="${CSS.escape(sym)}"]`);
  const r = data.results.find((x) => x.symbol === sym);
  if (!tr || !r) return;
  tr.querySelector(".row-toggle").setAttribute("aria-expanded", "true");
  tr.insertAdjacentHTML("afterend", detailRow(r, columns().length));
  fitLedger();
}

// A table wider than its box scrolls sideways, its right edge fading while there is more to see. An opened row's ledger
// keeps to the box's width rather than running the table's whole width. One observer serves every redraw.
let observed = null;
const ro = window.ResizeObserver ? new ResizeObserver(() => observed && edge(observed)) : null;
function edge(wrap) {
  const scrolls = wrap.scrollWidth > wrap.clientWidth + 1 || wrap.querySelector("table").offsetWidth > wrap.clientWidth + 1;
  wrap.classList.toggle("scrolls", scrolls);
  wrap.classList.toggle("more-right", scrolls && wrap.scrollLeft + wrap.clientWidth < wrap.scrollWidth - 1);
  fitLedger();
}
function fitLedger() {
  const wrap = host.querySelector(".table-wrap");
  const inner = wrap?.querySelector(".ledger");
  if (inner) inner.style.width = wrap.classList.contains("scrolls") ? `${wrap.clientWidth - 2}px` : "";
}
function watch(wrap) {
  observed = wrap;
  wrap.addEventListener("scroll", () => edge(wrap), { passive: true });
  ro?.observe(wrap);
  ro?.observe(wrap.querySelector("table")); // fonts arriving can widen it
  edge(wrap);
}

host.addEventListener("click", (e) => {
  const sortBtn = e.target.closest("[data-sort-key]");
  if (sortBtn) { setSort(sortBtn.dataset.sortKey); host.querySelector(`[data-sort-key="${sortBtn.dataset.sortKey}"]`)?.focus(); return; }
  if (e.target.closest("[data-clear-all]")) { clearAll(); return; }
  if (e.target.closest("a, .ledger")) return;
  const tr = e.target.closest("tr.row");
  if (tr) toggle(tr.dataset.sym);
});

// ---- The lists under the ranked one ----

// Each is hidden in the page and shown only when the data has it, so a screener.json from before these lists leaves
// them out. A long list shows its first SHOWN rows and a button for the rest.
const SHOWN = 15;
const coCell = (r) => `<td class="txt co-cell"><div class="co"><a class="sym" href="report.html?t=${encodeURIComponent(r.symbol)}">${esc(r.symbol)}</a> <span class="nm" title="${esc(r.name)}">${esc(displayName(r.name))}</span></div></td>`;
const asOf = (iso) => (iso ? longDate(iso) : "n/a");

// A list wider than its box scrolls sideways too, its right edge fading while there is more to see. Each list keeps its
// own frame, so the lists share this observer rather than the ranked table's one. It also watches the table, which
// "Show all" can widen.
const listRo = window.ResizeObserver ? new ResizeObserver((es) => es.forEach((e) => fade(e.target.closest(".list-wrap")))) : null;
function fade(wrap) {
  wrap.classList.toggle("more-right", wrap.scrollLeft + wrap.clientWidth < wrap.scrollWidth - 1);
}

function section(key, body, count) {
  const el = document.querySelector(`[data-section="${key}"]`);
  el.hidden = false;
  el.querySelector("[data-body]").innerHTML = body;
  const wrap = el.querySelector(".list-wrap");
  if (wrap) {
    wrap.addEventListener("scroll", () => fade(wrap), { passive: true });
    listRo?.observe(wrap);
    listRo?.observe(wrap.querySelector("table"));
    fade(wrap);
  }
  const c = el.querySelector("[data-sec-count]");
  if (c && count != null) c.textContent = typeof count === "number" ? `${count} ${count === 1 ? "company" : "companies"}` : `${count} industries`;
  const more = el.querySelector("[data-more]");
  if (more) {
    more.onclick = () => {
      const extra = el.querySelectorAll("tr[data-extra]");
      const opening = more.getAttribute("aria-expanded") !== "true";
      extra.forEach((tr) => { tr.hidden = !opening; });
      more.setAttribute("aria-expanded", String(opening));
      more.textContent = opening ? "Show fewer" : more.dataset.label;
      if (opening) {
        // Keep keyboard focus in the list, at the first row it revealed: on its link, or on the row itself where it has
        // none (the industries), since the button has moved far down the page.
        const first = extra[0], link = first?.querySelector("a");
        if (link) link.focus();
        else if (first) { first.tabIndex = -1; first.focus(); }
      } else {
        more.scrollIntoView({ block: "nearest" }); // a long list closing above the button would leave it far up the page
      }
    };
  }
  index[key] = count;
}

// head: the column names. The first column (the company or industry) and those numbered in `text` hold words, aligned
// left (class "txt"); the rest hold figures, aligned right.
function listTable(caption, head, rows, cell, note = "", text = []) {
  const extra = rows.length - SHOWN;
  return `<div class="table-wrap list-wrap"><table class="data list">
    <caption class="sr-only">${esc(caption)}</caption>
    <thead><tr>${head.map((h, i) => `<th scope="col"${i === 0 || text.includes(i) ? ` class="txt"` : ""}>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r, i) => `<tr${i >= SHOWN ? " data-extra hidden" : ""}>${cell(r)}</tr>`).join("")}</tbody></table></div>
    ${extra > 0 ? `<button class="btn btn-ghost btn-sm list-more" type="button" data-more aria-expanded="false" data-label="Show all ${rows.length}">Show all ${rows.length}</button>` : ""}${note}`;
}

function unconfirmedNote(syms, what) {
  if (!syms?.length) return "";
  return `<div class="notice unverified">${icon("info")}<p>${syms.length} more ${syms.length === 1 ? "company meets" : "companies meet"} ${what}, but where ${syms.length === 1 ? "it is" : "they are"} incorporated or based could not be confirmed with the SEC today: ${andList(syms.map((s) => symLink(s)))}. The next update checks again.</p></div>`;
}

function renderIndustries() {
  if (!Array.isArray(data.industries)) return;
  const out = data.industries.filter((x) => x.out_of_favor);
  const market = data.market_median_ps;
  // The marking is named only where a table shows it. Each count goes as a string, which section() counts in industries.
  const lead = (marked) => `<p class="list-note">${data.industries.length} industries measured${market != null ? `. The whole market's median price to sales is ${times(market)}${marked ? `; an industry whose median is under it is marked "under market"` : ""}` : ""}.</p>`;
  if (!data.industries.length) {
    section("industries", `<div class="panel empty"><h3>Industries could not be measured today</h3><p>Too few companies had a year of prices loaded to measure any industry.</p></div>`, "0");
    return;
  }
  if (!out.length) {
    section("industries", `<div class="panel empty"><h3>No industry is out of favor today</h3><p>In every industry measured, the median company trades less than ${ofDrawdown()} below its 52-week high.</p></div>${lead(false)}`, `0 of ${data.industries.length}`);
    return;
  }
  // An industry cheaper than the whole market says so in words, not only in green.
  const cheap = (x) => x.median_ps != null && market != null && x.median_ps < market;
  section("industries", `${listTable("Industries out of favor", ["Industry", "Companies", "Median below high", "Median price / sales"], out, (x) => `
    <td class="txt ind-cell"><b>${esc(x.industry)}</b><span>${esc(x.sector)}</span></td><td>${x.count}</td><td>${pct(x.median_drawdown, 0)}</td>
    <td${cheap(x) ? ` class="up"` : ""}>${cheap(x) ? `<span class="cell-sub">under market</span>` : ""}${times(x.median_ps)}</td>`)}${lead(true)}`, `${out.length} of ${data.industries.length}`);
}

function runway(r) {
  if (r.runway_years != null) {
    const n = Math.max(1, Math.round(r.runway_years * 12));
    return r.runway_years < 1 ? `${n} month${n === 1 ? "" : "s"}` : `${r.runway_years.toFixed(1)} years`;
  }
  return r.runway_note === "not_burning" ? `<span class="faint">Not burning cash</span>`
    : r.runway_note === "stale" ? `<span class="faint" title="Its latest fiscal year of figures (${esc(String(r.fiscal_year ?? ""))}) ended more than 15 months before its balance sheet, so its cash burn is out of date">Out of date</span>`
    : `<span class="faint" title="Its free cash flow could not be read from its filings">Unknown</span>`;
}

function renderNetCash() {
  if (!Array.isArray(data.net_cash_list)) return;
  const rows = data.net_cash_list;
  const note = unconfirmedNote(data.net_cash_unconfirmed, "the other tests");
  if (!rows.length) {
    section("net_cash", `<div class="panel empty"><h3>No company is worth less than its net cash today</h3><p>None of the US companies checked has a market value under its cash and short-term investments minus all debt, preferred stock and outside stakes in its subsidiaries.</p></div>${note}`, 0);
    return;
  }
  // Each list's ratio is worked out from its figures, which are stored to the dollar. The stored ratio is rounded to 4
  // places, which can lift one just under 1 to 1.00 (a price to book of 0.99996), so under() couldn't cut it to 0.99x.
  section("net_cash", listTable("Companies worth less than their net cash", ["Company", "Market value", "Net cash", "Value / net cash", "Free cash flow", "Cash runway", "Balance sheet"], rows, (r) => `
    ${coCell(r)}<td>${money(r.mcap)}</td><td>${money(r.net_cash)}</td><td>${under(r.mcap / r.net_cash)}</td>
    <td class="${r.fcf != null && r.fcf < 0 ? "down" : ""}">${money(r.fcf)}</td><td>${runway(r)}</td><td>${esc(asOf(r.balance_as_of))}</td>`,
  `<p class="list-note">Cheapest against net cash first. Free cash flow is for the latest fiscal year; cash and debt are from the balance sheet dated on the right. "Not burning cash" means free cash flow was zero or more in its latest fiscal year.</p>${note}`), rows.length);
}

function renderFinancials() {
  if (!Array.isArray(data.financials_list)) return;
  const rows = data.financials_list;
  const note = unconfirmedNote(data.financials_unconfirmed, "the other tests");
  if (!rows.length) {
    section("financials", `<div class="panel empty"><h3>No profitable bank or insurer trades under book value today</h3></div>${note}`, 0);
    return;
  }
  // Price to book from its figures, as for net cash above.
  section("financials", listTable("Profitable banks and insurers under book value", ["Company", "Kind", "Market value", "Book used", "Price / book", "Profit last year", "Return on equity"], rows, (r) => `
    ${coCell(r)}<td class="txt">${esc(r.kind === "bank" ? "Bank" : r.industry || "Financial")}</td><td>${money(r.mcap)}</td>
    <td>${r.book_basis === "tangible" ? "Tangible" : "Book"}, ${money(r.book)}</td>
    <td>${under(r.mcap / r.book)}</td><td>${money(r.net_income)}</td><td>${pct(r.roe)}</td>`,
  `<p class="list-note">Lowest price to book first. A bank is measured on tangible book value (equity less preferred stock, goodwill and other intangible assets), any other on book value (equity less preferred stock). Profit and return on equity leave out one-time items and are what is left to common shareholders, after preferred dividends.</p>${note}`, [1]), rows.length);
}

function renderGiants() {
  if (!Array.isArray(data.giants)) return;
  const rows = data.giants;
  if (!rows.length) {
    section("giants", `<div class="panel empty"><h3>No company worth over $90 billion trades above 10 times its sales today</h3></div>`, 0);
    return;
  }
  // Older data has no sales_basis: its sales were the latest fiscal year's.
  const period = (r) => (r.sales_basis === "ttm" ? `the 12 months to ${asOf(r.sales_end)}` : `fiscal ${r.fiscal_year ?? "year"}`);
  section("giants", listTable("Companies worth over $90 billion at more than 10 times sales", ["Company", "Sector", "Market value", "Sales", "Price / sales"], rows, (r) => `
    ${coCell(r)}<td class="txt">${esc(r.sector)}</td><td>${money(r.mcap)}</td><td><span class="cell-sub">${esc(period(r))}</span>${money(r.revenue)}</td><td class="down">${times(r.ps)}</td>`,
  `<p class="list-note">Highest price to sales first, on each company's sales over the last 12 months where its quarterly reports give them, otherwise its latest fiscal year's.</p>`, [1]), rows.length);
}

// The rail of links to the lists under the ranked one, each with its count.
const index = {};
function renderIndex() {
  const el = document.querySelector("[data-index]");
  const items = [["how", "How the list is made", null], ["industries", "Industries out of favor", index.industries],
    ["net-cash", "Under 1x cash", index.net_cash], ["financials", "Banks and insurers under book value", index.financials], ["giants", "Overpriced giants", index.giants]]
    .filter(([id]) => !document.getElementById(id)?.hidden);
  el.innerHTML = `<span class="index-label">On this page</span>${items.map(([id, label, n]) => `<a href="#${id}">${esc(label)}${n != null ? `<span class="count">${n}</span>` : ""}</a>`).join("")}`;
  el.hidden = false;
}

// ---- Explanations wired to the data the page was built with ----

// The rules from the data (8% margin in data from before September 2026), the newer flags and the score's weights only
// where the data has them.
function explainRules() {
  const R = data.rules || {};
  const margin = R.min_net_margin;
  if (margin != null) {
    const n = Math.round(margin * 10000) / 100;
    document.querySelectorAll("[data-margin-pct]").forEach((el) => { el.textContent = `${n}%`; });
    document.querySelectorAll("[data-margin-cents]").forEach((el) => { el.textContent = `${n} cent${n === 1 ? "" : "s"}`; });
    document.querySelectorAll("[data-thin-margin]").forEach((el) => { el.hidden = margin > 0.05; });
  }
  const words = {
    max_ps: (v) => String(v),
    max_mcap: (v) => money(v).replace(/\.00B$/, " billion").replace(/B$/, " billion"),
    bonus_mcap: (v) => money(v).replace(/\.00B$/, " billion").replace(/B$/, " billion"),
    max_lt_de: (v) => (v === 0.5 ? "half" : `${times(v)}`),
    min_profitable_years: (v) => String(v),
    min_revenue_growth: (v) => pct(Math.abs(v), 0),
    out_of_favor_drawdown: (v) => pct(v, 0),
  };
  document.querySelectorAll("[data-rule]").forEach((el) => {
    const k = el.dataset.rule;
    if (R[k] != null && words[k]) el.textContent = words[k](R[k]);
  });
  const fresh = Array.isArray(data.industries);
  document.querySelectorAll("[data-new-flag]").forEach((el) => { el.hidden = !fresh; });
  const w = data.score_weights || {};
  const stated = Object.keys(STATED_WEIGHTS).every((k) => w[k] === STATED_WEIGHTS[k]) && Object.keys(w).length === Object.keys(STATED_WEIGHTS).length;
  const help = document.querySelector("[data-score-help]");
  help.hidden = !stated;
  // The note on which flags also earn points names the newer flags and the stated parts, so it needs both.
  document.querySelector("[data-flag-points]").hidden = !stated || !fresh;
  if (stated) {
    help.querySelector("[data-tape-key]").innerHTML = tape(Object.entries(STATED_WEIGHTS).map(([key, max]) => ({ key, max, points: max })), "tape-lg");
  }
}
// The weights "Ranked by 100 points" states (screener.SCORE_WEIGHTS), in the pipeline's order. Data scored with other
// weights hides it, so the page never explains a score by weights it wasn't built with; each row's own parts still show.
const STATED_WEIGHTS = { ps: 15, debt: 20, margin: 15, fcf: 10, growth: 10, small: 5, out_of_favor: 5, undiscovered: 5, under_book: 10, insiders_buying: 5 };

function renderProof(meta) {
  // The claim carries its own proof: how many of the stocks checked passed all eight rules. The pipeline counts one per
  // listing, so a company with two share classes is checked twice: "stocks", not "companies".
  const checked = data.sectors.reduce((a, s) => a + (s.checked || 0), 0);
  const n = data.results.length;
  if (checked) document.querySelector("[data-claim]").textContent = `${n.toLocaleString("en-US")} of ${checked.toLocaleString("en-US")} stocks pass all eight quality checks`;
  document.querySelector("[data-proof-date]").textContent = meta?.market_date ? longDate(meta.market_date) : "n/a";
  // The screen at a glance, from the data: its market value limit (screener.RULES) and today's top score.
  const cap = data.rules?.max_mcap;
  if (cap) document.querySelector('[data-stat="mcap"]').textContent = `Under ${cap % 1e9 === 0 ? `$${cap / 1e9}B` : money(cap)}`;
  const top = Math.max(...data.results.map((r) => r.score));
  document.querySelector('[data-stat="top"]').textContent = n ? String(top) : "None";
  const src = document.querySelector("[data-sources-date]");
  if (src && meta?.market_date) {
    src.textContent = `The latest update read SEC filings through ${longDate(meta.last_filing_day || meta.market_date)}, with prices from ${longDate(meta.market_date)}.`;
  }
}

// The panel's text filter narrows the table as it is typed, and stays in the page's address (?q=) like the rest.
filterEl.value = state.q;
let typing;
filterEl.addEventListener("input", () => {
  clearTimeout(typing);
  typing = setTimeout(() => {
    state.q = filterEl.value.trim();
    state.open = null;
    setParam("q", state.q || null);
    paintReset();
    if (data) renderTable();
  }, 120);
});
filterEl.addEventListener("keydown", (e) => { if (e.key === "Escape" && filterEl.value) { e.preventDefault(); clearFilter(); } });
function clearFilter() {
  filterEl.value = "";
  state.q = "";
  setParam("q", null);
  paintReset();
  if (data) renderTable();
}
host.addEventListener("click", (e) => { if (e.target.closest("[data-clear-filter]")) { clearFilter(); filterEl.focus(); } });

// Every filter and the text filter off; the sort and the columns shown stay as they are.
function clearAll() {
  for (const f of FILTERS) setFilter(f.key, null);
  filterEl.value = "";
  state.q = "";
  setParam("q", null);
  refresh();
  filterEl.focus();
}

// A link into "How the list is made" (screener.html#scoring, or to anything inside a section) opens its section.
function openTarget() {
  const el = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
  for (let d = el?.closest("details"); d; d = d.parentElement?.closest("details")) d.open = true;
}
window.addEventListener("hashchange", openTarget);

// The panel: a dropdown sets its filter, a chip removes one, a group button shows that group's dropdowns, and on a
// phone a button opens the dropdowns.
document.querySelector("[data-fields]").addEventListener("change", (e) => {
  const s = e.target.closest("[data-f]");
  if (!s) return;
  setFilter(s.dataset.f, s.value);
  refresh(s.dataset.f);
});
document.querySelector("[data-chips]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-remove]");
  if (!b) return;
  const li = b.closest("li");
  const key = (li.nextElementSibling || li.previousElementSibling)?.querySelector("[data-remove]")?.dataset.remove;
  setFilter(b.dataset.remove, null);
  refresh();
  // Keyboard focus moves to the chip beside the one removed, or to the text filter when none is left.
  ((key && document.querySelector(`[data-remove="${key}"]`)) || filterEl).focus();
});
document.querySelector("[data-groups]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-group]");
  if (!b) return;
  state.group = b.dataset.group;
  renderGroups();
  renderFields();
});
const toggleEl = document.querySelector("[data-builder-toggle]");
toggleEl.addEventListener("click", () => {
  const open = !builder.classList.contains("open");
  builder.classList.toggle("open", open);
  toggleEl.setAttribute("aria-expanded", String(open));
  toggleEl.querySelector("[data-toggle-word]").textContent = open ? "Hide filters" : "Show filters";
});
document.querySelector("[data-views]").addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (!b) return;
  state.view = b.dataset.view;
  state.open = null;
  setParam("view", state.view === "overview" ? null : state.view);
  paintViews();
  renderTable();
});
function paintViews() {
  document.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === state.view)));
}

sortEl.addEventListener("change", (e) => setSort(e.target.value));
resetEl.addEventListener("click", clearAll);

Promise.all([load("screener.json"), load("meta.json").catch(() => null)]).then(([d, m]) => {
  data = d;
  meta = m;
  insiderDays = m?.insider_days ?? null;
  insiderPending = m?.insider_pending?.length ?? 0;
  if (insiderDays) document.querySelectorAll("[data-window]").forEach((el) => { el.textContent = insiderDays; });
  if (!hasPb()) {
    document.querySelector("[data-needs-pb]")?.remove();
    if (state.sort === "pb") state.sort = "score";
  }
  // The filters a link carries (screener.html?sector=Technology&insider=buying), each checked against today's data.
  for (const f of FILTERS) {
    const v = params.get(f.key);
    if (v) state.f[f.key] = v;
  }
  validate();
  // The panel opens on the group of the first filter in use.
  state.group = inUse()[0]?.group || "descriptive";
  sortEl.value = state.sort;
  paintViews();
  renderProof(m);
  explainRules();
  refresh();
  renderIndustries();
  renderNetCash();
  renderFinancials();
  renderGiants();
  renderIndex();
  // A link to a list further down (screener.html#giants) lands on it once the lists are drawn, and a link into the
  // explanation opens its section first.
  openTarget();
  if (location.hash) document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView({ block: "start", behavior: "instant" });
}).catch((err) => {
  console.error(err);
  host.setAttribute("aria-busy", "false");
  host.innerHTML = `<div class="empty grid-empty"><h2>Screener data is not available yet</h2><p>Results appear after the first nightly update runs. If you expected them, reload the page in a minute.</p></div>`;
});
