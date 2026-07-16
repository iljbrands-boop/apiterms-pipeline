#!/usr/bin/env python3
"""Freshness layer 2 — the value-diff engine.

Layer 1 (refresh.py) answers "did this source page change." This answers the question
that is actually the product: "did the TERMS change, and to what." It diffs two census
snapshots — the record values before a refill vs. after — and appends structured,
evidenced change events to data/changelog.jsonl. That ledger is the moat: apis.guru died
of invisible staleness; nobody else keeps snapshots of API terms, so nobody else can
produce this event stream.

  python3 ingest/changelog.py diff <before.jsonl> <after.jsonl>
        append value-change events for every field that changed between the two snapshots

  python3 ingest/changelog.py baseline
        snapshot the current census as data/census_prev.jsonl (the reference for next diff)

Event shape (data/changelog.jsonl, append-only):
  {"domain","field","old","new","kind","detected","evidence_url","significance"}
  kind: added (null->value) | removed (value->null) | changed (value->value)
  significance: pricing | limits | auth | spec | mcp | info   (derived from field)

Jitter control (spec §6): values are normalized (case/whitespace/trailing punctuation)
before comparison, so an identical fact re-phrased by the extractor does not emit an event.
Only records that passed a page-change check (layer 1) should be refilled, so free-text
churn is already bounded; the qa.py sample is the final guard before anything publishes.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CENSUS = ROOT / "data" / "census.jsonl"
PREV = ROOT / "data" / "census_prev.jsonl"
CHANGELOG = ROOT / "data" / "changelog.jsonl"

FIELDS = ["base_url", "auth_type", "free_tier", "pricing_model", "pricing_details",
          "rate_limits", "openapi_spec_url", "mcp_server"]
SIGNIFICANCE = {
    "auth_type": "auth", "pricing_model": "pricing", "pricing_details": "pricing",
    "free_tier": "pricing", "rate_limits": "limits", "openapi_spec_url": "spec",
    "mcp_server": "mcp", "base_url": "info",
}
_WS = re.compile(r"\s+")


def val(rec, f):
    o = rec.get(f)
    return o.get("value") if isinstance(o, dict) else None


def ev(rec, f):
    o = rec.get(f)
    return o.get("evidence_url") if isinstance(o, dict) else None


def norm(v):
    """Normalize a value for jitter-resistant comparison. None stays None."""
    if v is None:
        return None
    return _WS.sub(" ", str(v).strip().lower()).rstrip(".;,")


def load_by_domain(path: Path) -> dict:
    return {json.loads(l)["domain"]: json.loads(l) for l in path.open()}


def diff_record(old_rec: dict, new_rec: dict) -> list:
    """Emit one event per field whose normalized value changed."""
    dom = new_rec["domain"]
    today = time.strftime("%Y-%m-%d")
    events = []
    for f in FIELDS:
        ov, nv = val(old_rec, f), val(new_rec, f)
        if norm(ov) == norm(nv):
            continue
        kind = "added" if ov is None else "removed" if nv is None else "changed"
        events.append({
            "domain": dom, "field": f,
            "old": ov, "new": nv, "kind": kind,
            "detected": today,
            "evidence_url": ev(new_rec, f) if nv is not None else ev(old_rec, f),
            "significance": SIGNIFICANCE.get(f, "info"),
        })
    return events


def cmd_diff(before_path: str, after_path: str):
    before = load_by_domain(Path(before_path))
    after = load_by_domain(Path(after_path))
    all_events, changed_domains = [], set()
    for dom, new_rec in after.items():
        old_rec = before.get(dom)
        if old_rec is None:
            continue  # brand-new record, not a change (first appearance != event)
        evs = diff_record(old_rec, new_rec)
        if evs:
            changed_domains.add(dom)
            all_events += evs

    if all_events:
        with CHANGELOG.open("a") as f:
            for e in all_events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    by_sig = {}
    for e in all_events:
        by_sig[e["significance"]] = by_sig.get(e["significance"], 0) + 1
    print(f"changelog: {len(all_events)} value change(s) across {len(changed_domains)} API(s)")
    if by_sig:
        print("  by significance: " + ", ".join(f"{k}={v}" for k, v in sorted(by_sig.items())))
        for e in all_events[:12]:
            o = "null" if e["old"] is None else f'"{str(e["old"])[:40]}"'
            nw = "null" if e["new"] is None else f'"{str(e["new"])[:40]}"'
            print(f"  {e['domain']} {e['field']}: {o} -> {nw}  [{e['kind']}]")
        if len(all_events) > 12:
            print(f"  … +{len(all_events) - 12} more")
    else:
        print("  no value changes — sources may have changed wording only, or nothing moved.")
    print(f"-> appended to {CHANGELOG}")


def cmd_baseline():
    if not CENSUS.exists():
        sys.exit("census.jsonl not found.")
    PREV.write_text(CENSUS.read_text())
    n = sum(1 for _ in PREV.open())
    print(f"baseline snapshot: {n} records -> {PREV.name} (reference for the next diff)")


def cmd_diff_git():
    """Diff the committed census.jsonl (git HEAD = last week's published state) against
    the current working tree. Git is the baseline store — no separate snapshot to manage,
    and the change ledger is reproducible from history. This is the automation path."""
    try:
        head = subprocess.check_output(["git", "show", "HEAD:data/census.jsonl"],
                                       cwd=str(ROOT), text=True)
    except subprocess.CalledProcessError:
        sys.exit("could not read HEAD:data/census.jsonl — is census.jsonl committed?")
    tmp = ROOT / "data" / "_head_census.jsonl"
    tmp.write_text(head)
    try:
        cmd_diff(str(tmp), str(CENSUS))
    finally:
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "diff"
    if cmd == "diff":
        b = sys.argv[2] if len(sys.argv) > 2 else str(PREV)
        a = sys.argv[3] if len(sys.argv) > 3 else str(CENSUS)
        cmd_diff(b, a)
    elif cmd == "diff-git":
        cmd_diff_git()
    elif cmd == "baseline":
        cmd_baseline()
    else:
        sys.exit(f"unknown command: {cmd} (use: diff-git | diff <before> <after> | baseline)")
