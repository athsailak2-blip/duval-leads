#!/usr/bin/env python3
"""
Build a PRE-RENDERED static dashboard (public/index.html) from the same CSV
sources the live dashboard uses. Data is inlined as JSON so the page loads
instantly from any static host (GitHub Pages, Cloudflare Pages, Netlify, or a
plain static file server) -- no Flask, no per-request CSV parsing.

Run after the scrapers update the CSVs (also called at the end of run_daily.py).
Output: /root/duval/public/index.html
"""
from __future__ import annotations
import json, logging
from pathlib import Path

from dashboard import collect, SOURCES, ROWS, SCHEMA  # reuse the exact same normalized loaders

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("build_static")

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "public"
OUT.mkdir(parents=True, exist_ok=True)

# Keep only the columns the (wholesaler-facing) table renders.
KEEP = ("source", "lead_type", "record_date", "property_address", "name",
        "amount", "status", "case_number", "detail", "fields", "extra")

# Inlined data template (mirrors dashboard.py HTML, no fetch()).
TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Duval County Leads</title>
<style>
  :root{
    --bg:#0d1117;--card:#161b22;--ink:#e6edf3;--mut:#9aa6b2;--acc:#3b82f6;
    --grn:#3fb950;--red:#f85149;--amber:#e3b341;--cyan:#39c5cf;--violet:#bc8cff;
    --line:#222a35;--chip:#1c232c;--hover:#1b2230;
  }
  *{box-sizing:border-box}
  body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,Arial;
       background:linear-gradient(180deg,#0b0f15 0%,#0d1117 240px);color:var(--ink)}
  header{padding:18px 22px;background:linear-gradient(120deg,#11161f,#161d29);
         border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;
         box-shadow:0 2px 14px rgba(0,0,0,.35)}
  h1{font-size:21px;margin:0 0 4px;font-weight:700;letter-spacing:.2px}
  h1 .pin{color:var(--acc)}
  .sub{color:var(--mut);font-size:13px;margin-bottom:14px}
  .bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
  select,input,button{background:#0d1117;color:var(--ink);border:1px solid #30363d;
         border-radius:8px;padding:9px 12px;font-size:14px;outline:none;transition:.15s}
  select:focus,input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(59,130,246,.18)}
  input[type=search]{min-width:260px;flex:1}
  label.chk{display:flex;align-items:center;gap:7px;color:var(--mut);font-size:13.5px;
         cursor:pointer;user-select:none;padding:6px 10px;border:1px solid var(--line);
         border-radius:8px;background:#0d1117}
  label.chk input{accent-color:var(--acc);width:16px;height:16px}
  .chips{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}
  .chip{background:var(--chip);border:1px solid var(--line);color:var(--mut);
        padding:6px 13px;border-radius:20px;cursor:pointer;user-select:none;font-size:13px;
        transition:.15s}
  .chip:hover{background:var(--hover);color:var(--ink)}
  .chip.on{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:600;
           box-shadow:0 2px 8px rgba(59,130,246,.35)}
  main{padding:18px 22px;max-width:1500px;margin:0 auto}
  .cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:14px 18px;min-width:140px;flex:1;box-shadow:0 1px 4px rgba(0,0,0,.25)}
  .card b{display:block;font-size:26px;line-height:1.1}
  .card .amt{color:var(--grn)}
  .card span{color:var(--mut);font-size:12.5px}
  .count{color:var(--mut);margin:4px 0 12px;font-size:13.5px}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{padding:11px 12px;text-align:left;border-bottom:1px solid #1c232c;vertical-align:top}
  th{position:sticky;top:150px;background:#12171f;cursor:pointer;white-space:nowrap;
     color:var(--mut);font-weight:600;font-size:12.5px;letter-spacing:.3px}
  th:hover{color:var(--ink)}
  th .ar{color:var(--acc)}
  tr:hover td{background:#12171f}
  .tag{display:inline-block;padding:3px 10px;border-radius:11px;font-size:11px;font-weight:700;
       white-space:nowrap;letter-spacing:.2px}
  .s-OfficialRecords{background:#06222b;color:#5fd0e0}
  .s-Foreclosure{background:#2e0a1c;color:#f29bc0}
  .s-TaxDeed{background:#2e2608;color:#ecc066}
  .s-VacantResidential{background:#240a2e;color:#c79bf0}
  .s-TaxDelinquent{background:#06291f;color:#5fe0a8}
  .s-Eviction{background:#2e2508;color:#e9d27f}
  .s-CoreCivil{background:#061a2e;color:#6fb0f0}
  .s-Civil{background:#061a2e;color:#6fb0f0}
  a{color:var(--acc);text-decoration:none;font-weight:600}
  a:hover{text-decoration:underline}
  .amt{color:var(--grn);font-weight:700}
  .prop{font-weight:600;color:#fff}
  .wrap{color:var(--mut);max-width:280px}
  .scroll{overflow:auto;max-height:74vh;border:1px solid var(--line);border-radius:12px;
          box-shadow:inset 0 0 18px rgba(0,0,0,.25)}
  .btn{background:var(--acc);color:#fff;border:none;cursor:pointer;font-weight:600;
       padding:8px 14px;box-shadow:0 2px 8px rgba(59,130,246,.3)}
  .btn:hover{background:#2f6fd6}
  .btn.ghost{background:#0d1117;border:1px solid #30363d;box-shadow:none;color:var(--ink)}
  .pill{font-size:11px;color:var(--mut);border:1px solid var(--line);border-radius:8px;
        padding:2px 8px;background:#0d1117}
  .s-OfficialRecords{background:#06222b;color:#5fd0e0}
  .s-Foreclosure{background:#2e0a1c;color:#f29bc0}
  .s-TaxDeed{background:#2e2608;color:#ecc066}
  .s-VacantResidential{background:#240a2e;color:#c79bf0}
  .s-TaxDelinquent{background:#06291f;color:#5fe0a8}
  .s-Eviction{background:#2e2508;color:#e9d27f}
  .s-CoreCivil{background:#061a2e;color:#6fb0f0}
  .s-Civil{background:#061a2e;color:#6fb0f0}
  .badge{display:inline-block;padding:3px 10px;border-radius:11px;font-size:11px;font-weight:700;letter-spacing:.2px}
  .st-open{background:#06291f;color:#5fe0a8}
  .st-closed{background:#1c232c;color:#8b98a5}
  .st-sale{background:#06222b;color:#5fd0e0}
  .st-other{background:#1c232c;color:#8b98a5}
  th{border-bottom:2px solid #2d3644}
  @media(max-width:720px){
    .card{min-width:120px;flex:1 1 40%}
    input[type=search]{min-width:100%}
    th,td{padding:9px 8px;font-size:12.5px}
  }
</style>
</head>
<body>
<header>
  <h1>🏠 Duval County <span class="pin">Distressed Leads</span></h1>
  <div class="sub">Fresh foreclosures, tax deeds, liens, probate &amp; vacant-property leads — updated daily. Green = open/actionable.</div>
  <div class="bar">
    <select id="source"><option value="ALL">All sources</option></select>
    <input id="q" type="search" placeholder="Search address, name, case #, parcel...">
    <label class="chk"><input type="checkbox" id="openonly" checked> Open leads only</label>
    <button class="btn" onclick="dl()">⬇ Download CSV</button>
    <span id="status" class="count"></span>
  </div>
  <div class="chips" id="chips"></div>
</header>
<main>
  <div class="cards" id="cards"></div>
  <div class="count" id="cnt"></div>
  <div class="scroll">
    <table id="tbl">
      <thead><tr id="head"></tr></thead>
      <tbody id="body"></tbody>
    </table>
  </div>
</main>
<script>
const SCHEMA=__SCHEMA__;
const SOURCES=__SOURCES__;
const ALLDATA=__DATA__;
let DATA=[],SRC="ALL",TYPE="ALL",OPENONLY=true,SORT={c:null,d:1},PAGE=1,PAGESIZE=50;
const $=id=>document.getElementById(id);
function init(){
  const sel=$("source");
  for(const s of SOURCES){const o=document.createElement("option");o.value=s.source;o.textContent=`${s.source} (${s.count})`;sel.appendChild(o);}
  $("head").innerHTML=COLS.map(([k,l],i)=>`<th onclick="sort('${k}',${i})">${l} <span id="ar${i}"></span></th>`).join("");
  sel.onchange=()=>{SRC=sel.value;PAGE=1;load();};
  $("q").oninput=()=>{PAGE=1;render();};
  $("openonly").onchange=()=>{OPENONLY=$("openonly").checked;PAGE=1;load();};
  load();
}
function load(){setStatus("Loading...");DATA=(SRC==="ALL"?ALLDATA:ALLDATA.filter(r=>r.source===SRC));PAGE=1;buildChips();render();setStatus("");}
function setStatus(s){$("status").textContent=s;}
function buildChips(){
  const types=["ALL",...Array.from(new Set(DATA.map(r=>r.lead_type))).sort()];
  const ch=$("chips");ch.innerHTML="";
  types.forEach(t=>{const c=document.createElement("span");c.className="chip"+(t===TYPE?" on":"");c.textContent=t;c.onclick=()=>{TYPE=t;PAGE=1;[...ch.children].forEach(x=>x.classList.remove("on"));c.classList.add("on");render();};ch.appendChild(c);});
}
function colsFor(type){
  if(TYPE!=="ALL" && SCHEMA[TYPE]) return SCHEMA[TYPE];
  return ["property_address","name","amount","record_date","status","case_number"].map(k=>[k,k,"txt"]);
}
function fmtAmt(a){if(!a)return "";const n=parseFloat(String(a).replace(/[^0-9.]/g,""));if(isNaN(n))return a;return "$"+n.toLocaleString(undefined,{maximumFractionDigits:0});}
function statusBadge(s){
  if(!s)return "";
  const t=s.toUpperCase();
  let cls="st-other";
  if(t.startsWith("OPEN"))cls="st-open";
  else if(t.startsWith("CLOSED"))cls="st-closed";
  else if(t.includes("SALE"))cls="st-sale";
  else if(t.includes("REDEEM"))cls="st-closed";
  else if(t.includes("VACANT"))cls="st-open";
  return `<span class="badge ${cls}">${s}</span>`;
}
function isClosed(s){const t=(s||"").toUpperCase();return t.includes("CLOSED")||t.includes("REDEEM");}
function filtered(){
  let rows=DATA;
  if(TYPE!=="ALL")rows=rows.filter(r=>r.lead_type===TYPE);
  if(OPENONLY)rows=rows.filter(r=>!isClosed(r.status));
  const q=$("q").value.trim().toLowerCase();
  if(q){rows=rows.filter(r=>Object.values(r).join(" ").toLowerCase().includes(q));}
  if(SORT.c){rows=[...rows].sort((a,b)=>{const x=(a.fields&&a.fields[SORT.c])||"",y=(b.fields&&b.fields[SORT.c])||"";return (x<y?-1:x>y?1:0)*SORT.d;});}
  return rows;
}
function renderHead(){
  const cols=colsFor(TYPE);
  $("head").innerHTML=`<th></th><th>Type</th>`+cols.map(([k,l],i)=>`<th onclick="sort('${k}',${i})">${l} <span id="ar${i}"></span></th>`).join("");
}
function render(){
  renderHead();
  const rows=filtered();
  const by={};rows.forEach(r=>by[r.lead_type]=(by[r.lead_type]||0)+1);
  let amt=0;rows.forEach(r=>{const v=parseFloat((r.amount||"").replace(/[^0-9.]/g,""));if(v)amt+=v;});
  $("cards").innerHTML=`<div class="card"><b>${rows.length}</b><span>leads shown</span></div>`+
    `<div class="card"><b class="amt">$${amt.toLocaleString(undefined,{maximumFractionDigits:0})}</b><span>total $ (where known)</span></div>`+
    Object.keys(by).map(t=>`<div class="card"><b>${by[t]}</b><span>${t}</span></div>`).join("");
  $("cnt").textContent=`${rows.length} leads`+(OPENONLY?" (open only)":"")+` — page ${PAGE} of ${Math.max(1,Math.ceil(rows.length/PAGESIZE))}`;
  const start=(PAGE-1)*PAGESIZE;
  const pageRows=rows.slice(start,start+PAGESIZE);
  const cols=colsFor(TYPE);
  $("body").innerHTML=pageRows.map(r=>{
    const tag=`<span class="tag s-${r.source}">${r.source}</span>`;
    const cells=cols.map(([k,label,kind])=>{
      const v=(r.fields&&r.fields[k])||"";
      if(kind==="amt") return `<td>${v?`<span class="amt">${fmtAmt(v)}</span>`:""}</td>`;
      if(kind==="status") return `<td>${statusBadge(v)}</td>`;
      if(kind==="link") return `<td>${v?`<a href="${v}" target="_blank">open</a>`:""}</td>`;
      const cls=kind==="addr"?"prop":(kind==="txt"?"wrap":"");
      return `<td class="${cls}">${v||""}</td>`;
    }).join("");
    return `<tr><td>${tag}</td><td>${r.lead_type}</td>${cells}</tr>`;
  }).join("")||`<tr><td colspan="${cols.length+2}" style="color:var(--mut);padding:20px">No leads match.</td></tr>`;
  let pager=$("pager");
  if(!pager){pager=document.createElement("div");pager.id="pager";pager.className="bar";pager.style.marginTop="10px";document.querySelector("main").appendChild(pager);}
  let pg=` <span class="count"> ${PAGE} / ${Math.max(1,Math.ceil(rows.length/PAGESIZE))} </span>`;
  if(PAGE>1)pg=`<button class="btn" onclick="PAGE--;render()">‹ Prev</button> `+pg;
  if(start+PAGESIZE<rows.length)pg+=`<button class="btn" onclick="PAGE++;render()">Next ›</button>`;
  pager.innerHTML=pg;
}
function sort(c,i){if(SORT.c===c)SORT.d*=-1;else{SORT.c=c;SORT.d=1;}[...$("head").querySelectorAll(".ar")].forEach(x=>x.textContent="");if($("ar"+i))$("ar"+i).textContent=SORT.d>0?"▲":"▼";render();}
function dl(){
  const rows=(SRC==="ALL"?ALLDATA:DATA);
  const cols=colsFor(TYPE);
  const flat=["source","lead_type",...cols.map(c=>c[0])];
  const csv=[flat.join(",")].concat(rows.map(r=>flat.map(c=>{
    if(c==="source")return `"${r.source}"`;
    if(c==="lead_type")return `"${r.lead_type}"`;
    const v=(r.fields&&r.fields[c])||"";return `"${String(v).replace(/"/g,'""')}"`;
  }).join(","))).join("\n");
  const blob=new Blob([csv],{type:"text/csv"});const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);a.download="duval_leads_"+(SRC==="ALL"?"all":SRC)+".csv";a.click();
}
init();
</script>
</body>
</html>"""


def main():
    log.info("collecting leads from CSV sources...")
    rows, files = collect()
    # group source counts (mirror /api/sources)
    src_counts = {}
    for f in files:
        src_counts[f["source"]] = src_counts.get(f["source"], 0) + f["count"]
    sources = [{"source": s, "count": c} for s, c in sorted(src_counts.items())]
    # trim to the columns the table renders (keeps payload small)
    data = [{k: r.get(k, "") for k in KEEP} for r in rows]
    log.info("total leads=%d across %d sources", len(rows), len(sources))

    html = (TEMPLATE
            .replace("__SOURCES__", json.dumps(sources))
            .replace("__SCHEMA__", json.dumps(SCHEMA, ensure_ascii=False))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False)))
    out = OUT / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info("wrote %s (%d bytes)", out, out.stat().st_size)


if __name__ == "__main__":
    main()
