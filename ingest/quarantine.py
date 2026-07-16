#!/usr/bin/env python3
"""Self-healing QA for the autonomous freshness cycle.

`qa.py` is the strict gate — it exits non-zero on any critical flag, which is right for a
manual run where a human is watching. But an unattended weekly cron can't just stop and wait.
This makes the cycle self-healing: any record that FAILS QA after a re-fill is reverted to its
last-known-good version (the committed git HEAD) and logged to data/quarantine.jsonl for human
review. Clean re-fills are kept; a brand-new record that fails is dropped (also logged).

Net effect: the cycle never publishes bad data and never blocks. A human empties the review
queue whenever convenient — not on the pipeline's schedule.

  python3 ingest/quarantine.py     # run after `fill --only`, before changelog/qa/generate

Runs in the freshness cycle as: refill -> QUARANTINE -> changelog diff -> qa (now clean) ->
generate. Because bad refills are reverted to HEAD *before* the changelog diff, a reverted
record equals HEAD and correctly produces no change event.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qa import audit  # single source of truth for what counts as a critical flag

ROOT = Path(__file__).resolve().parent.parent
CENSUS = ROOT / "data" / "census.jsonl"
QUARANTINE = ROOT / "data" / "quarantine.jsonl"


def head_census() -> dict:
    """Last-known-good census from git HEAD, keyed by domain. Empty if unavailable."""
    try:
        out = subprocess.check_output(["git", "show", "HEAD:data/census.jsonl"],
                                      cwd=str(ROOT), text=True)
    except subprocess.CalledProcessError:
        return {}
    good = {}
    for line in out.splitlines():
        if line.strip():
            r = json.loads(line)
            good[r["domain"]] = r
    return good


def main():
    if not CENSUS.exists():
        sys.exit("census.jsonl not found.")
    recs = [json.loads(l) for l in CENSUS.open()]
    head = head_census()
    today = time.strftime("%Y-%m-%d")

    kept, reverted, dropped, events = [], [], [], []
    for rec in recs:
        crit = audit(rec)["critical"]
        if not crit:
            kept.append(rec)
            continue
        dom = rec["domain"]
        if dom in head:                      # revert the bad refill to last-known-good
            kept.append(head[dom])
            reverted.append(dom)
            action = "reverted_to_last_good"
        else:                                # brand-new record that failed — don't publish
            dropped.append(dom)
            action = "dropped_new"
        events.append({"domain": dom, "detected": today, "action": action,
                       "flags": crit, "bad_record": rec})

    if events:
        CENSUS.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + "\n")
        with QUARANTINE.open("a") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    print(f"quarantine: {len(kept)} kept, {len(reverted)} reverted to last-good, "
          f"{len(dropped)} new-and-dropped")
    if events:
        print(f"  ⚠ {len(events)} record(s) held for review -> data/quarantine.jsonl")
        for e in events[:10]:
            print(f"    {e['domain']}: {e['action']} — {'; '.join(e['flags'])[:80]}")
    else:
        print("  all refills clean — nothing quarantined.")
    # exit 0 always: self-healing means the cycle continues. The review queue is the signal.


if __name__ == "__main__":
    main()
