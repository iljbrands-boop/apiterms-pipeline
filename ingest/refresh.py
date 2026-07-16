#!/usr/bin/env python3
"""The snapshot clock — freshness layer 1 (no API key needed).

apis.guru died of staleness; freshness is the product. This is the cheap,
key-free half of the scheduled freshness loop: re-fetch each published record's evidence
pages, compare against the crawl snapshot they were extracted from, and emit change
candidates. Only records whose *source pages actually changed* need the expensive LLM
re-extraction — this is what keeps the re-fill bill small AND powers the change feed.

  python3 ingest/refresh.py            # check every record in data/census.jsonl

Outputs:
  data/changes.jsonl            append-only log: one event per changed/unreachable page
  data/needs_reextraction.txt   domains whose sources changed → feed to `extract.py fill`
  (prints a summary; census.jsonl itself is left authoritative and untouched)

Change signal is normalized-text hash mismatch on an evidence page (whitespace collapsed
so trivial reflows don't churn). A hash mismatch means "worth re-extracting", not
"pricing definitely changed" — the definitive per-field value diff comes from layer 2
(the LLM re-fill comparing new values to stored ones). Kept separate so this runs for $0.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

import extract  # reuse get() + strip_html() — no key needed at import

ROOT = Path(__file__).resolve().parent.parent
CENSUS = ROOT / "data" / "census.jsonl"
PAGES = ROOT / "data" / "pages"
CHANGES = ROOT / "data" / "changes.jsonl"
WORKLIST = ROOT / "data" / "needs_reextraction.txt"

_WS = re.compile(r"\s+")


def norm_hash(text: str) -> str:
    """Hash of whitespace-collapsed text — ignores reflow/indentation churn."""
    return hashlib.sha256(_WS.sub(" ", text).strip().encode("utf-8", "replace")).hexdigest()


def stored_pages(domain: str) -> dict:
    f = PAGES / domain / "pages.json"
    if not f.exists():
        return {}
    return json.loads(f.read_text()).get("pages", {})


def refetch_text(url: str) -> str | None:
    final, ctype, body = extract.get(url)
    if body is None:
        return None
    if url.endswith((".txt", ".md")) or "text/plain" in (ctype or ""):
        return body[:extract.MAX_TEXT_PER_PAGE]
    return extract.strip_html(body)


def write_snapshot(domain: str, pages: dict):
    f = PAGES / domain / "pages.json"
    if not f.exists():
        return
    data = json.loads(f.read_text())
    data["pages"] = pages
    data["refreshed_at"] = time.strftime("%Y-%m-%d")
    f.write_text(json.dumps(data, ensure_ascii=False))


def check_record(rec: dict) -> dict:
    dom = rec["domain"]
    snap = stored_pages(dom)
    pages = rec.get("evidence_pages") or list(snap)
    events, changed, unreachable = [], 0, 0
    today = time.strftime("%Y-%m-%d")
    updated = dict(snap)  # collect fresh content so a refill re-reads the NEW pages

    for url in pages:
        old = snap.get(url)
        new = refetch_text(url)
        if new is None:
            unreachable += 1
            events.append({"domain": dom, "url": url, "change": "unreachable",
                           "detected_at": today})
            continue
        if old is None:
            # page wasn't in the snapshot (e.g. evidence added post-crawl) — record baseline
            continue
        if norm_hash(old) != norm_hash(new):
            changed += 1
            updated[url] = new  # refresh the stored snapshot for this page
            events.append({"domain": dom, "url": url, "change": "content_changed",
                           "detected_at": today})
        time.sleep(0.2)

    # persist the refreshed snapshot only when something actually changed, so
    # `extract.py fill --only` re-extracts from the NEW content, not the stale copy
    if changed:
        write_snapshot(dom, updated)

    return {"domain": dom, "changed": changed, "unreachable": unreachable, "events": events}


def main():
    if not CENSUS.exists():
        sys.exit(f"{CENSUS} not found — nothing published to refresh yet. "
                 "Run `extract.py fill` first; this becomes the scheduled job once records exist.")
    recs = [json.loads(l) for l in CENSUS.open()]
    if not recs:
        sys.exit("census.jsonl is empty — nothing to refresh.")

    print(f"snapshot clock: checking {len(recs)} records against their crawl snapshots\n")
    all_events, need = [], []
    stale = fresh = 0
    for rec in recs:
        res = check_record(rec)
        all_events += res["events"]
        if res["changed"] or res["unreachable"]:
            stale += 1
            need.append(res["domain"])
            print(f"  ~ {res['domain']}: {res['changed']} changed, "
                  f"{res['unreachable']} unreachable")
        else:
            fresh += 1

    if all_events:
        with CHANGES.open("a") as f:
            for e in all_events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    WORKLIST.write_text("\n".join(need) + ("\n" if need else ""))

    print(f"\nsummary: {fresh} unchanged, {stale} with changed sources "
          f"({len(all_events)} page events → data/changes.jsonl)")
    print(f"re-extraction worklist: {len(need)} domains → data/needs_reextraction.txt")
    if need:
        print("next: LLM re-fill only these (layer 2) to produce per-field value diffs.")


if __name__ == "__main__":
    main()
