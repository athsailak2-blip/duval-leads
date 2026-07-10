#!/usr/bin/env python3
"""
Duval County leads dashboard -- single-file Flask app.

Unifies ALL lead sources into one searchable, filterable table that a
NON-TECHNICAL real-estate wholesaler can actually use.

Design rules (wholesaler-facing):
  * Only SHOW useful lead types. Money judgments (JDG / CCCJUDG) are dropped --
    they are not distressed-property opportunities.
  * Liens are filtered to amounts > $5,000 (small liens are not worth chasing).
  * Tax-deed rows that were REDEEMED (owner paid) are dropped -- they're dead.
  * Court / foreclosure / tax-deed leads carry an OPEN / CLOSED / SALE status.
    By default the dashboard shows OPEN leads only; a checkbox reveals all.
  * Type codes are shown as friendly names (Lien, Lis Pendens, Probate, ...).
  * The table leads with the PROPERTY ADDRESS (what a wholesaler acts on),
    then parties, amount, status, case #, and a Doc link.

Endpoints:
  /                 -> dashboard HTML
  /api/sources      -> JSON list of loaded source files + counts
  /api/leads        -> JSON array of all normalized leads (?source=ALL)
  /download         -> CSV of all leads
"""
import csv, glob, json, os, re
from pathlib import Path
from flask import Flask, jsonify, request, Response

DATA_DIR = Path("/root/duval/data")
app = Flask(__name__)

# Common normalized columns shown in the table (used for CSV download).
COLS = [
    "source", "lead_type", "record_date", "case_number", "name",
    "property_address", "amount", "status", "detail", "extra",
]

# ---------------------------------------------------------------------------
# Per-lead-type column schema + cleaning layer.
#
# The old dashboard forced EVERY lead type into one identical column set
# (Date / Property / Parties / Amount / Status / Case#). That is why headers
# did not match the data and parties looked "jumbled". We now give each
# lead_type its own ordered list of [field_key, header_label, kind] and a
# cleaning pass (`enrich`) that fixes mislabeled/mis-placed data.
#
# kind: "txt" | "addr" | "amt" | "date" | "status" | "link"
# ---------------------------------------------------------------------------
SCHEMA = {
    # --- Official records ---
    "Lien": [
        ("creditor", "Creditor", "txt"), ("debtor", "Debtor", "txt"),
        ("property_address", "Property", "addr"),
        ("amount", "Lien Amount", "amt"),
        ("record_date", "Recorded", "date"), ("detail", "Doc", "link"),
    ],
    "Lis Pendens": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("property_address", "Property", "addr"),
        ("record_date", "Recorded", "date"), ("detail", "Doc", "link"),
        # NOTE: no Amount column -- a lis pendens is a notice, not a $ figure.
    ],
    "Probate": [
        ("decedent", "Decedent", "txt"), ("party", "Party / PR", "txt"),
        ("record_date", "Recorded", "date"), ("detail", "Doc", "link"),
    ],
    # --- Court / foreclosure auctions ---
    "Foreclosure": [
        ("property_address", "Property", "addr"), ("name", "Owner", "txt"),
        ("amount", "Final Judgment", "amt"), ("status", "Status", "status"),
        ("case_number", "Case #", "txt"),
    ],
    # Civil subtypes (CoreCivil). Foreclosure subtypes have NO usable property
    # address (the source returns the attorney's address) and NO date, so we
    # omit both and show the parties cleanly (primary plaintiff/defendant).
    "Commercial Foreclosure": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Homestead Res Foreclosure": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Non Homestead Res Foreclosure": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Mortgage Lien Foreclosure": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Other Real Prop High": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Other Real Prop Mid": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Other Real Prop Low": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    "Ejectment": [
        ("plaintiff", "Plaintiff", "txt"), ("defendant", "Defendant", "txt"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
    # Estate / probate civil: the source stashes the decedent in `filing_date`,
    # so we surface a clean "Decedent" column instead of a bogus date.
    "Opening Estate": [
        ("decedent", "Decedent", "txt"), ("status", "Status", "status"),
        ("case_number", "Case #", "txt"),
    ],
    "Summary Admin High": [
        ("decedent", "Decedent", "txt"), ("status", "Status", "status"),
        ("case_number", "Case #", "txt"),
    ],
    "Summary Admin Low": [
        ("decedent", "Decedent", "txt"), ("status", "Status", "status"),
        ("case_number", "Case #", "txt"),
    ],
    # --- Other sources ---
    "Tax Deed": [
        ("property_address", "Property (Legal)", "addr"),
        ("name", "Owner / Applicant", "txt"),
        ("amount", "Opening Bid", "amt"), ("status", "Sale", "status"),
        ("case_number", "Cert #", "txt"),
    ],
    "Tax Delinquent": [
        ("case_number", "Parcel", "txt"), ("name", "Owner", "txt"),
        ("amount", "Assessed Value", "amt"), ("tax_year", "Tax Yr", "txt"),
        ("property_address", "Situs", "addr"),
    ],
    "Vacant": [
        ("property_address", "Address", "addr"), ("name", "Owner", "txt"),
        ("case_number", "RE #", "txt"), ("use", "Use", "txt"),
        ("status", "Status", "status"),
    ],
    "Eviction": [
        ("property_address", "Property", "addr"), ("name", "Defendant", "txt"),
        ("plaintiff", "Plaintiff", "txt"), ("record_date", "Filed", "date"),
        ("status", "Status", "status"), ("case_number", "Case #", "txt"),
    ],
}

# Generic labels used when a type has no explicit schema (the "ALL" union view).
GEN_ORDER = ["property_address", "name", "plaintiff", "defendant",
             "creditor", "debtor", "decedent", "party", "amount",
             "record_date", "tax_year", "use", "status", "case_number", "detail"]
GEN_LABEL = {
    "property_address": "Property", "name": "Name", "plaintiff": "Plaintiff",
    "defendant": "Defendant", "creditor": "Creditor", "debtor": "Debtor",
    "decedent": "Decedent", "party": "Party", "amount": "Amount",
    "record_date": "Date", "tax_year": "Tax Yr", "use": "Use",
    "status": "Status", "case_number": "Case #", "detail": "Doc",
}


def _split_primary(s):
    """'A | B | C' -> ('A', 2)  (primary name + how many extra)."""
    if not s:
        return ("", 0)
    parts = [p.strip() for p in str(s).split("|") if p.strip()]
    if not parts:
        return ("", 0)
    return (parts[0], len(parts) - 1)


def _probate_split(grantor, grantee):
    """Bifurcate a probate instrument into decedent vs. real party (PR/heir).

    e.g. grantor='ESTATE SHORE LEWIS FAY DECEASED', grantee='SHORE LAURIE ELIZABETH'
         -> ('SHORE LEWIS FAY', 'SHORE LAURIE ELIZABETH')
    """
    g = (grantor or "").strip()
    n = (grantee or "").strip()
    g_dec = bool(re.search(r"DECEASED|ESTATE", g, re.I))
    n_dec = bool(re.search(r"DECEASED|ESTATE", n, re.I))
    if g_dec and not n_dec:
        dec_raw, party_raw = g, n
    elif n_dec and not g_dec:
        dec_raw, party_raw = n, g
    else:
        # both or neither flagged -- decedent is the longer / Estate-tagged side
        dec_raw, party_raw = (g, n) if g_dec else (n, g)
    dec = re.sub(r"\b(ESTATE|DECEASED)\b", "", dec_raw, flags=re.I).strip(" ,|")
    party = "" if re.fullmatch(r"ESTATE\s*", party_raw or "", re.I) else party_raw
    return dec, party


def _civil_parties(s):
    """Pull (plaintiff, defendant) out of a CoreCivil party line stored in
    `filing_date`, e.g.
      'LSREF7 RANGER, LLC (P) MW BEACON POINTE 1, LLC (D) ... (D) ...'
    Returns the first (P) party and first (D) party, or ('','') if none.
    The scraper stuffed the whole party blob into filing_date, so this is
    the only reliable source for plaintiff/defendant on these rows.
    """
    s = (s or "").strip()
    pls = re.findall(r"([^(]+?)\s*\(P\)", s)
    dfs = re.findall(r"([^(]+?)\s*\(D\)", s)
    pl = pls[0].strip(" ,") if pls else ""
    df = dfs[0].strip(" ,") if dfs else ""
    return pl, df


def _civil_decedent(s):
    """Pull a clean decedent name out of a garbled CoreCivil `filing_date`.

    The scraper stuffed the party line into `filing_date`. Examples:
      'NICHOLAS, HIRAM HERBERT (D) DOB: 5/9/...'
      'PETERSEN III, CHARLES DONALD (W) ... (P) ... (W) ...'
    We take everything up to the first role tag ' (X)' as the decedent.
    """
    s = (s or "").strip()
    m = re.match(r"^(.*?)\s*\([A-Z]\)", s)
    if m:
        return m.group(1).strip(" ,")
    # no role tag: if multiple parties joined by ' | ', first is usually decedent
    if " | " in s:
        return s.split(" | ")[0].strip(" ,")
    return s


def enrich(r):
    """Attach a per-type `fields` dict (key->value) with cleaned data.

    Mutates and returns `r`.
    """
    src = r.get("source", "")
    lt = r.get("lead_type", "")
    ex = r.get("extra", {}) or {}
    f = {}
    if src == "Foreclosure":
        f["property_address"] = r.get("property_address", "")
        f["name"] = r.get("name", "")
        f["amount"] = r.get("amount", "")
        f["status"] = r.get("status", "") or "\u2014"
        f["case_number"] = r.get("case_number", "")
    elif src == "OfficialRecords" and lt == "Lien":
        # Some source files use creditor/debtor, others use grantor/grantee.
        cred = ex.get("creditor") or ex.get("grantor") or ""
        debt = ex.get("debtor") or ex.get("grantee") or ""
        f["creditor"] = cred
        f["debtor"] = debt
        f["property_address"] = r.get("property_address", "")
        f["amount"] = r.get("amount", "")
        f["record_date"] = r.get("record_date", "")
        f["detail"] = r.get("detail", "")
    elif src == "OfficialRecords" and lt == "Lis Pendens":
        f["plaintiff"] = ex.get("grantor", "") or ""
        f["defendant"] = ex.get("grantee", "") or ""
        f["property_address"] = r.get("property_address", "")
        f["record_date"] = r.get("record_date", "")
        f["detail"] = r.get("detail", "")
    elif src == "OfficialRecords" and lt == "Probate":
        dec, party = _probate_split(ex.get("grantor", ""), ex.get("grantee", ""))
        f["decedent"] = dec
        f["party"] = party
        f["record_date"] = r.get("record_date", "")
        f["detail"] = r.get("detail", "")
    elif src == "TaxDeed":
        f["property_address"] = r.get("property_address", "")
        f["name"] = r.get("name", "")
        f["amount"] = r.get("amount", "")
        f["status"] = r.get("status", "") or "\u2014"
        f["case_number"] = r.get("case_number", "")
    elif src == "TaxDelinquent":
        f["case_number"] = r.get("case_number", "")
        f["name"] = r.get("name", "")
        f["amount"] = r.get("amount", "")
        f["tax_year"] = ex.get("tax_year", "") or ""
        f["property_address"] = r.get("property_address", "")
    elif src == "VacantResidential":
        f["property_address"] = r.get("property_address", "")
        f["name"] = r.get("name", "")
        f["case_number"] = r.get("case_number", "")
        f["use"] = ex.get("property_use_label", "") or ""
        f["status"] = r.get("status", "") or "\u2014"
    elif src == "Eviction":
        f["property_address"] = r.get("property_address", "")
        nm, _ = _split_primary(r.get("name", ""))
        f["name"] = nm
        pl, _ = _split_primary(ex.get("plaintiff", ""))
        f["plaintiff"] = pl
        # NOTE: the scraper's `filing_date` column actually holds the party
        # line (e.g. "5906 INC (P) JACKSON, ANGEL (D)"), not a real date --
        # the true filing date is not captured. Leave Filed blank rather than
        # show party text in a date column.
        f["record_date"] = ""
        f["status"] = r.get("status", "") or "—"
        f["case_number"] = r.get("case_number", "")
    elif src == "CoreCivil":
        if lt in ("Opening Estate", "Summary Admin High", "Summary Admin Low"):
            f["decedent"] = _civil_decedent(r.get("record_date", ""))
            f["status"] = r.get("status", "") or "\u2014"
            f["case_number"] = r.get("case_number", "")
        else:
            pl, _ = _split_primary(ex.get("plaintiff", ""))
            df, _ = _split_primary(ex.get("defendant", ""))
            if not pl and not df:
                # fallback: the scraper parked the party line in record_date
                pl, df = _civil_parties(r.get("record_date", ""))
            f["plaintiff"] = pl
            f["defendant"] = df
            f["status"] = r.get("status", "") or "—"
            f["case_number"] = r.get("case_number", "")
    else:
        # fallback: surface the flat fields
        f["property_address"] = r.get("property_address", "")
        f["name"] = r.get("name", "")
        f["amount"] = r.get("amount", "")
        f["record_date"] = r.get("record_date", "")
        f["status"] = r.get("status", "")
        f["case_number"] = r.get("case_number", "")
    r["fields"] = f
    return r

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------
def _amt(v):
    """Parse a dollar-ish string to float, or None."""
    if not v:
        return None
    m = re.sub(r"[^0-9.]", "", str(v))
    try:
        return float(m) if m else None
    except ValueError:
        return None


# Official-records doc types that are NOT wholesaling leads (money judgments).
DROP_OR_TYPES = {"JDG", "CCCJUDG"}

# Non-property liens to drop per user (hospital/physician + credit-card debt
# buyers + generic gov filings) — these are not wholesale leads.
import re as _re
_NONPROP_LIEN_RE = _re.compile(
    r"""(?ix)
    \b(hca|hospital|health|medical|physician|surgery|surgicenter|orthopaed|
       orthopedic|clinic|care\s*center|emergency|urgent|rehab|asc\b|
       baptist|memorial|st\.?\s*vincent|cleveland\s*clinic|mayo|
       lvnv|midland\s*credit|portfolio\s*recovery|resurgent|amcollect|
       enco|calvary\s*spit|velocity\s*investments|cavalry|rcm\s*acquisition|
       capital\s*one|discover\s*bank|synchrony|citibank|american\s*express|
       one(?:main)?\s*financial|credit\s*acceptance|spring\s*oaks|
       quadient|enhanced\s*recovery|the\s*arbor|avant|upstart|
       florida\s*state\s*(?:of|rev)|florida\s*revenue|city\s*of\s*jacksonville|
       duval\s*county\s*property|property\s*appraiser|tax\s*collector|
       dept(?:artment)?\s*of\s*revenue|irs\b|internal\s*revenue)
    """)
# Friendly labels for the doc types we DO keep.
OR_TYPE_LABEL = {
    "LN": "Lien",
    "LP": "Lis Pendens",
    "PROB": "Probate",
    "RPOFJ": "Final Judgment",
    "VAFJ": "Final Judgment",
    "NTD": "Notice of Default",
    "CERTDEED": "Certificate of Title",
}


def _title_case(code):
    """'CommercialForeclosure' -> 'Commercial Foreclosure'."""
    return re.sub(r"(?<!^)(?=[A-Z])", " ", code).strip()


# ---------------------------------------------------------------------------
# Loaders: each returns list[dict] with (at least) the normalized keys above.
# ---------------------------------------------------------------------------
def _read_csv(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_offrec(p):
    out = []
    for r in _read_csv(p):
        lt = (r.get("lead_type") or "").strip()
        # Some merged files (liens_*.csv, leads_today.csv) use a different
        # schema: a 'doc_type' column with values like LIEN/LP, and
        # party_a/party_b instead of grantor/grantee. Normalize to our types.
        doc_type = (r.get("doc_type") or "").strip().upper()
        if not lt and doc_type:
            lt = doc_type
        if lt in DROP_OR_TYPES:
            continue  # money judgments are not property leads
        if lt == "LN" or doc_type == "LIEN":
            a = _amt(r.get("amount", ""))
            if a is not None and a <= 5000:
                continue
            # skip non-property liens (hospital/physician, credit-card debt
            # buyers, government non-lien filings) — defense in depth
            party_blob = " ".join([str(r.get("creditor", r.get("party_a", ""))),
                                   str(r.get("debtor", r.get("party_b", "")))]).lower()
            if _NONPROP_LIEN_RE.search(party_blob):
                continue
            label = "Lien"
            parties = (r.get("creditor", r.get("party_a", "")) + " / " +
                       r.get("debtor", r.get("party_b", ""))).strip(" /")
            prop = r.get("address", "") or r.get("property_address", "")
            detail = r.get("detail_url", "") or r.get("doc_image", "")
        elif lt == "LP" or doc_type == "LP":
            label = "Lis Pendens"
            parties = (r.get("grantor", r.get("party_a", "")) + " / " +
                       r.get("grantee", r.get("party_b", ""))).strip(" /")
            prop = r.get("address", "") or r.get("property_address", "")
            detail = r.get("detail_url", "")
        else:
            label = OR_TYPE_LABEL.get(lt, lt)
            parties = (r.get("grantor", "") + " / " + r.get("grantee", "")).strip(" /")
            prop = r.get("address", "") or r.get("property_address", "")
            detail = r.get("detail_url", "")
        out.append({
            "source": "OfficialRecords",
            "lead_type": label,
            "record_date": r.get("record_date", ""),
            "case_number": r.get("case_number", ""),
            "name": parties,
            "property_address": prop,
            "amount": r.get("amount", ""),
            "status": "",
            "detail": detail,
            "extra": {"instrument": r.get("instrument", ""),
                      "book_page": r.get("book_page", ""),
                      "consideration": r.get("consideration", ""),
                      "extract_by": r.get("extract_by", ""),
                      "source_doc": r.get("source", ""),
                      "creditor": r.get("creditor", r.get("party_a", "")),
                      "debtor": r.get("debtor", r.get("party_b", "")),
                      "grantor": r.get("grantor", ""),
                      "grantee": r.get("grantee", "")},
        })
    return out


def load_foreclosure(p):
    out = []
    for r in _read_csv(p):
        out.append({
            "source": "Foreclosure",
            "lead_type": "Foreclosure",
            "record_date": "",
            "case_number": r.get("case_number", ""),
            "name": r.get("owner_name", ""),
            "property_address": r.get("property_address", ""),
            "amount": r.get("final_judgment_amount", ""),
            "status": (r.get("auction_status", "") or r.get("sale_amount", "") or "").strip(),
            "detail": "",
            "extra": {"parcel_id": r.get("parcel_id", ""),
                      "assessed_value": r.get("assessed_value", ""),
                      "plaintiff_max_bid": r.get("plaintiff_max_bid", ""),
                      "sold_to": r.get("sold_to", ""),
                      "owner_mailing": r.get("owner_mailing", "")},
        })
    return out


def load_taxdeed(p):
    out = []
    for r in _read_csv(p):
        st = (r.get("status", "") or "").strip()
        if re.search(r"REDEEM", st, re.I):
            continue  # owner paid up -> not a live lead
        status_label = "Sale Scheduled" if st.upper() == "SALE" else st
        owners = r.get("owner_appraiser", "") or r.get("owners_grid", "")
        out.append({
            "source": "TaxDeed",
            "lead_type": "Tax Deed",
            "record_date": r.get("sale_event_date", ""),
            "case_number": r.get("certificate", "") or r.get("case_number", ""),
            "name": owners,
            "property_address": r.get("legal_desc", "") or r.get("parcel_id", ""),
            "amount": r.get("opening_bid", "") or r.get("amount_due", ""),
            "status": status_label,
            "detail": "",
            "extra": {"parcel_id": r.get("parcel_id", ""),
                      "sale_date": r.get("sale_date", ""),
                      "amount_due": r.get("amount_due", ""),
                      "bid_amount": r.get("bid_amount", ""),
                      "applicant": r.get("applicant", ""),
                      "legal_desc": r.get("legal_desc", "")},
        })
    return out


def load_codeenf(p):
    out = []
    for r in _read_csv(p):
        out.append({
            "source": "CodeEnforcement",
            "lead_type": "CE-" + (r.get("case_type", "").split("(")[0].strip() or "X"),
            "record_date": r.get("date", ""),
            "case_number": r.get("case_number", ""),
            "name": r.get("owner", ""),
            "property_address": r.get("address", ""),
            "amount": "",
            "status": r.get("status", ""),
            "detail": r.get("detail_url", ""),
            "extra": {"description": r.get("description", ""),
                      "short_notes": r.get("short_notes", ""),
                      "expiration": r.get("expiration", "")},
        })
    return out


def load_vacant(p):
    out = []
    for r in _read_csv(p):
        addr = " ".join(filter(None, [r.get("street_no", ""), r.get("street_name", ""),
                                       r.get("street_type", ""), r.get("direction", ""),
                                       r.get("unit", ""), r.get("city", ""), r.get("zip", "")])).strip()
        out.append({
            "source": "VacantResidential",
            "lead_type": "Vacant",
            "record_date": "",
            "case_number": r.get("re_number", ""),
            "name": r.get("owner", ""),
            "property_address": addr,
            "amount": "",
            "status": "Vacant",
            "detail": r.get("parcel_url", ""),
            "extra": {"parcel_id": r.get("re_number", ""),
                      "property_use_code": r.get("property_use_code", ""),
                      "property_use_label": r.get("property_use_label", "")},
        })
    return out


def load_taxdelinquent(p):
    out = []
    for r in _read_csv(p):
        owner = r.get("owner_appraiser", "")
        parcel = r.get("parcel_id", "") or r.get("re_number", "")
        out.append({
            "source": "TaxDelinquent",
            "lead_type": "Tax Delinquent",
            "record_date": "",
            "case_number": parcel,
            "name": owner,
            "property_address": parcel,
            "amount": r.get("assessed_value", "") or r.get("purchase_amount", ""),
            "status": r.get("status", "") or "Delinquent",
            "detail": r.get("detail_url", "") or r.get("parcel_url", ""),
            "extra": {"parcel_id": parcel,
                      "certificate_no": r.get("certificate_no", ""),
                      "tax_year": r.get("tax_year", ""),
                      "purchase_amount": r.get("purchase_amount", ""),
                      "assessed_value": r.get("assessed_value", ""),
                      "situs": r.get("situs", "")},
        })
    return out


def load_eviction(p):
    out = []
    for r in _read_csv(p):
        st = (r.get("status", "") or "").replace("----", "").strip() or "Open"
        out.append({
            "source": "Eviction",
            "lead_type": "Eviction",
            "record_date": r.get("filing_date", ""),
            "case_number": r.get("case_number", ""),
            "name": r.get("defendant", "") or r.get("plaintiff", ""),
            "property_address": r.get("property_address", ""),
            "amount": "",
            "status": st,
            "detail": "",
            "extra": {"plaintiff": r.get("plaintiff", ""),
                      "defendant": r.get("defendant", ""),
                      "parcel_id": r.get("parcel_id", ""),
                      "owner_appraiser": r.get("owner_appraiser", "")},
        })
    return out


def load_corecivil(p):
    out = []
    seen_case = set()
    for r in _read_csv(p):
        st = (r.get("status", "") or "").replace("----", "").strip()
        st = re.sub(r"\s+", " ", st)
        ct = (r.get("case_type", "") or "X").strip()
        rec = {
            "source": "CoreCivil",
            "lead_type": _title_case(ct),
            "record_date": r.get("filing_date", ""),
            "case_number": r.get("case_number", ""),
            "name": r.get("defendant", "") or r.get("plaintiff", ""),
            "property_address": r.get("property_address", ""),
            "amount": "",
            "status": st,
            "detail": "",
            "extra": {"court_type": r.get("court_type", ""),
                      "case_type_code": r.get("case_type_code", ""),
                      "plaintiff": r.get("plaintiff", ""),
                      "defendant": r.get("defendant", ""),
                      "parcel_id": r.get("parcel_id", ""),
                      "owner_appraiser": r.get("owner_appraiser", "")},
        }
        # De-dupe: core_civil.csv repeats the same case under multiple
        # foreclosure type labels (Commercial/Homestead/Non-Homestead/
        # Mortgage). Keep the first occurrence so each case appears once.
        cn = rec["case_number"]
        if cn and cn in seen_case:
            continue
        if cn:
            seen_case.add(cn)
        out.append(rec)
    return out


SOURCES = [
    # glob pattern, loader, human label
    ("duval_leads_*_combined.csv", load_offrec, "Public Records"),
    ("foreclosure_*.csv", load_foreclosure, "Foreclosure Auction"),
    ("taxdeed_future.csv", load_taxdeed, "Tax Deed Sales"),
    ("vacant_residential.csv", load_vacant, "Vacant Properties"),
    ("tax_delinquent.csv", load_taxdelinquent, "Tax Delinquent"),
    ("eviction.csv", load_eviction, "Eviction"),
    ("core_civil.csv", load_corecivil, "Civil Court Cases"),
]


def collect():
    rows = []
    files = []
    for pat, loader, label in SOURCES:
        for p in sorted(glob.glob(str(DATA_DIR / pat)), reverse=True):
            try:
                recs = loader(p)
            except Exception as e:
                print("load fail", p, e)
                continue
            if recs:
                for r in recs:
                    enrich(r)
                rows.extend(recs)
                files.append({"file": Path(p).name, "source": label, "count": len(recs)})
    return rows, files


ROWS, FILES = collect()


def get_leads(source="ALL"):
    source = (source or "ALL").upper()
    if source == "ALL":
        return ROWS
    return [r for r in ROWS if str(r.get("source", "")).upper() == source]


@app.route("/")
def index():
    return HTML


@app.route("/api/sources")
def api_sources():
    return jsonify(FILES)


@app.route("/api/schema")
def api_schema():
    return jsonify(SCHEMA)


@app.route("/api/leads")
def api_leads():
    source = request.args.get("source", "ALL")
    return jsonify(get_leads(source))


@app.route("/download")
def download():
    source = request.args.get("source", "ALL")
    rows = get_leads(source)
    out = ["﻿" + ",".join(COLS)]
    for r in rows:
        out.append(",".join('"%s"' % (str(r.get(c, "")).replace('"', '""')) for c in COLS))
    csv_text = "\n".join(out)
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=duval_leads_{source}.csv"})


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Duval County Leads</title>
<style>
  :root{--bg:#0f1419;--card:#1a212b;--ink:#e6edf3;--mut:#8b98a5;--acc:#2f81f7;--grn:#3fb950;--red:#f85149;--amber:#d29922;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.45 system-ui,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--ink)}
  header{padding:14px 18px;background:var(--card);border-bottom:1px solid #2d3340;position:sticky;top:0;z-index:5}
  h1{font-size:18px;margin:0 0 10px;font-weight:600}
  .bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
  select,input,button{background:#0d1117;color:var(--ink);border:1px solid #30363d;border-radius:6px;padding:8px 10px;font-size:14px}
  input[type=search]{min-width:240px;flex:1}
  label.chk{display:flex;align-items:center;gap:6px;color:var(--mut);font-size:13px;cursor:pointer;user-select:none}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .chip{background:#21262d;border:1px solid #30363d;color:var(--mut);padding:5px 11px;border-radius:20px;cursor:pointer;user-select:none;font-size:13px}
  .chip.on{background:var(--acc);color:#fff;border-color:var(--acc)}
  main{padding:14px 18px}
  .cards{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:12px}
  .card{background:var(--card);border:1px solid #2d3340;border-radius:8px;padding:10px 14px;min-width:120px}
  .card b{display:block;font-size:22px}
  .card span{color:var(--mut);font-size:12px}
  .count{color:var(--mut);margin:4px 0 10px}
  table{width:100%;border-collapse:collapse;font-size:13px}
  th,td{padding:9px 10px;text-align:left;border-bottom:1px solid #21262d;vertical-align:top}
  th{position:sticky;top:120px;background:#161b22;cursor:pointer;white-space:nowrap;color:var(--mut);font-weight:600}
  th .ar{color:var(--acc)}
  tr:hover td{background:#161b22}
  .tag{display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:600;white-space:nowrap}
  .s-OfficialRecords{background:#0b2f3a;color:#7fd1e0}
  .s-Foreclosure{background:#3a0b1f;color:#f09bbf}
  .s-TaxDeed{background:#3a2f0b;color:#f0c674}
  .s-VacantResidential{background:#2f0b3a;color:#c79bf0}
  .s-TaxDelinquent{background:#0b3a2f;color:#7fe0b0}
  .s-Eviction{background:#3a2f0b;color:#f0d27f}
  .s-CoreCivil{background:#0b203a;color:#7fb0f0}
  a{color:var(--acc);text-decoration:none}
  .amt{color:var(--grn);font-weight:600}
  .prop{font-weight:600;color:#fff}
  .wrap{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:240px}
  .scroll{overflow:auto;max-height:72vh;border:1px solid #21262d;border-radius:8px}
  .btn{background:var(--acc);color:#fff;border:none;cursor:pointer;font-weight:600}
  .btn:hover{filter:brightness(1.1)}
  .badge{display:inline-block;padding:2px 9px;border-radius:10px;font-size:11px;font-weight:700}
  .st-open{background:#0b3a1f;color:#7fe0a0}
  .st-closed{background:#21262d;color:#8b98a5}
  .st-sale{background:#0b2f3a;color:#7fd1e0}
  .st-other{background:#21262d;color:#8b98a5}
</style>
</head>
<body>
<header>
  <h1>Duval County Distressed Leads</h1>
  <div class="bar">
    <select id="source"><option value="ALL">All sources</option></select>
    <input id="q" type="search" placeholder="Search address, name, case #, parcel...">
    <label class="chk"><input type="checkbox" id="openonly" checked> Open leads only</label>
    <button class="btn" onclick="dl()">Download CSV</button>
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
let SCHEMA={},GEN={order:[],label:{}};
let DATA=[],SRC="ALL",TYPE="ALL",OPENONLY=true,SORT={c:null,d:1};
const $=id=>document.getElementById(id);

function colsFor(type){
  if(TYPE!=="ALL" && SCHEMA[TYPE]) return SCHEMA[TYPE];
  return GEN.order.map(k=>[k, GEN.label[k]||k, "txt"]);
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

async function init(){
  const sources=await (await fetch("/api/sources")).json();
  SCHEMA=await (await fetch("/api/schema")).json();
  const sel=$("source");
  for(const s of sources){const o=document.createElement("option");o.value=s.source;o.textContent=`${s.source} (${s.count})`;sel.appendChild(o);}
  sel.onchange=()=>{SRC=sel.value;load();};
  $("q").oninput=render;
  $("openonly").onchange=()=>{OPENONLY=$("openonly").checked;load();};
  load();
}
async function load(){setStatus("Loading...");DATA=await (await fetch("/api/leads?source="+SRC)).json();buildChips();render();setStatus("");}
function setStatus(s){$("status").textContent=s;}
function buildChips(){
  const types=["ALL",...Array.from(new Set(DATA.map(r=>r.lead_type))).sort()];
  const ch=$("chips");ch.innerHTML="";
  types.forEach(t=>{const c=document.createElement("span");c.className="chip"+(t===TYPE?" on":"");c.textContent=t;c.onclick=()=>{TYPE=t;[...ch.children].forEach(x=>x.classList.remove("on"));c.classList.add("on");render();};ch.appendChild(c);});
}
function filtered(){
  let rows=DATA;
  if(TYPE!=="ALL")rows=rows.filter(r=>r.lead_type===TYPE);
  if(OPENONLY)rows=rows.filter(r=>!isClosed(r.status));
  const q=$("q").value.trim().toLowerCase();
  if(q){rows=rows.filter(r=>Object.values(r).join(" ").toLowerCase().includes(q));}
  if(SORT.c){rows=[...rows].sort((a,b)=>{const x=(a.fields&&a.fields[SORT.c])||"";const y=(b.fields&&b.fields[SORT.c])||"";return (x<y?-1:x>y?1:0)*SORT.d;});}
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
  $("cnt").textContent=`${rows.length} of ${DATA.length} leads`+(OPENONLY?" (open only)":"");
  const cols=colsFor(TYPE);
  const cidx={};cols.forEach((c,i)=>cidx[c[0]]=i);
  $("body").innerHTML=rows.map(r=>{
    const cols=colsFor(TYPE);
    const tag=`<span class="tag s-${r.source}">${r.source}</span>`;
    const cells=cols.map(([k,label,kind])=>{
      const v=(r.fields&&r.fields[k])||"";
      if(kind==="amt") return `<td>${v?`<span class="amt">${fmtAmt(v)}</span>`:""}</td>`;
      if(kind==="status") return `<td>${statusBadge(v)}</td>`;
      if(kind==="link") return `<td>${v?`<a href="${v}" target="_blank">open</a>`:""}</td>`;
      const cls=kind==="addr"?"prop":(kind==="txt"?"wrap":"");
      const extra=(kind==="addr"||kind==="txt")?" title=\""+String(v).replace(/"/g,'&quot;')+"\"":"";
      return `<td class="${cls}"${extra}>${v||""}</td>`;
    }).join("");
    return `<tr><td>${tag}</td><td>${r.lead_type}</td>${cells}</tr>`;
  }).join("")||`<tr><td colspan="${cols.length+2}" style="color:var(--mut);padding:20px">No leads match.</td></tr>`;
}
function sort(c,i){if(SORT.c===c)SORT.d*=-1;else{SORT.c=c;SORT.d=1;}[...$("head").querySelectorAll(".ar")].forEach(x=>x.textContent="");if($("ar"+i))$("ar"+i).textContent=SORT.d>0?"▲":"▼";render();}
function dl(){
  const rows=(SRC==="ALL"?DATA:ALL_SRC());
  const cols=colsFor(TYPE===undefined?TYPE:TYPE);
  const flat=["source","lead_type",...colsFor(TYPE).map(c=>c[0])];
  const csv=[flat.join(",")].concat(rows.map(r=>flat.map(c=>{
    if(c==="source")return `"${r.source}"`;
    if(c==="lead_type")return `"${r.lead_type}"`;
    const v=(r.fields&&r.fields[c])||"";return `"${String(v).replace(/"/g,'""')}"`;
  }).join(","))).join("\n");
  const blob=new Blob([csv],{type:"text/csv"});const a=document.createElement("a");
  a.href=URL.createObjectURL(blob);a.download="duval_leads_"+(SRC==="ALL"?"all":SRC)+".csv";a.click();
}
function ALL_SRC(){return DATA;}
init();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import sys
    host = "0.0.0.0"
    port = 80
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)
