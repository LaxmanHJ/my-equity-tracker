# CATALAN data audit

A read-only, reproducible audit of everything the forward log has collected. It answers: is the
data clean, how much is duplicate or templated, which categories carry information, and what the
AI scoring spend bought.

```bash
python3 -m catalan.audit.data_audit                  # ~25 s, writes output/
python3 -m catalan.audit.data_audit --fetch-missing  # re-download the PDF sample by URL
```

**What it reads**
- The committed NDJSON archive. It never reads `catalan.db`.
- From Turso, read-only: the NIFTY500 roster and the *set* of symbols with price data.
- The 145-PDF random sample, from `~/.cache/catalan_audit_pdfs/`. `output/pdf_sample_manifest.csv`
  pins it by seq_id, URL and sha256.

**What it never reads:** prices, returns, or anything that would join a verdict to a market
outcome. The NO-PEEK rule is in force until the amended pre-registration is committed.

**What it writes:** only files under `output/`. It changes no data, no schema and no filter.

## Outputs

| File | Contents |
|---|---|
| `output/data_audit_report.md` | Final report: direct answers to the 11 audit questions |
| `output/data_quality_audit.md` | Record counts, completeness, invalid values, timestamps, duplicates, content |
| `output/data_category_analysis.md` / `.csv` | Per-category table (all 113 categories in the CSV) |
| `output/data_value_analysis.md` / `.csv` | Information types, evidence tiers, redundancy, scorecard, cost |
| `output/audit_metrics.json` | Every headline number |
| `output/records_enriched.csv.gz` | One row per announcement with every derived flag, for drill-down |
| `output/field_completeness.*`, `daily_coverage.csv`, `duplicate_examples.csv`, `scorecard.*`, `cost_scenarios.*` | Supporting tables |
| `output/classification_validation_sample.csv`, `no_info_rationale_validation_sample.csv` | The samples hand-read to validate the classifier and the no-information proxy |
| `output/pdf_sample_manifest.csv`, `pdf_sample_results.csv` | The PDF sample and its per-document results |

The narrative `.md` reports were written from the generated numbers on 2026-09-27. Re-running
the script refreshes every table and number, but not the prose.
