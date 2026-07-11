#!/usr/bin/env python3
"""
Duval County (FL) CORE CIVIL distressed-lead scraper  --  PRODUCTION.

Source: Duval County Clerk of Court (CORE)  --  County Civil + Circuit Civil
case-type search. We search by COURT TYPE + CASE TYPE + filing date range,
then for each case pull the parties (plaintiff / defendant) and property
street address from the case detail (via the proven scraper.core_lookup path,
which waits for the ParsedCaseNumberLabel so it never reads a stale tab).

This covers case types the OfficialRecords pipeline + realforeclose do NOT:
  * Foreclosure (304-307), Commercial Foreclosure (488-490),
    Homestead/Non-Homestead Residential Foreclosure (491-496),
    Mortgage/Lien Foreclosure (691-692, 714)
  * Mechanic's Lien (362,363,561), Construction Lien (361,558)
  * Quiet Title (295-297,529), Condominium (309,404,405)
  * Probate (406,638) + Opening An Estate + Summary Admin (cross-listed)
Plus Eviction (357-712) which the eviction_scraper already handles -- kept
here too so one run can cover everything.

Reuses:
  * ref_lp/scraper.py  : launch, warm_core, core_lookup  (CORE login + parties)
  * ref_taxdeed        : papa_owner() fallback (by parcel, when present)

Output: data/core_civil.csv + data/core_civil.json
  columns: case_number, filing_date, court_type, case_type, case_type_code,
           plaintiff, defendant, property_address, parcel_id, owner_appraiser,
           status, source
"""
import argparse, csv, json, re, sys, time, logging, os
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("duval")

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "ref_lp"))
sys.path.insert(0, str(HERE.parent / "ref_taxdeed"))

import scraper as LP
from scraper import launch, warm_core, core_lookup, BASE_URL

try:
    from taxdeed_scraper import papa_owner
except Exception:
    def papa_owner(pid):
        return ""

if not os.environ.get("CORE_EMAIL"):
    envp = HERE.parent / ".env"
    if envp.exists():
        for line in envp.read_text().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

DATA_DIR = HERE.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# (label, court_type, case_type_code, case_keyword)
# case_keyword = substring used to match the option TEXT (CORE arms its
# cascading handler only when the selection matches the option text, the way
# a human click would). case_type_code kept for reference/record.
CASE_TYPES = [
    ("CommercialForeclosure",       "Circuit Civil", "488,489,490",            "Commercial Foreclosure"),
    ("HomesteadResForeclosure",     "Circuit Civil", "491,492,493",            "Homestead Residential Foreclosure"),
    ("NonHomesteadResForeclosure",  "Circuit Civil", "494,495,496",            "Non-Homestead Residential Foreclosure"),
    ("MortgageLienForeclosure",     "County Civil",  "691,692,714",            "Mortgage/Lien Foreclosure"),
    ("MechanicsLien",               "County Civil",  "362,363,561",            "Mechanic"),
    ("ConstructionLien",            "County Civil",  "361,558",                "Construction Lien"),
    ("QuietTitle",                  "Circuit Civil", "295,296,297,529",        "Quiet Title"),
    ("Condominium",                 "Circuit Civil", "309,404,405",            "Condominium"),
    ("Probate",                     "Probate",       "406,638",                "Probate"),
    ("Eviction",                    "County Civil",  "357,358,359,360,409,685,686,687,694,695,696,697,698,699,711,712", "Eviction"),
    ("Ejectment",                   "Circuit Civil", "301,302,303,524",        "Ejectment"),
    ("OpeningEstate",               "Probate",       "391",                    "Opening An Estate"),
    ("SummaryAdminHigh",            "Probate",       "392",                    "Summary Admin-Estates Valued At $1,000"),
    ("SummaryAdminLow",             "Probate",       "393",                    "Summary Admin-Estates Valued At Less Than"),
    ("OtherRealPropLow",            "Circuit Civil", "497",                    "Other Real Property Actions $0"),
    ("OtherRealPropHigh",           "Circuit Civil", "498",                    "Other Real Property Actions $250,000"),
    ("OtherRealPropMid",            "Circuit Civil", "499",                    "Other Real Property Actions $50,001"),
    ("Dissolution",                 "Family",        "601,602,603,604,639",     "Dissolution of Marriage"),
]


def run_search(page, court_type, case_type_code, year, case_keyword):
    """Open Case Search tab, set Court Type + Case Type + Year, Begin Search.

    Mirrors the PROVEN eviction_scraper.run_search: selects dropdowns by
    matching the OPTION TEXT (not the option value), because CORE's cascading
    change-handler only arms when the selection goes through the text-matched
    option the way a human click would.
    """
    # open a fresh in-page tab via '+' (last igtab_THTab span)
    tabs = page.locator("span.igtab_THTab")
    n = tabs.count()
    if n:
        tabs.nth(n - 1).click(timeout=8000)
        page.wait_for_timeout(2500)
    page.evaluate("""() => {
        const e=[...document.querySelectorAll('a,span,td,button')].find(x=>(x.innerText||'').trim().toLowerCase().includes('search by criteria') || (x.innerText||'').trim().toLowerCase().includes('case search'));
        if(e) e.click();
    }""")
    page.wait_for_timeout(3000)
    # Court Type -- match option text (proven)
    page.evaluate("""(ct) => {
        const s=[...document.querySelectorAll('select')].find(s=>/CourtTypeDropDownList/.test(s.id));
        if(s){ const o=[...s.options].find(o=>o.text.trim().toLowerCase()===ct.toLowerCase()); if(o){ s.value=o.value; s.dispatchEvent(new Event('change',{bubbles:true})); } }
    }""", court_type)
    page.wait_for_timeout(2000)
    # Case Type -- match option TEXT containing keyword (proven path)
    page.evaluate("""(kw) => {
        const s=[...document.querySelectorAll('select')].find(s=>/CaseTypeDropDownList/.test(s.id));
        if(!s) return;
        const o=[...s.options].find(o=>o.text.trim().toLowerCase().includes(kw.toLowerCase()) && o.value!=='NoSelection');
        if(o){ s.value=o.value; s.dispatchEvent(new Event('change',{bubbles:true})); }
    }""", case_keyword)
    page.wait_for_timeout(2000)
    # Case Year
    page.evaluate("""(yr) => {
        const s=[...document.querySelectorAll('select,input')].find(s=>/CaseYear/i.test(s.id));
        if(!s) return;
        if(s.tagName==='SELECT'){ const o=[...s.options].find(o=>o.value===yr||o.text.trim()===yr); if(o){s.value=yr; s.dispatchEvent(new Event('change',{bubbles:true}));} }
        else { const set=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set; set.call(s,yr); s.dispatchEvent(new Event('input',{bubbles:true})); s.dispatchEvent(new Event('change',{bubbles:true})); }
    }""", year)
    page.wait_for_timeout(1500)
    # click 'Begin Search'
    page.evaluate("""() => {
        const el=[...document.querySelectorAll('input,button,a,span')].find(e=>(e.value||e.innerText||'').trim().toLowerCase()==='begin search' || /begin search/i.test(e.id||''));
        if(el) el.click();
    }""")
    page.wait_for_timeout(6000)
    return None


def parse_results(page):
    return page.evaluate("""() => {
        const out=[];
        const trs=[...document.querySelectorAll('table tr')];
        for(const tr of trs){
            const c=[...tr.querySelectorAll('td,th')].map(x=>x.innerText.replace(/\\s+/g,' ').trim());
            if(c.length>=3 && /\\d{2,4}-(?:CA|CC|EV|DR|FA|CP|CF|MH|MI|CO|IN|MM|MO|MS|XX|AP|SC|AO|AS|FR|OI|DP|JT|GP|GA|1D)-/i.test(c[0]||'')){
                out.push({case:c[0], date:c[1]||'', party:c[2]||'', extra:c.slice(3).join(' | ')});
            }
        }
        return out;
    }""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default="2026")
    ap.add_argument("--types", default="", help="comma list of CASE_TYPES labels to run (default: all)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-papa", action="store_true")
    args = ap.parse_args()

    types = [t for t in CASE_TYPES if (not args.types or t[0] in args.types.split(","))]
    pw, b, page = launch()
    warm_core(page, login=True)
    time.sleep(2)

    cols = ["case_number", "filing_date", "court_type", "case_type", "case_type_code",
            "plaintiff", "defendant", "property_address", "parcel_id",
            "owner_appraiser", "status", "source"]
    out_csv = DATA_DIR / "core_civil.csv"

    # idempotent append: remember already-written case_numbers so a mid-run
    # browser restart (anti tab-overflow) or a re-run never duplicates rows.
    done = set()
    if out_csv.exists():
        try:
            done = {r["case_number"] for r in csv.DictReader(out_csv.open())}
        except Exception:
            done = set()

    def flush(rows):
        new = [r for r in rows if r.get("case_number") and r["case_number"] not in done]
        if not new:
            return 0
        is_new = not out_csv.exists()
        with out_csv.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            if is_new:
                w.writeheader()
            for r in new:
                w.writerow(r)
        done.update(r["case_number"] for r in new)
        return len(new)

    for label, court_type, code, kw in types:
        log.info("=== CORE CIVIL: %s (%s / code %s) ===", label, court_type, code)
        run_search(page, court_type, code, args.year, kw)
        time.sleep(3)
        results = parse_results(page)
        log.info("  parsed %d results", len(results))
        if args.limit:
            results = results[:args.limit]
        batch = []
        for i, r in enumerate(results):
            rec = {
                "case_number": r.get("case", "").strip(),
                "filing_date": r.get("date", "").strip(),
                "court_type": court_type,
                "case_type": label,
                "case_type_code": code,
                "plaintiff": "", "defendant": "",
                "property_address": "",
                "parcel_id": "",
                "owner_appraiser": "",
                "status": r.get("extra", "").strip(),
                "source": "CORE-Duval-Civil",
            }
            parties = core_lookup(page, rec["case_number"])
            for p in parties:
                t = (p.get("type") or "").upper()
                if "PLAINTIFF" in t:
                    rec["plaintiff"] = (rec["plaintiff"] + " | " + p.get("name", "")).strip(" |")
                    if p.get("address") and not rec["property_address"]:
                        rec["property_address"] = p["address"]
                elif "DEFENDANT" in t:
                    rec["defendant"] = (rec["defendant"] + " | " + p.get("name", "")).strip(" |")
            batch.append(rec)
            # ANTI TAB-OVERFLOW: core_lookup opens a new CORE in-page tab per
            # case and never closes it. After ~10 cases the tab strip overflows
            # and clicks become unstable -> the whole run dies (-1). Restart the
            # browser every 10 cases. flush() is idempotent (dedupes by
            # case_number) so the re-warmed browser simply re-does already-written
            # cases harmlessly.
            if (i + 1) % 10 == 0 and (i + 1) < len(results):
                log.info("  browser reset after %d cases (anti tab-overflow)", i + 1)
                try:
                    b.close(); pw.stop()
                except Exception:
                    pass
                pw, b, page = launch()
                warm_core(page, login=True)
                time.sleep(2)
        # write this case type's results IMMEDIATELY (don't wait for the whole run)
        n = flush(batch)
        json.dump(batch, open(DATA_DIR / f"core_civil_{label}.json", "w"), indent=2)
        log.info("  flushed %d %s records -> %s", n, label, out_csv)
        # back off between case types
        time.sleep(2)

    # full JSON snapshot for reference
    all_rows = []
    if out_csv.exists():
        all_rows = list(csv.DictReader(out_csv.open()))
    json.dump(all_rows, open(DATA_DIR / "core_civil.json", "w"), indent=2)
    log.info("CORE CIVIL: %d total records -> %s", len(all_rows), out_csv)
    for r in all_rows[:6]:
        log.info("  %s | %s | P:%s | D:%s", r["case_number"], r["case_type"],
                 (r.get("plaintiff") or "-")[:25], (r.get("defendant") or "-")[:25])
    print(f"DONE records={len(all_rows)} csv={out_csv}")


if __name__ == "__main__":
    main()
