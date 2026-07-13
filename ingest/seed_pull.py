#!/usr/bin/env python3
"""S1: build the seed census candidate list from the two free instant sources.

Sources:
  1. apis.guru list.json   — 2.5k providers with OpenAPI specs (frozen 2023, but the
                             provider list itself is a valid seed of real APIs)
  2. public-apis README    — ~1.6k community-listed public APIs with category + auth hints

Output: data/seed.jsonl — one record per registered domain:
  {domain, name, description, category, auth_hint, spec_url, spec_count, sources[]}

Zero dependencies. Polite: two GETs total.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "seed.jsonl"
UA = "apicensus-seed/0.1 (+https://apiterms.com)"

# rough public-suffix handling: enough for dedupe keys, not for display
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def registered_domain(host: str) -> str:
    host = host.lower().strip().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def pull_apisguru() -> dict:
    """provider-key -> record. Collapses multi-service providers (azure.com: 1829 specs)."""
    data = json.loads(fetch("https://api.apis.guru/v2/list.json"))
    out = {}
    for key, entry in data.items():
        provider = key.split(":", 1)[0]
        dom = registered_domain(provider)
        pref = entry.get("preferred")
        info = entry.get("versions", {}).get(pref, {}).get("info", {})
        swagger = entry.get("versions", {}).get(pref, {}).get("swaggerUrl")
        rec = out.setdefault(dom, {
            "domain": dom, "name": None, "description": None, "category": None,
            "auth_hint": None, "spec_url": None, "spec_count": 0,
            "sources": ["apis.guru"],
        })
        rec["spec_count"] += len(entry.get("versions", {}))
        # keep the first (usually only) service's metadata for single-service providers
        if rec["name"] is None:
            rec["name"] = info.get("title") or provider
            desc = (info.get("description") or "").strip()
            rec["description"] = re.sub(r"\s+", " ", desc)[:300] or None
            cats = info.get("x-apisguru-categories") or []
            rec["category"] = cats[0] if cats else None
            rec["spec_url"] = swagger
    return out


ROW_RE = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)]+)\)\s*\|([^|]*)\|([^|]*)\|")
HDR_RE = re.compile(r"^###\s+(.+)")


def pull_publicapis() -> dict:
    md = fetch(
        "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
    ).decode("utf-8", "replace")
    out, category = {}, None
    for line in md.splitlines():
        m = HDR_RE.match(line)
        if m:
            category = m.group(1).strip()
            continue
        m = ROW_RE.match(line)
        if not m or category is None:
            continue
        name, url, desc, auth = (g.strip() for g in m.groups())
        if name.lower() == "api":  # table header row
            continue
        host = urlparse(url).netloc
        if not host:
            continue
        dom = registered_domain(host)
        out.setdefault(dom, {
            "domain": dom, "name": name,
            "description": desc or None, "category": category,
            "auth_hint": auth.strip("`") or None,
            "spec_url": None, "spec_count": 0,
            "sources": ["public-apis"],
        })
    return out


def main() -> None:
    guru = pull_apisguru()
    pub = pull_publicapis()
    merged = dict(guru)
    overlap = 0
    for dom, rec in pub.items():
        if dom in merged:
            overlap += 1
            m = merged[dom]
            m["sources"].append("public-apis")
            m["auth_hint"] = m["auth_hint"] or rec["auth_hint"]
            m["category"] = m["category"] or rec["category"]
            m["description"] = m["description"] or rec["description"]
        else:
            merged[dom] = rec
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        for dom in sorted(merged):
            f.write(json.dumps(merged[dom], ensure_ascii=False) + "\n")
    print(f"apis.guru providers (by domain): {len(guru)}")
    print(f"public-apis entries (by domain): {len(pub)}")
    print(f"overlap: {overlap}")
    print(f"seed total: {len(merged)}  -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
