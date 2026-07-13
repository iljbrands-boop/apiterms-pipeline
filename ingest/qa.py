#!/usr/bin/env python3
"""QA gate for the extraction output — run on every fill batch before publish.

Accuracy is the trust moat (CLAUDE.md): one wrong pricing claim on a pSEO page kills
the product. This validates data/census.jsonl structurally, flags records for human
review, and prints a 5% manual-review sample.

  python3 ingest/qa.py [path]      # default data/census.jsonl

Checks, per record:
  CRITICAL  fabricated_evidence  — an evidence_url that is NOT one of the record's
            own evidence_pages. The model was only given those pages; citing anything
            else means it invented the source. This is the check that protects trust.
  CRITICAL  missing_field        — a required schema field absent.
  CRITICAL  bad_confidence       — confidence not in {high,medium,low}.
  WARN      high_conf_thin       — confidence "high" but pricing_model AND auth_type
            AND rate_limits all null (over-confident on an empty record).
  WARN      no_evidence_at_all   — every field value is null (crawl likely failed).
  WARN      unverified_base_url  — base_url has a value but null evidence_url.

Exit code is non-zero if any CRITICAL flag fires, so it can gate a batch in a pipeline.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "data" / "census.jsonl"

REQUIRED = ["name", "what_it_does", "base_url", "auth_type", "free_tier",
            "pricing_model", "pricing_details", "rate_limits", "openapi_spec_url",
            "mcp_server", "category", "confidence", "notes"]
FIELD_OBJS = ["base_url", "auth_type", "free_tier", "pricing_model", "pricing_details",
              "rate_limits", "openapi_spec_url", "mcp_server"]
SAMPLE_RATE = 0.05


def audit(rec: dict) -> dict:
    dom = rec.get("domain", "?")
    crit, warn = [], []
    pages = set(rec.get("evidence_pages") or [])

    for f in REQUIRED:
        if f not in rec:
            crit.append(f"missing_field:{f}")

    if rec.get("confidence") not in ("high", "medium", "low"):
        crit.append(f"bad_confidence:{rec.get('confidence')!r}")

    # the trust-critical check: every cited evidence_url must be a page we crawled
    for f in FIELD_OBJS:
        obj = rec.get(f)
        if not isinstance(obj, dict):
            crit.append(f"malformed_field:{f}")
            continue
        ev = obj.get("evidence_url")
        if ev and pages and ev not in pages:
            crit.append(f"fabricated_evidence:{f}->{ev}")

    def val(f):
        o = rec.get(f)
        return o.get("value") if isinstance(o, dict) else None

    if rec.get("confidence") == "high" and not any(
            val(f) for f in ("pricing_model", "auth_type", "rate_limits")):
        warn.append("high_conf_thin")

    if all(val(f) is None for f in FIELD_OBJS):
        warn.append("no_evidence_at_all")

    b = rec.get("base_url")
    if isinstance(b, dict) and b.get("value") and not b.get("evidence_url"):
        warn.append("unverified_base_url")

    return {"domain": dom, "critical": crit, "warn": warn}


def val_of(rec, f):
    o = rec.get(f)
    return o.get("value") if isinstance(o, dict) else None


def main(path: Path):
    if not path.exists():
        sys.exit(f"{path} not found — run `extract.py fill` first.")
    recs = [json.loads(l) for l in path.open()]
    if not recs:
        sys.exit(f"{path} is empty.")
    n = len(recs)

    audits = [audit(r) for r in recs]
    crit = [a for a in audits if a["critical"]]
    warn = [a for a in audits if a["warn"] and not a["critical"]]

    print(f"== QA: {path.name} ({n} records) ==\n")

    # golden assertions: hand-verified domain/field pairs that must never be null
    # again (data/golden_assertions.json). A regression here is CRITICAL — it means
    # a re-fill silently lost a fact we know the vendor documents.
    gold_file = path.parent / "golden_assertions.json"
    if gold_file.exists():
        by_dom = {r["domain"]: r for r in recs}
        broken = []
        for a in json.loads(gold_file.read_text())["assertions"]:
            rec = by_dom.get(a["domain"])
            if rec is None or val_of(rec, a["field"]) is None:
                broken.append(f"{a['domain']}.{a['field']}")
        print(f"golden assertions: {'ALL PASS' if not broken else 'REGRESSED: ' + ', '.join(broken)}"
              f" ({len(json.loads(gold_file.read_text())['assertions'])} checked)\n")
        if broken:
            crit.append({"domain": "GOLDEN", "critical": [f"gold regression: {b}" for b in broken],
                         "warn": []})

    # confidence distribution
    conf = {}
    for r in recs:
        conf[r.get("confidence")] = conf.get(r.get("confidence"), 0) + 1
    print("confidence:", ", ".join(f"{k}={v}" for k, v in sorted(conf.items(), key=lambda x: str(x[0]))))

    # per-field fill rate — how often we actually captured a value
    print("\nfield fill-rate (non-null value):")
    for f in FIELD_OBJS:
        filled = sum(1 for r in recs if val_of(r, f) is not None)
        print(f"  {f:20s} {filled:4d}/{n}  ({filled/n:4.0%})")

    # flags
    print(f"\nCRITICAL flags: {len(crit)} record(s)")
    for a in crit:
        print(f"  ✗ {a['domain']}: {'; '.join(a['critical'])}")
    print(f"\nWARN flags: {len(warn)} record(s)")
    for a in warn[:25]:
        print(f"  ! {a['domain']}: {', '.join(a['warn'])}")
    if len(warn) > 25:
        print(f"  … +{len(warn) - 25} more")

    # deterministic 5% manual-review sample (every 1/SAMPLE_RATE-th record)
    step = max(1, int(1 / SAMPLE_RATE))
    sample = recs[::step]
    print(f"\n== manual-review sample ({len(sample)} of {n}, every {step}th record) ==")
    print("   verify each pricing/auth/limit claim against its evidence_url:\n")
    for r in sample:
        print(f"  {r.get('domain')}  [{r.get('confidence')}]  {r.get('category')}")
        for f in ("pricing_model", "free_tier", "auth_type", "rate_limits"):
            o = r.get(f) or {}
            v, e = o.get("value"), o.get("evidence_url")
            if v:
                print(f"      {f}: {v}")
                print(f"        ↳ {e}")
        print()

    print(f"summary: {len(crit)} critical, {len(warn)} warn, {n} total")
    sys.exit(1 if crit else 0)


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT)
