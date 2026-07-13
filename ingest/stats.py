#!/usr/bin/env python3
"""State of the API Economy — the proof-engine report.

REUSE doc: "monthly number + one chart + LinkedIn/HN/subreddit" transfers verbatim;
'State of the API Economy' is the EU Rent Index of this product. This computes the
headline numbers reproducibly from the pipeline's own data so the launch post (and the
monthly ritual) never hand-waves a stat.

  python3 ingest/stats.py

Two sections:
  CORPUS   — from data/seed_classified.jsonl (the 1,833-domain census universe):
             liveness, llms.txt adoption, spec availability, category spread.
  EXTRACTED — from data/census.jsonl once fill has run: auth/pricing/MCP/free-tier
             distributions across completed records + extraction confidence.

Everything is a plain count + percentage with the denominator stated — no smoothing.
"""
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLASSIFIED = ROOT / "data" / "seed_classified.jsonl"
CENSUS = ROOT / "data" / "census.jsonl"


def load(path):
    return [json.loads(l) for l in path.open()] if path.exists() else []


def pct(a, b):
    return f"{a/b:5.1%}" if b else "   n/a"


def bar(a, b, width=24):
    fill = int(round(width * a / b)) if b else 0
    return "█" * fill + "·" * (width - fill)


def line(label, a, b):
    print(f"  {label:26s} {a:5d}/{b:<5d} {pct(a, b)}  {bar(a, b)}")


def top_counter(items, n=12):
    c = collections.Counter(x for x in items if x)
    return c.most_common(n)


def corpus_section(recs):
    n = len(recs)
    print(f"\n{'='*60}\nCORPUS — {n} API domains (seed_classified.jsonl)\n{'='*60}")
    alive = [r for r in recs if r.get("alive")]
    line("alive (reachable)", len(alive), n)
    line("serves /llms.txt", sum(1 for r in recs if r.get("llms_txt")), n)
    line("  ↳ among alive", sum(1 for r in alive if r.get("llms_txt")), len(alive))
    spec = sum(1 for r in recs if r.get("spec_url") or r.get("openapi_probe"))
    line("has OpenAPI spec", spec, n)
    line("  ↳ probe-discovered", sum(1 for r in recs if r.get("openapi_probe")), n)

    print("\n  top categories:")
    for cat, c in top_counter([r.get("category") for r in recs], 12):
        print(f"    {cat:28s} {c:4d}  {pct(c, n)}")

    print("\n  registry source:")
    src = collections.Counter()
    for r in recs:
        for s in r.get("sources", []):
            src[s] += 1
    for s, c in src.most_common():
        print(f"    {s:28s} {c:4d}")


def field_val(rec, f):
    o = rec.get(f)
    return o.get("value") if isinstance(o, dict) else None


def extracted_section(recs):
    n = len(recs)
    print(f"\n{'='*60}\nEXTRACTED — {n} completed census records (census.jsonl)\n{'='*60}")
    if not n:
        print("  (none yet — run `extract.py fill` after ANTHROPIC_API_KEY is set)")
        return

    line("has free tier", sum(1 for r in recs if field_val(r, "free_tier")), n)
    line("exposes an MCP server", sum(1 for r in recs if field_val(r, "mcp_server")), n)
    line("has documented rate limits", sum(1 for r in recs if field_val(r, "rate_limits")), n)
    line("has OpenAPI spec URL", sum(1 for r in recs if field_val(r, "openapi_spec_url")), n)

    print("\n  auth type:")
    for v, c in top_counter([field_val(r, "auth_type") for r in recs]):
        print(f"    {str(v):20s} {c:4d}  {pct(c, n)}")
    print("\n  pricing model:")
    for v, c in top_counter([field_val(r, "pricing_model") for r in recs]):
        print(f"    {str(v):20s} {c:4d}  {pct(c, n)}")
    print("\n  extraction confidence:")
    for v, c in top_counter([r.get("confidence") for r in recs]):
        print(f"    {str(v):20s} {c:4d}  {pct(c, n)}")


def main():
    classified = load(CLASSIFIED)
    census = load(CENSUS)
    print("STATE OF THE API ECONOMY — API Census proof-engine report")
    if classified:
        corpus_section(classified)
    else:
        print("\n(no seed_classified.jsonl — run classify.py first)")
    extracted_section(census)
    print(f"\n{'='*60}")
    print("headline (paste-ready): of the API domains we census, "
          f"{pct(sum(1 for r in classified if r.get('llms_txt')), len(classified)).strip()} "
          "publish an llms.txt and "
          f"{pct(sum(1 for r in classified if r.get('spec_url') or r.get('openapi_probe')), len(classified)).strip()} "
          "an OpenAPI spec.")


if __name__ == "__main__":
    main()
