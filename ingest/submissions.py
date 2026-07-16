#!/usr/bin/env python3
"""Auto-onboard community-submitted API domains into the extraction queue.

This is what makes the /add flow *automatic*: any domain that lands in
data/submissions.txt (from the website form review, or an OSS seed_domains PR) is
probed and appended to the extraction queue here — the weekly freshness cron then
crawls + fills + QA-gates + publishes it, and from that point it's tracked like every
other record. Contributors expand COVERAGE (a domain); the pipeline still produces
every field value with an evidence URL, so the evidence-or-null moat holds.

  data/submissions.txt   one domain per line; blank lines and #-comments ignored.
                         Reviewed before landing here (spam/cost gate), then automatic.

Behaviour mirrors add_domains.py: probes llms.txt + /openapi.json, appends to
seed_classified.jsonl + extract_queue.jsonl (source "submission"), skips anything
already in the census or the queue. Processed lines are moved to
data/submissions_processed.txt so a domain is never onboarded twice. Zero deps.

Exit 0 always (a bad line is skipped, never fatal) so it's safe as cron step 0.
Prints "QUEUED n" on the last line so the cron can decide whether to crawl this run.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CENSUS = DATA / "census.jsonl"
CLASSIFIED = DATA / "seed_classified.jsonl"
QUEUE = DATA / "extract_queue.jsonl"
INBOX = DATA / "submissions.txt"
DONE = DATA / "submissions_processed.txt"


def norm(domain: str) -> str:
    """Bare host: strip scheme, path, leading www, whitespace, lowercase."""
    d = domain.strip().lower()
    if not d or d.startswith("#"):
        return ""
    d = d.split("//")[-1]          # drop scheme
    d = d.split("/")[0]            # drop path
    d = d.split("?")[0].split("#")[0]
    if d.startswith("www."):
        d = d[4:]
    return d


def probe(url, timeout=10):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "apiterms-census/1.0 (+https://apiterms.com)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status == 200 and len(r.read(2048)) > 100:
                return url
    except Exception:
        pass
    return None


def existing_domains() -> set:
    have = set()
    for p in (CENSUS, QUEUE):
        if p.exists():
            for line in p.open():
                line = line.strip()
                if not line:
                    continue
                try:
                    have.add(json.loads(line)["domain"])
                except Exception:
                    continue
    return have


def main():
    if not INBOX.exists():
        print("no data/submissions.txt — nothing to onboard.")
        print("QUEUED 0")
        return 0

    raw = [l for l in INBOX.read_text().splitlines()]
    have = existing_domains()
    processed, queued = [], 0
    seen_this_run = set()
    keep_lines = []  # comments / blank lines are preserved; domain lines are consumed

    for line in raw:
        dom = norm(line)
        if not dom:
            keep_lines.append(line)   # header comments, blanks — keep the file documented
            continue
        if dom in seen_this_run:
            continue
        seen_this_run.add(dom)
        if dom in have:
            print(f"{dom}: already covered/queued, skip")
            processed.append(dom)
            continue

        rec = {
            "domain": dom, "name": dom, "description": None, "category": None,
            "auth_hint": None, "spec_url": None, "spec_count": 0,
            "sources": ["submission"], "alive": True,
            "llms_txt": probe(f"https://{dom}/llms.txt"),
            "openapi_probe": probe(f"https://{dom}/openapi.json"),
            "queue_rank": 9999,
        }
        with CLASSIFIED.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with QUEUE.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{dom}: queued (llms.txt={'yes' if rec['llms_txt'] else 'no'})")
        processed.append(dom)
        queued += 1

    # move everything we handled to the processed log; rewrite the inbox keeping only
    # its comments/blank structure (so it stays self-documenting for contributors).
    if processed:
        with DONE.open("a") as f:
            for d in processed:
                f.write(d + "\n")
        INBOX.write_text("\n".join(keep_lines) + ("\n" if keep_lines else ""))

    print(f"done: {queued} newly queued, {len(processed)} processed")
    print(f"QUEUED {queued}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
