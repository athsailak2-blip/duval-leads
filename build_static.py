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
import json, logging, hashlib, datetime
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
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="description" content="Duval County, FL distressed-property leads: foreclosures, tax deeds, tax-delinquent, liens, probate, divorce & vacant properties. Sourced from official county records.">
<meta name="theme-color" content="#0b0f15">
<title>Duval County Distressed Leads</title>
<style>
  :root{
    --bg:#0b0f15;--card:#141a23;--ink:#e8eef6;--mut:#93a1b3;--acc:#4f8cff;
    --grn:#37d67a;--red:#ff6b6b;--amber:#f5b94d;--cyan:#46d3e0;--violet:#b98cff;
    --line:#222c3a;--chip:#19222e;--hover:#1e2937;--soft:#0f1620;
  }
  *{box-sizing:border-box}
  html,body{margin:0}
  body{font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
       background:var(--bg);color:var(--ink);-webkit-text-size-adjust:100%}
  a{color:var(--acc);text-decoration:none}
  /* ---------- Header / brand ---------- */
  header{background:linear-gradient(180deg,#0e141d,#0b0f15);
         border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20;
         box-shadow:0 6px 24px rgba(0,0,0,.45)}
  .htop{max-width:1180px;margin:0 auto;padding:16px 20px 0;display:flex;align-items:center;gap:13px}
  .logo{width:42px;height:42px;border-radius:11px;flex:none;
        background:linear-gradient(140deg,#4f8cff,#37d67a);display:grid;place-items:center;
        font-size:22px;box-shadow:0 4px 14px rgba(79,140,255,.35)}
  .brand h1{font-size:20px;margin:0;font-weight:800;letter-spacing:.2px;line-height:1.1;overflow-wrap:anywhere}
  .brand h1 .pin{color:var(--acc)}
  .brand{flex:1;min-width:0}
  .brand .tag{color:var(--mut);font-size:12.5px;margin-top:2px;white-space:normal}
  .hstat{margin-left:auto;text-align:right;color:var(--mut);font-size:12px;line-height:1.35}
  .hstat b{color:var(--ink)}
  .trust{max-width:1180px;margin:0 auto;padding:8px 20px 14px;display:flex;flex-wrap:wrap;
        gap:8px 16px;align-items:center;color:var(--mut);font-size:12px}
  .trust .t{display:flex;align-items:center;gap:6px}
  .trust .dot{width:7px;height:7px;border-radius:50%;background:var(--grn);box-shadow:0 0 8px var(--grn)}
  /* ---------- Controls ---------- */
  .bar{max-width:1180px;margin:0 auto;padding:0 20px 14px;display:flex;flex-wrap:wrap;gap:9px;align-items:center}
  select,input,button{background:var(--soft);color:var(--ink);border:1px solid var(--line);
         border-radius:9px;padding:10px 12px;font-size:14px;outline:none;transition:.15s}
  select:focus,input:focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(79,140,255,.18)}
  input[type=search]{flex:1;min-width:200px}
  label.chk{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:13.5px;
         cursor:pointer;user-select:none;padding:9px 13px;border:1px solid var(--line);
         border-radius:9px;background:var(--soft)}
  label.chk input{accent-color:var(--acc);width:17px;height:17px;margin:0}
  .btn{background:var(--acc);color:#fff;border:none;cursor:pointer;font-weight:700;
       padding:10px 15px;border-radius:9px;box-shadow:0 3px 12px rgba(79,140,255,.3)}
  .btn:hover{background:#3f78ec}
  .btn:active{transform:translateY(1px)}
  .chips{max-width:1180px;margin:0 auto;padding:0 20px 6px;display:flex;flex-wrap:wrap;gap:7px}
  .chip{background:var(--chip);border:1px solid var(--line);color:var(--mut);
        padding:6px 13px;border-radius:20px;cursor:pointer;user-select:none;font-size:12.5px;transition:.15s}
  .chip:hover{background:var(--hover);color:var(--ink)}
  .chip.on{background:var(--acc);color:#fff;border-color:var(--acc);font-weight:700;box-shadow:0 2px 10px rgba(79,140,255,.4)}
  /* ---------- KPI cards ---------- */
  main{max-width:1180px;margin:0 auto;padding:18px 20px 60px}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:14px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:15px 17px;
        box-shadow:0 2px 10px rgba(0,0,0,.3);position:relative;overflow:hidden}
  .card:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--acc);opacity:.7}
  .card.b-amt:before{background:var(--grn)} .card.b-open:before{background:var(--cyan)}
  .card.b-type:before{background:var(--violet)}
  .card b{display:block;font-size:25px;line-height:1.05;font-weight:800}
  .card .amt{color:var(--grn)} .card .open{color:var(--cyan)}
  .card span{color:var(--mut);font-size:12px;display:block;margin-top:3px}
  .count{color:var(--mut);margin:2px 0 12px;font-size:13.5px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
  .count b{color:var(--ink)}
  /* ---------- Table ---------- */
  .scroll{overflow:auto;border:1px solid var(--line);border-radius:14px;max-height:76vh;
          box-shadow:inset 0 0 24px rgba(0,0,0,.25);background:var(--soft)}
  table{width:100%;border-collapse:collapse;font-size:13.5px}
  th,td{padding:11px 13px;text-align:left;border-bottom:1px solid #1a2230;vertical-align:top}
  th{position:sticky;top:0;background:#10161f;cursor:pointer;white-space:nowrap;
     color:var(--mut);font-weight:700;font-size:12px;letter-spacing:.4px;z-index:2}
  th:hover{color:var(--ink)} th .ar{color:var(--acc)}
  tbody tr:hover td{background:#121a25}
  .tag{display:inline-block;padding:3px 9px;border-radius:9px;font-size:10.5px;font-weight:800;white-space:nowrap;letter-spacing:.3px}
  .s-OfficialRecords{background:#06222b;color:#5fd0e0}
  .s-Foreclosure{background:#2e0a1c;color:#f29bc0}
  .s-TaxDeed{background:#2e2608;color:#ecc066}
  .s-VacantResidential{background:#240a2e;color:#c79bf0}
  .s-TaxDelinquent{background:#06291f;color:#5fe0a8}
  .s-Eviction{background:#2e2508;color:#e9d27f}
  .s-CoreCivil{background:#061a2e;color:#6fb0f0}
  .badge{display:inline-block;padding:3px 10px;border-radius:10px;font-size:11px;font-weight:800;letter-spacing:.2px}
  .st-open{background:#06291f;color:#5fe0a8} .st-closed{background:#1c232c;color:#8b98a5}
  .st-sale{background:#06222b;color:#5fd0e0} .st-other{background:#1c232c;color:#8b98a5}
  .amt{color:var(--grn);font-weight:800} .prop{font-weight:700;color:#fff}
  .wrap{color:var(--mut);max-width:300px}
  /* ---------- Mobile card list ---------- */
  .mlist{display:none}
  .mcard{background:var(--card);border:1px solid var(--line);border-radius:13px;padding:13px 15px;margin-bottom:10px;box-shadow:0 2px 8px rgba(0,0,0,.28)}
  .mcard .mtop{display:flex;align-items:center;gap:8px;margin-bottom:9px}
  .mcard .mtype{font-weight:800;font-size:14px}
  .mcard .mrow{display:flex;gap:8px;padding:3px 0;font-size:13px;border-top:1px solid #1a2230}
  .mcard .mrow:first-of-type{border-top:none}
  .mcard .mlabel{color:var(--mut);min-width:84px;flex:none}
  .mcard .mval{color:var(--ink);font-weight:600;word-break:break-word}
  .pager{max-width:1180px;margin:14px auto 0;display:flex;gap:8px;align-items:center;justify-content:center;flex-wrap:wrap}
  .pager .count{margin:0}
  /* loading */
  #loading{padding:60px 20px;text-align:center;color:var(--mut)}
  .spin{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--acc);
        border-radius:50%;animation:sp 1s linear infinite;margin:0 auto 14px}
  @keyframes sp{to{transform:rotate(360deg)}}
  /* ---------- Mobile ---------- */
  @media(max-width:720px){
    .htop{padding:13px 14px 0}
    .brand h1{font-size:17px} .logo{width:38px;height:38px;font-size:20px}
    .hstat{display:none}
    .trust{padding:8px 14px 12px;font-size:11px;gap:6px 12px}
    .bar{padding:0 14px 12px}
    input[type=search]{flex:1 1 100%;min-width:0;width:100%}
    .bar > select{flex:1 1 100%;width:100%}
    .bar > label.chk{flex:1 1 100%;justify-content:flex-start}
    .bar > .btn{flex:1 1 100%;width:100%}
    .chips{padding:0 14px 4px}
    main{padding:14px 14px 50px}
    .cards{grid-template-columns:repeat(2,1fr);gap:9px}
    .card{padding:12px 13px} .card b{font-size:21px}
    .scroll{display:none}            /* hide wide table on phones */
    .mlist{display:block}            /* show card list instead */
    .count{font-size:12.5px}
  }
  @media(max-width:400px){
    .cards{grid-template-columns:1fr 1fr}
    .brand h1{font-size:15.5px}
  }
</style>
</head>
<body>
<header>
  <div class="htop">
    <div class="logo">&#127968;</div>
    <div class="brand">
      <h1>Duval County <span class="pin">Distressed Leads</span></h1>
      <div class="tag">Pre-foreclosure, tax deed, lien, probate, divorce &amp; vacant-property leads</div>
    </div>
    <div class="hstat">
      Updated <b id="upd">&#8212;</b><br>
      <span id="srccount">&#8212; sources</span>
    </div>
  </div>
  <div class="trust">
    <span class="t"><span class="dot"></span> Live from official county records</span>
    <span class="t">&#128274; Sourced from Clerk of Court &middot; Property Appraiser &middot; Tax Collector</span>
    <span class="t">&#10003; Addresses verified where available</span>
  </div>
  <div class="bar">
    <select id="source"><option value="ALL">All lead types</option></select>
    <input id="q" type="search" placeholder="Search address, owner, case #, parcel...">
    <label class="chk"><input type="checkbox" id="openonly" checked> Open leads only</label>
    <button class="btn" onclick="dl()">&#11015; Download CSV</button>
  </div>
  <div class="chips" id="chips"></div>
</header>
<main>
  <div id="loading"><div class="spin"></div>Loading leads&#8230;</div>
  <div class="cards" id="cards" style="display:none"></div>
  <div class="count" id="cnt" style="display:none"></div>
  <div class="scroll" id="tblwrap" style="display:none">
    <table id="tbl"><thead><tr id="head"></tr></thead><tbody id="body"></tbody></table>
  </div>
  <div class="mlist" id="mlist"></div>
  <div class="pager" id="pager"></div>
</main>
<script>
const BUILD_ID="__BUILD__";
let SCHEMA={},SOURCES=[],ALLDATA=[];
const COLS=[["source","Source"],["lead_type","Type"],["property_address","Property"],
  ["name","Parties"],["amount","Amount"],["record_date","Filed"],
  ["status","Status"],["case_number","Case # / Cert"]];
let DATA=[],SRC="ALL",TYPE="ALL",OPENONLY=true,SORT={c:null,d:1},PAGE=1,PAGESIZE=50;
const $=id=>document.getElementById(id);
function init(){
  loadData();
}
function loadData(){
  const url="leads.json?b="+BUILD_ID;
  fetch(url).then(r=>{if(!r.ok)throw new Error(r.status);return r.json();}).then(j=>{
    SCHEMA=j.schema||{};SOURCES=j.sources||[];ALLDATA=j.data||[];
    const sel=$("source");
    for(const s of SOURCES){const o=document.createElement("option");o.value=s.source;o.textContent=s.source+" ("+s.count+")";sel.appendChild(o);}
    sel.onchange=()=>{SRC=sel.value;PAGE=1;load();};
    $("q").oninput=()=>{PAGE=1;render();};
    $("openonly").onchange=()=>{OPENONLY=$("openonly").checked;PAGE=1;load();};
    $("upd").textContent=new Date().toLocaleDateString(undefined,{month:"short",day:"numeric",year:"numeric"});
    $("srccount").textContent=SOURCES.length+" sources";
    load();
  }).catch(e=>{
    $("loading").innerHTML="<div style='color:var(--red);padding:20px'>Failed to load leads data ("+e.message+").<br>Check your connection and refresh.</div>";
  });
}
function load(){setLoading();DATA=(SRC==="ALL"?ALLDATA:ALLDATA.filter(r=>r.source===SRC));PAGE=1;buildChips();render();}
function setLoading(){$("loading").style.display="block";$("cards").style.display="none";$("cnt").style.display="none";$("tblwrap").style.display="none";}
function buildChips(){
  const types=["ALL",...Array.from(new Set(DATA.map(r=>r.lead_type))).sort()];
  const ch=$("chips");ch.innerHTML="";
  types.forEach(t=>{const c=document.createElement("span");c.className="chip"+(t===TYPE?" on":"");c.textContent=t;c.onclick=()=>{TYPE=t;PAGE=1;[...ch.children].forEach(x=>x.classList.remove("on"));c.classList.add("on");render();};ch.appendChild(c);});
}
function colsFor(type){return (TYPE!=="ALL"&&SCHEMA[TYPE])?SCHEMA[TYPE]:COLS;}
function fmtAmt(a){if(!a)return "";const n=parseFloat(String(a).replace(/[^0-9.]/g,""));if(isNaN(n))return a;return "$"+n.toLocaleString(undefined,{maximumFractionDigits:0});}
function statusBadge(s){
  if(!s)return "";const t=s.toUpperCase();let cls="st-other";
  if(t.startsWith("OPEN"))cls="st-open";else if(t.startsWith("CLOSED")||t.includes("REDEEM"))cls="st-closed";
  else if(t.includes("SALE"))cls="st-sale";
  return '<span class="badge '+cls+'">'+s+'</span>';
}
function isClosed(s){const t=(s||"").toUpperCase();return t.includes("CLOSED")||t.includes("REDEEM");}
function filtered(){
  let rows=DATA;
  if(TYPE!=="ALL")rows=rows.filter(r=>r.lead_type===TYPE);
  if(OPENONLY)rows=rows.filter(r=>!isClosed(r.status));
  const q=$("q").value.trim().toLowerCase();
  if(q)rows=rows.filter(r=>Object.values(r).join(" ").toLowerCase().includes(q));
  if(SORT.c)rows=[...rows].sort((a,b)=>{const x=(a.fields&&a.fields[SORT.c])||"",y=(b.fields&&b.fields[SORT.c])||"";return (x<y?-1:x>y?1:0)*SORT.d;});
  return rows;
}
function renderHead(){
  const cols=colsFor(TYPE);
  $("head").innerHTML='<th></th><th>Type</th>'+cols.map(([k,l],i)=>'<th onclick="sort(\''+k+'\','+i+')">'+l+' <span class="ar" id="ar'+i+'"></span></th>').join("");
}
function render(){
  renderHead();
  const rows=filtered();
  const by={};rows.forEach(r=>by[r.lead_type]=(by[r.lead_type]||0)+1);
  let amt=0,open=0;rows.forEach(r=>{const v=parseFloat((r.amount||"").replace(/[^0-9.]/g,""));if(v)amt+=v;if(!isClosed(r.status))open++;});
  $("cards").innerHTML=
    '<div class="card"><b>'+rows.length.toLocaleString()+'</b><span>leads shown</span></div>'+
    '<div class="card b-amt"><b class="amt">$'+amt.toLocaleString(undefined,{maximumFractionDigits:0})+'</b><span>distressed $ (where known)</span></div>'+
    '<div class="card b-open"><b class="open">'+open.toLocaleString()+'</b><span>open / actionable</span></div>'+
    '<div class="card b-type"><b>'+Object.keys(by).length+'</b><span>lead categories</span></div>';
  $("cards").style.display="grid";$("cnt").style.display="flex";$("tblwrap").style.display="";$("loading").style.display="none";
  $("cnt").innerHTML='<span><b>'+rows.length.toLocaleString()+'</b> leads'+(OPENONLY?" &middot; open only":"")+' &middot; '+Object.keys(by).length+' types</span><span>page '+PAGE+' / '+Math.max(1,Math.ceil(rows.length/PAGESIZE))+'</span>';
  const start=(PAGE-1)*PAGESIZE,pageRows=rows.slice(start,start+PAGESIZE),cols=colsFor(TYPE);
  $("body").innerHTML=pageRows.map(r=>{
    const tag='<span class="tag s-'+r.source+'">'+r.source+'</span>';
    const cells=cols.map(([k,label,kind])=>{
      const v=(r.fields&&r.fields[k])||"";
      if(kind==="amt")return '<td>'+(v?'<span class="amt">'+fmtAmt(v)+'</span>':"")+'</td>';
      if(kind==="status")return '<td>'+statusBadge(v)+'</td>';
      if(kind==="link")return '<td>'+(v?'<a href="'+v+'" target="_blank">open</a>':"")+'</td>';
      const cls=kind==="addr"?"prop":(kind==="txt"?"wrap":"");return '<td class="'+cls+'">'+(v||"")+'</td>';
    }).join("");
    return '<tr><td>'+tag+'</td><td>'+r.lead_type+'</td>'+cells+'</tr>';
  }).join("")||'<tr><td colspan="'+(cols.length+2)+'" style="color:var(--mut);padding:22px;text-align:center">No leads match your filters.</td></tr>';
  $("mlist").innerHTML=pageRows.map(r=>{
    const cols2=colsFor(TYPE);
    const rows2=cols2.map(([k,label,kind])=>{
      const v=(r.fields&&r.fields[k])||"";let val=v;
      if(kind==="amt")val=v?fmtAmt(v):"";if(kind==="status")val=statusBadge(v);
      if(!val)return "";
      return '<div class="mrow"><span class="mlabel">'+label+'</span><span class="mval">'+val+'</span></div>';
    }).filter(Boolean).join("");
    return '<div class="mcard"><div class="mtop"><span class="tag s-'+r.source+'">'+r.source+'</span><span class="mtype">'+r.lead_type+'</span></div>'+rows2+'</div>';
  }).join("");
  renderPager(rows.length,start);
}
function renderPager(total,start){
  let pg='<span class="count"> '+PAGE+' / '+Math.max(1,Math.ceil(total/PAGESIZE))+' </span>';
  if(PAGE>1)pg='<button class="btn" onclick="PAGE--;render()">&#8249; Prev</button>'+pg;
  if(start+PAGESIZE<total)pg+='<button class="btn" onclick="PAGE++;render()">Next &#8250;</button>';
  $("pager").innerHTML=pg;
}
function sort(c,i){if(SORT.c===c)SORT.d*=-1;else{SORT.c=c;SORT.d=1;}[...$("head").querySelectorAll(".ar")].forEach(x=>x.textContent="");if($("ar"+i))$("ar"+i).textContent=SORT.d>0?"&#9650;":"&#9660;";render();}
function dl(){
  const rows=(SRC==="ALL"?ALLDATA:DATA);const cols=colsFor(TYPE);
  const flat=["source","lead_type",...cols.map(c=>c[0])];
  const csv=[flat.join(",")].concat(rows.map(r=>flat.map(c=>{
    if(c==="source")return '"'+r.source+'"';if(c==="lead_type")return '"'+r.lead_type+'"';
    const v=(r.fields&&r.fields[c])||"";return '"'+String(v).replace(/"/g,'""')+'"';
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

    # Build id: short hash of the data so the browser refetches leads.json
    # only when content actually changed (kills stale-cache problems).
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    build_id = hashlib.sha1(payload).hexdigest()[:10]

    # Write the heavy data as a SEPARATE file (loaded via fetch with cache-bust).
    # This keeps index.html a tiny shell that loads instantly.
    leads = {"schema": SCHEMA, "sources": sources, "data": data}
    (OUT / "leads.json").write_text(
        json.dumps(leads, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    log.info("wrote %s (%d bytes)", OUT / "leads.json",
             (OUT / "leads.json").stat().st_size)

    html = TEMPLATE.replace("__BUILD__", build_id)
    out = OUT / "index.html"
    out.write_text(html, encoding="utf-8")
    log.info("wrote %s (%d bytes, build=%s)", out, out.stat().st_size, build_id)


if __name__ == "__main__":
    main()
