#!/usr/bin/env python3
"""The snapshot clock — freshness layer 1 (no API key needed).

apis.guru died of staleness; freshness is the product. This is the cheap,
key-free half of the weekly freshness loop: re-fetch each published record's evidence
pages, compare their TERMS-RELEVANT content against the committed signature manifest,
and emit change candidates. Only records whose access terms plausibly changed get the
expensive LLM re-extraction — that keeps the re-fill bill small AND powers the change feed.

  python3 ingest/refresh.py                  # check every record in data/census.jsonl
  python3 ingest/refresh.py --init-manifest  # (re)build the manifest from local page
                                             # snapshots, no network — the CI baseline

State model (CI-friendly):
  data/page_signatures.json   COMMITTED — {domain: {url: terms_sig}}. ~64 bytes per page
                              instead of the full text, so the weekly GitHub Actions run
                              can detect changes without the 31MB data/pages/ store.
  data/pages/<dom>/pages.json local working store. When a changed record is queued for
                              refill, the fresh page text fetched during detection is
                              written here (created if absent — e.g. on CI) so
                              `extract.py fill --only` re-extracts from the NEW content.

Outputs:
  data/changes.jsonl            append-only log: one event per changed/unreachable page
  data/needs_reextraction.txt   domains whose terms changed → feed to `extract.py fill`
  (census.jsonl itself is left authoritative and untouched)

Cost guard: REFILL_CAP (default 40) bounds how many records a cycle queues for the paid
refill. Deferred records keep their OLD manifest signature, so they re-flag next cycle —
a change is delayed under burst load, never lost.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import extract  # reuse get() + strip_html() — no key needed at import

ROOT = Path(__file__).resolve().parent.parent
CENSUS = ROOT / "data" / "census.jsonl"
PAGES = ROOT / "data" / "pages"
MANIFEST = ROOT / "data" / "page_signatures.json"
CHANGES = ROOT / "data" / "changes.jsonl"
WORKLIST = ROOT / "data" / "needs_reextraction.txt"

# Hard ceiling on how many records a single cycle will queue for the (paid) LLM refill.
# Detection is a cheap gate; refills cost ~$0.09 each. Even if far more pages churn, we
# only refill this many per cycle and the rest roll to a later cycle — the bill is
# bounded no matter how noisy the web is. Override with REFILL_CAP env var.
REFILL_CAP = int(os.environ.get("REFILL_CAP", "40"))

_WS = re.compile(r"\s+")

# Volatile page content that changes between fetches without any terms changing — strip it
# before comparing so timestamps / CSRF tokens / build hashes never trigger a paid refill.
_VOLATILE = [
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), " "),                     # hex tokens/nonces/hashes
    (re.compile(r"\b\d{4}-\d{2}-\d{2}([t ]\d{2}:\d{2}(:\d{2})?z?)?\b", re.I), " "),  # ISO datetimes
    (re.compile(r"\b\d{9,}\b"), " "),                                  # epochs / long numeric ids
    (re.compile(r"\b(19|20)\d{2}\b"), " "),                            # bare years (© 2026 etc.)
    (re.compile(r"csrf[-_]?token\S*", re.I), " "),
    (re.compile(r"nonce[=:\"']\s*[\w-]+", re.I), " "),
]

# A line only matters to us if it plausibly carries access terms — pricing, free tier,
# rate limits, or auth. Diffing ONLY these lines is what turns "the page changed" (noise)
# into "the terms plausibly changed" (worth paying to re-verify). The authoritative
# per-field diff still happens in layer 2 (changelog.py) after the refill.
_TERMS_KW = re.compile(
    r"(pric|\$|€|£|per\s*(month|year|call|request|seat|user)|/mo\b|/month|/year"
    r"|free\s*(tier|plan)|\btier\b|rate\s*limit|throttl|quota|\breq(uest|uests|s)?\b"
    r"|/s\b|/sec|per\s*second|\btoken(s)?\b|api[\s-]*key|oauth|bearer|\bauth\b|billing"
    r"|\bplan\b|\b429\b|usage|credit|subscription|monthly|annual|overage)", re.I)


def terms_sig(text: str) -> str:
    """Signature of only the terms-relevant, volatile-stripped content of a page.

    A page whose pricing / free-tier / rate-limit / auth lines are unchanged yields the
    SAME signature even if its marketing copy, timestamps, or CSRF tokens churn — so we
    only flag (and pay to refill) records whose access terms plausibly moved. If a page
    has no terms-relevant lines at all, its signature is constant (never flags)."""
    joined = "\n".join(ln for ln in text.splitlines() if _TERMS_KW.search(ln))
    for rx, sub in _VOLATILE:
        joined = rx.sub(sub, joined)
    return hashlib.sha256(_WS.sub(" ", joined).strip().encode("utf-8", "replace")).hexdigest()


# --------------------------------------------------------------------------- state
def load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except Exception:
            pass
    return {}


def save_manifest(man: dict) -> None:
    MANIFEST.write_text(json.dumps(man, ensure_ascii=False, indent=0, sort_keys=True) + "\n")


def stored_pages(domain: str) -> dict:
    f = PAGES / domain / "pages.json"
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text()).get("pages", {})
    except Exception:
        return {}


def write_pages(domain: str, pages: dict) -> None:
    """Persist fresh page text so `extract.py fill --only` re-reads the NEW content.
    Creates the store if absent (the CI case, where data/pages/ isn't in the repo)."""
    outdir = PAGES / domain
    outdir.mkdir(parents=True, exist_ok=True)
    f = outdir / "pages.json"
    data = {"domain": domain, "crawled_at": time.strftime("%Y-%m-%d"), "pages": {}}
    if f.exists():
        try:
            data = json.loads(f.read_text())
        except Exception:
            pass
    data["pages"] = {**data.get("pages", {}), **pages}
    data["refreshed_at"] = time.strftime("%Y-%m-%d")
    f.write_text(json.dumps(data, ensure_ascii=False))


def refetch_text(url: str) -> str | None:
    final, ctype, body = extract.get(url)
    if body is None:
        return None
    if url.endswith((".txt", ".md")) or "text/plain" in (ctype or ""):
        return body[:extract.MAX_TEXT_PER_PAGE]
    return extract.strip_html(body)


# --------------------------------------------------------------------------- checking
def check_record(rec: dict, man_dom: dict) -> dict:
    """Fetch a record's evidence pages and compare terms signatures to the manifest.

    Returns fresh signatures + fresh text; the caller decides whether to adopt them
    (queued or baseline) or discard (deferred past the refill cap)."""
    dom = rec["domain"]
    pages = rec.get("evidence_pages") or list(man_dom)
    events, changed, unreachable, baselined = [], 0, 0, 0
    fresh_sigs, fresh_text = {}, {}
    today = time.strftime("%Y-%m-%d")

    for url in pages:
        new = refetch_text(url)
        if new is None:
            unreachable += 1
            events.append({"domain": dom, "url": url, "change": "unreachable",
                           "detected_at": today})
            continue
        sig = terms_sig(new)
        fresh_sigs[url] = sig
        fresh_text[url] = new
        old = man_dom.get(url)
        if old is None:
            baselined += 1          # first sighting — adopt silently, never flag
        elif old != sig:
            changed += 1
            events.append({"domain": dom, "url": url, "change": "content_changed",
                           "detected_at": today})
        time.sleep(0.2)

    return {"domain": dom, "changed": changed, "unreachable": unreachable,
            "baselined": baselined, "events": events,
            "fresh_sigs": fresh_sigs, "fresh_text": fresh_text}


def init_manifest() -> int:
    """Build the signature manifest from the local page snapshots — no network. This is
    the one-time baseline (run where data/pages/ exists), committed so CI can detect."""
    if not PAGES.exists():
        sys.exit("data/pages/ not found — the baseline must be built where snapshots exist.")
    man, n_dom, n_url = {}, 0, 0
    for pj in sorted(PAGES.glob("*/pages.json")):
        dom = pj.parent.name
        pages = stored_pages(dom)
        if not pages:
            continue
        man[dom] = {url: terms_sig(text) for url, text in pages.items()}
        n_dom += 1
        n_url += len(pages)
    save_manifest(man)
    print(f"manifest built: {n_dom} domains, {n_url} page signatures -> {MANIFEST.name} "
          f"({MANIFEST.stat().st_size // 1024} KB)")
    return 0


def main():
    if "--init-manifest" in sys.argv[1:]:
        return init_manifest()

    if not CENSUS.exists():
        sys.exit(f"{CENSUS} not found — nothing published to refresh yet.")
    recs = [json.loads(l) for l in CENSUS.open()]
    if not recs:
        sys.exit("census.jsonl is empty — nothing to refresh.")
    man = load_manifest()
    if not man:
        sys.exit("data/page_signatures.json missing/empty — run `refresh.py --init-manifest` "
                 "where data/pages/ exists and commit the manifest first.")

    print(f"snapshot clock: checking {len(recs)} records against {sum(len(v) for v in man.values())} "
          f"page signatures (refill cap {REFILL_CAP}/cycle)\n")
    all_events, need = [], []
    fresh = changed_total = deferred = unreachable_only = baselined_total = 0

    for rec in recs:
        dom = rec["domain"]
        res = check_record(rec, man.get(dom, {}))
        all_events += res["events"]
        baselined_total += res["baselined"]

        if res["changed"]:
            changed_total += 1
            if len(need) < REFILL_CAP:
                # queue for refill: stage the fresh page text for extract.py and adopt
                # the fresh signatures so this change isn't re-flagged next cycle.
                write_pages(dom, res["fresh_text"])
                man.setdefault(dom, {}).update(res["fresh_sigs"])
                need.append(dom)
                print(f"  ~ {dom}: {res['changed']} terms page(s) changed → queued")
            else:
                deferred += 1  # keep the OLD signature so it re-flags in a later cycle
        else:
            # no change: adopt any first-sighting baselines (and refreshed sigs are
            # identical by definition, so this is a no-op for known pages)
            if res["fresh_sigs"]:
                man.setdefault(dom, {}).update(res["fresh_sigs"])
            if res["unreachable"]:
                # temporarily-unreachable pages are logged for review but NOT refilled —
                # a refill against a dead page would only risk nulling good data.
                unreachable_only += 1
                print(f"  ! {dom}: {res['unreachable']} page(s) unreachable (logged, not refilled)")
            else:
                fresh += 1

    save_manifest(man)
    if all_events:
        with CHANGES.open("a") as f:
            for e in all_events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    WORKLIST.write_text("\n".join(need) + ("\n" if need else ""))

    print(f"\nsummary: {fresh} unchanged, {changed_total} with changed terms, "
          f"{unreachable_only} unreachable-only, {baselined_total} pages baselined "
          f"({len(all_events)} page events → data/changes.jsonl)")
    print(f"re-extraction worklist: {len(need)} domains → data/needs_reextraction.txt")
    if deferred:
        print(f"NOTE: {deferred} more changed but held back by the {REFILL_CAP}/cycle refill "
              f"cap (cost guard) — they re-flag and get picked up in a later cycle.")
    if need:
        print("next: LLM re-fill only these (layer 2) to produce per-field value diffs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
