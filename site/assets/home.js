import { attachSearch, chrome, date, esc, load, meter, money, pct, price, searchMarkup, signClass } from "./common.js";

chrome();

const searchHost = document.getElementById("hero-search");
searchHost.innerHTML = searchMarkup("hero-q");
attachSearch(searchHost.querySelector("form"));

const roleShort = (roles) => {
  if (!roles?.length) return "Insider";
  const r = roles.find((x) => /chief executive|ceo/i.test(x)) || roles.find((x) => /chief financial|cfo/i.test(x)) || roles[0];
  return r.replace(/Chief Executive Officer/i, "CEO").replace(/Chief Financial Officer/i, "CFO");
};

async function latest() {
  const list = document.querySelector("[data-latest]");
  try {
    const [ins, meta] = await Promise.all([load("insiders.json"), load("meta.json")]);
    const rows = ins.companies.filter((c) => c.tier <= 2).slice(0, 5);
    const pool = rows.length >= 3 ? rows : ins.companies.slice(0, 5);
    document.querySelector("[data-latest-date]").textContent = pool[0] && meta.last_filing_day ? `Filed through ${date(meta.last_filing_day)}` : "";
    if (!pool.length) {
      const span = meta.insider_pending?.length ? "in the days scanned so far" : `in the last ${ins.window_days} days`;
      list.innerHTML = `<li class="hp-empty">No open-market purchases were filed ${span}.</li>`;
      return;
    }
    list.innerHTML = pool.map((c) => {
      const b = c.buyers[0];
      const more = c.buyers.length > 1 ? ` +${c.buyers.length - 1} more` : "";
      return `<li><a class="hp-row" href="${c.symbol ? `report.html?t=${encodeURIComponent(c.symbol)}` : "insiders.html"}">
        <span class="sym">${esc(c.symbol || "n/a")}</span>
        <span class="who"><b>${esc(b.name)}</b><span>${esc(roleShort(b.roles))}${more}, ${esc(date(c.filed))}</span></span>
        <span class="amt"><b>${money(c.total_value)}</b><span class="${signClass(c.vs_paid)}">${c.vs_paid == null ? "" : pct(c.vs_paid, 1, true) + " vs. paid"}</span></span>
      </a></li>`;
    }).join("");
  } catch {
    list.innerHTML = `<li class="hp-empty">Insider data is not available yet. It appears after the first nightly update.</li>`;
  }
}

async function screener() {
  const host = document.querySelector("[data-top-screen]");
  try {
    const s = await load("screener.json");
    document.querySelector("[data-screen-count]").textContent = `${s.results.length} passed every check`;
    if (!s.results.length) {
      host.innerHTML = `<p class="muted" style="font-size:14px">No company passes every check today. The rules are strict on purpose.</p>`;
      return s;
    }
    host.innerHTML = s.results.slice(0, 5).map((r, i) => `
      <a class="tt-row" href="report.html?t=${encodeURIComponent(r.symbol)}">
        <span class="rank">${i + 1}</span><span class="sym">${esc(r.symbol)}</span>
        <span class="nm">${esc(r.name)}</span>${meter(r.score, r.score >= 70 ? "strong" : r.score >= 40 ? "fair" : "weak")}
      </a>`).join("");
    return s;
  } catch {
    host.innerHTML = `<p class="muted" style="font-size:14px">Screener results appear after the first nightly update.</p>`;
  }
}

async function insiderSummary() {
  const el = document.querySelector("[data-insider-summary]");
  try {
    const [ins, meta] = await Promise.all([load("insiders.json"), load("meta.json")]);
    const t1 = ins.companies.filter((c) => c.tier === 1).length;
    const t2 = ins.companies.filter((c) => c.tier === 2).length;
    // Counted here from the same Tier 1 and 2 companies, so it can never exceed the total above.
    const latest = ins.companies.filter((c) => c.tier <= 2 && c.filed === meta.market_date).length;
    el.innerHTML = `In the last ${ins.window_days} days, insiders bought stock in <b>${t1}</b> Tier 1 and <b>${t2}</b> Tier 2 companies.` +
      (latest ? ` Purchases at <b>${latest}</b> of them were filed on ${esc(date(meta.market_date))}.` : "");
  } catch {
    el.textContent = "Purchases appear after the first nightly update.";
  }
}

async function example(screen) {
  const host = document.querySelector("[data-example]");
  const link = document.querySelector("[data-example-link]");
  const sym = screen?.results?.[0]?.symbol;
  if (!sym) { host.innerHTML = `<p class="muted" style="font-size:14px">Search any US ticker above to open its report.</p>`; return; }
  try {
    const r = await load(`t/${sym}.json`);
    const bars = (r.health || []).flatMap((g) => g.bars).filter((b) => !b.context && b.score != null);
    const pick = ["net_margin", "net_cash", "ps"].map((k) => bars.find((b) => b.key === k)).filter(Boolean);
    const v = r.valuation;
    const tag = v?.verdict && { undervalued: "tag-up", overvalued: "tag-down", fair: "tag-mid" }[v.verdict];
    host.innerHTML = `
      <div class="ex-head"><div><span class="sym">${esc(r.symbol)}</span><span class="nm">${esc(r.name)}</span></div>
        ${tag ? `<span class="tag ${tag}">${v.verdict[0].toUpperCase() + v.verdict.slice(1)}</span>` : ""}</div>
      ${tag ? `<p class="ex-fv">Estimated fair value <b>${price(v.low)} to ${price(v.high)}</b> vs. price <b>${price(r.price)}</b></p>` : ""}
      <div class="ex-bars">${pick.map((b) => `<div class="ex-bar"><span>${esc(b.label)}</span>${meter(b.score, b.band)}</div>`).join("")}</div>`;
    link.href = `report.html?t=${encodeURIComponent(sym)}`;
    link.innerHTML = `Open the ${esc(sym)} report <i class="ph ph-arrow-right" aria-hidden="true"></i>`;
  } catch {
    host.innerHTML = `<p class="muted" style="font-size:14px">Search any US ticker above to open its report.</p>`;
  }
}

latest();
insiderSummary();
screener().then(example);
