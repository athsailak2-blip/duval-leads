# Duval County Distressed Leads

Static dashboard of Duval County (FL) distressed-property leads — foreclosures,
tax deeds, tax-delinquent certificates, liens, probate, evictions, code
enforcement, and vacant residential parcels.

## Site

The deployed site is a single pre-built file: `public/index.html`. Open it
directly in a browser, or serve the folder (`python -m http.server` inside
`public/`).

## Rebuild

```bash
source .venv/bin/activate
python build_static.py      # regenerates public/index.html from data/*.csv
```

`dashboard.py` is the optional Flask dev server (`python dashboard.py --port 8765`).

## Deploy (GitHub Pages)

Pushing to `main` triggers `.github/workflows/pages.yml`, which publishes the
`public/` folder to GitHub Pages. No backend required.

## Data

Source CSVs under `data/` are not committed (they contain PII and are large).
Re-run the scrapers in `ref_*/` to refresh them, then `build_static.py`.
