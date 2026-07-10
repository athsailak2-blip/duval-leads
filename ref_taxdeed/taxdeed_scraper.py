#!/usr/bin/env python3
"""
Duval County TAX DEED scraper (future-sale focused).

Collection uses stdlib urllib (no extra packages):
  - POST the search form with widened date-range bounds (01/01/2010..12/31/2030)
    and the target FUTURE Sale Date. The site's grid is server-state filtered
    and defaults its other bounds to end 07/09/2026, which would clip every
    future sale -- widening them is required.
  - GET Home/GridSearchData JSON (rows=1000, paginate) -> all records.
  - Filter client-side to future sale dates.

Detail + owner enrichment use Playwright (PAPA):
  - Home/Details?id=<rowid> for inside tables.
  - PAPA Detail.aspx?RE=<ParcelID> for owner-of-record.
Output: data/taxdeed_<tag>.csv (+ .json)
"""
import argparse, csv, json, logging, re, sys, time
from datetime import datetime, date
from pathlib import Path
from collections import Counter
from urllib.parse import urlencode
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("taxdeed")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from playwright.sync_api import sync_playwright

DATA_DIR = Path("/root/duval/data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
BASE = "https://taxdeed.duvalclerk.com/"
PAPA_DETAIL = "https://paopropertysearch.coj.net/Basic/Detail.aspx?RE={re}"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
WIDE_FROM, WIDE_TO = "01/01/2010", "12/31/2030"
TODAY = date(2026, 7, 10)
MON = {m: i+1 for i, m in enumerate(["January","February","March","April","May","June","July","August","September","October","November","December"])}

SEARCH_FORM = {
    "SearchForCertificate":"", "buttonSubmitCertificate":"",
    "SearchForCase":"", "SearchForParcelId":"", "SearchForTaxCollector":"",
    "SearchForApplicantName":"", "dateFromApplicantName":WIDE_FROM, "dateToApplicantName":WIDE_TO,
    "SearchForOwnerName":"", "dateFromOwnerName":WIDE_FROM, "dateToOwnerName":WIDE_TO,
    "SearchTypeStatus":"2", "dateFromStatus":WIDE_FROM, "dateToStatus":WIDE_TO,
    "SearchSaleDateFrom":None, "SearchSaleDateTo":None,  # filled per target
}

FUTURE_LABELS = ["Wednesday, August 12, 2026 9:00 AM",
                 "Wednesday, September 16, 2026 9:00 AM",
                 "Wednesday, October 14, 2026 9:00 AM"]


def parse_sale_date(s):
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
    if not m: return None
    try: return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except: return None


def collect_grid(date_label):
    """POST search for one future date, return list of (id, cell_list)."""
    form = dict(SEARCH_FORM)
    form["SearchSaleDateFrom"] = date_label
    form["SearchSaleDateTo"] = date_label
    cj = CookieJar()
    op = build_opener(HTTPCookieProcessor(cj))
    req = Request(BASE, data=urlencode(form).encode(),
                  headers={"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded", "Referer": BASE})
    op.open(req, timeout=60).read()
    time.sleep(0.4)
    rows = []
    for pg in range(1, 5):
        url = BASE + "Home/GridSearchData?SearchType=Certificate%20%23&_search=false&rows=1000&page=" + str(pg) + "&sidx=&sord=asc"
        r = Request(url, headers={"User-Agent": UA, "Referer": BASE, "X-Requested-With": "XMLHttpRequest"})
        js = json.loads(op.open(r, timeout=60).read())
        rs = js.get("rows", [])
        if not rs: break
        rows.extend(rs)
        if pg >= int(js.get("total", 1)): break
        time.sleep(0.3)
    return rows


def parse_cell(cell):
    def g(i): return (cell[i] if i < len(cell) else "").strip()
    owners = g(9)
    owner_list = [o.strip() for o in owners.split("~") if o.strip()] if owners else []
    return {
        "applicant": g(0), "case_number": g(1), "certificate": g(2).strip(),
        "parcel_id": g(3).strip(), "sale_date": g(4), "status": g(5),
        "amount_due": g(6), "bid_amount": g(7), "other_amount": g(8),
        "owners_grid": " | ".join(owner_list), "owner_count": len(owner_list),
    }


def fetch_detail(rid):
    """Return dict of label->value from Home/Details?id=<rid> using a fresh Playwright page."""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = b.new_context(user_agent=UA).new_page()
        pg.goto(BASE + "Home/Details?id=" + str(rid), wait_until="domcontentloaded")
        pg.wait_for_timeout(900)
        pairs = pg.evaluate("""() => {
            const out={};
            for(const tr of document.querySelectorAll('tr')){
                const tds=[...tr.querySelectorAll('td,th')];
                if(tds.length>=2){
                    const k=tds[0].innerText.replace(/\\s+/g,' ').trim();
                    const v=tds[1].innerText.replace(/\\s+/g,' ').trim();
                    if(k && v && !(k in out)) out[k]=v;
                }
            }
            return out;
        }""")
        b.close()
        return pairs


def papa_situs(parcel_id):
    """Pull the street (situs) address from PAPA parcel detail. Wholesalers
    need a real street address, not just a legal description. Returns
    '480 OSCAR RD JACKSONVILLE, FL 32234' or '' on miss/failure.
    """
    if not parcel_id or not re.search(r"\d", parcel_id):
        return ""
    re_id = parcel_id.replace("-", "").strip()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_context(user_agent=UA).new_page()
            pg.goto(PAPA_DETAIL.format(re=re_id), wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            addr = pg.evaluate("""() => {
                // The page shows 'Primary Site Address <street> <city> FL <zip>'
                const els=[...document.querySelectorAll('*')];
                for(const el of els){
                    const t=(el.innerText||'').replace(/\\s+/g,' ').trim();
                    const m=t.match(/Primary Site Address\\s+(\\d{3,5}\\s+[A-Z0-9 .]+?\\s+FL\\s*\\d{5}(?:-\\d+)?)/i);
                    if(m) return m[1].trim();
                }
                // fallback: any element whose text starts with a street number
                for(const el of els){
                    const t=(el.innerText||'').replace(/\\s+/g,' ').trim();
                    const m=t.match(/^(\\d{3,5}\\s+[A-Z0-9 .]+?\\s+FL\\s*\\d{5}(?:-\\d+)?)/i);
                    if(m) return m[1].trim();
                }
                return '';
            }""")
            b.close()
            return addr or ""
    except Exception as e:
        log.warning("PAPA situs %s failed: %s", parcel_id, e)
        return ""


def papa_owner(parcel_id):
    if not parcel_id or not re.search(r"\d", parcel_id):
        return ""
    re_id = parcel_id.replace("-", "").strip()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = b.new_context(user_agent=UA).new_page()
            pg.goto(PAPA_DETAIL.format(re=re_id), wait_until="domcontentloaded")
            pg.wait_for_timeout(2000)
            # Owner name sits just before 'Primary Site Address' on the page.
            res = pg.evaluate("""() => {
                const txt=(document.body.innerText||'').replace(/\\s+/g,' ');
                const m=txt.match(/Primary Site Address[\\s\\S]{0,120}?([A-Z][A-Z .'-]{2,40}?)\\s+\\d{3,5}\\s+[A-Z]/);
                // simpler: grab the line preceding 'Primary Site Address'
                const i=txt.indexOf('Primary Site Address');
                if(i>0){
                    const pre=txt.slice(0,i).trim();
                    const parts=pre.split(/\\s{2,}/);
                    const cand=parts[parts.length-1].trim();
                    if(/[A-Z]/.test(cand) && cand.length>=4) return cand;
                }
                return '';
            }""")
            b.close()
            return res or ""
    except Exception as e:
        log.warning("PAPA %s failed: %s", parcel_id, e)
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="one future label; empty=all known future dates")
    ap.add_argument("--no-detail", action="store_true")
    ap.add_argument("--no-papa", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    targets = [args.date] if args.date else FUTURE_LABELS
    all_recs = []
    seen_ids = set()
    for label in targets:
        rows = collect_grid(label)
        kept = [r for r in rows if (parse_sale_date(r["cell"][4]) or date(1900,1,1)) >= TODAY]
        log.info("%s: %d raw / %d future", label, len(rows), len(kept))
        for r in kept:
            if r["id"] in seen_ids:  # grid is not filtered by sale date; dedupe by row id
                continue
            seen_ids.add(r["id"])
            rec = parse_cell(r["cell"])
            rec["_id"] = r["id"]
            rec["sale_event_date"] = label
            all_recs.append(rec)

    if args.limit:
        all_recs = all_recs[:args.limit]

    if not args.no_detail:
        log.info("Fetching Home/Details for %d records...", len(all_recs))
        for i, r in enumerate(all_recs):
            try:
                det = fetch_detail(r["_id"])
                r["opening_bid"] = det.get("Opening Bid", "")
                r["final_bid"] = det.get("Final Bid", "")
                r["surplus"] = det.get("Surplus", "")
                r["legal_desc"] = det.get("Legal Description", "")
                r["detail"] = det
            except Exception as e:
                log.warning("detail %s: %s", r["_id"], e)
                r["detail"] = {}
            if (i+1) % 20 == 0:
                log.info("  detail %d/%d", i+1, len(all_recs))
            time.sleep(0.2)

    if not args.no_papa:
        log.info("PAPA enrichment...")
        for i, r in enumerate(all_recs):
            if r["parcel_id"]:
                r["situs"] = papa_situs(r["parcel_id"])
                r["owner_appraiser"] = papa_owner(r["parcel_id"])
                if (i+1) % 10 == 0:
                    log.info("  PAPA %d/%d", i+1, len(all_recs))
                time.sleep(0.3)

    tag = "future" if not args.date else re.sub(r"[^0-9]", "", args.date)
    out_csv = DATA_DIR / f"taxdeed_{tag}.csv"
    out_json = DATA_DIR / f"taxdeed_{tag}.json"
    cols = ["sale_event_date","case_number","certificate","parcel_id","applicant","owners_grid","owner_appraiser","situs","sale_date","status","amount_due","bid_amount","other_amount","opening_bid","final_bid","surplus","legal_desc"]
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in all_recs: w.writerow(r)
    json.dump(all_recs, open(out_json, "w"), indent=2)
    with_papa = sum(1 for r in all_recs if r.get("owner_appraiser"))
    log.info("=" * 50)
    log.info("TAXDEED: %d records, %d PAPA owners", len(all_recs), with_papa)
    for r in all_recs[:6]:
        log.info("  %s | %s | %s | %s", r["case_number"], r["parcel_id"], r["status"], r["owners_grid"][:35])
    log.info("WROTE %s", out_csv)
    print(f"DONE records={len(all_recs)} with_papa={with_papa} csv={out_csv}")


if __name__ == "__main__":
    main()
