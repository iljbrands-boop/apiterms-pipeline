#!/usr/bin/env python3
"""Proactive discovery — find newly launched APIs before anyone asks us to add them.

The SEO wedge: a brand-new company's API has zero search competition.
Whoever publishes the first structured terms page owns that SERP when the volume arrives,
and our change history starts from the company's day zero — data nobody can backfill.

  python3 ingest/discover.py            # scan, probe, queue top candidates
  DISCOVER_CAP=5 python3 ingest/discover.py

v1 source: the Hacker News Algolia API (token-free) — Show HN / Launch HN posts are where
new developer products announce. Candidate domains are probed for real API signals
(llms.txt, openapi.json, /docs, /pricing, "API" on the homepage) and only scored keepers
enter the pipeline. Product Hunt (needs a token) is a natural v2 source.

Flow: candidates -> probe/score -> dedup vs census + queue + processed + inbox ->
append top N to data/submissions.txt -> the SAME weekly cron path as human submissions
(crawl -> evidence-linked fill -> QA -> publish -> tracked weekly). Cost is bounded:
DISCOVER_CAP (default 10/wk) x ~$0.09/fill, and refresh_cycle's SUB_CAP still caps total
onboarding per cycle. Every accept/reject is logged to data/discovery_log.jsonl.

Zero deps, never fatal: any source failing just means fewer candidates this week.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CENSUS = DATA / "census.jsonl"
QUEUE = DATA / "extract_queue.jsonl"
INBOX = DATA / "submissions.txt"
DONE = DATA / "submissions_processed.txt"
REJECTS = DATA / "discovery_rejects.txt"   # hand-reviewed wrong-entity bans, committed
LOG = DATA / "discovery_log.jsonl"

CAP = int(os.environ.get("DISCOVER_CAP", "10"))
LOOKBACK_DAYS = int(os.environ.get("DISCOVER_LOOKBACK_DAYS", "8"))
UA = "apiterms-discover/1.0 (+https://apiterms.com)"

# Aggregator/platform hosts that are never the API vendor itself.
PLATFORM = {
    "github.com", "gitlab.com", "bitbucket.org", "npmjs.com", "pypi.org", "crates.io",
    "huggingface.co", "medium.com", "substack.com", "dev.to", "hashnode.dev",
    "youtube.com", "youtu.be", "twitter.com", "x.com", "reddit.com", "linkedin.com",
    "apps.apple.com", "play.google.com", "chromewebstore.google.com", "producthunt.com",
    "kickstarter.com", "itch.io", "arxiv.org", "wikipedia.org", "notion.site",
    "docs.google.com", "google.com", "chrome.com", "apple.com", "microsoft.com", "vercel.app", "netlify.app", "pages.dev",
    "herokuapp.com", "streamlit.app", "figma.com", "loom.com", "discord.gg",
    "discord.com", "t.me", "apiterms.com",
}
# Multi-label public suffixes we should keep three labels for.
TWO_LEVEL_TLDS = {"co.uk", "com.au", "co.jp", "co.in", "com.br", "co.nz"}


_POOL = None  # lazy thread pool: hard wall-clock cap per fetch, DNS included


def fetch(url: str, timeout: int = 12, limit: int = 40_000) -> tuple[int, bytes]:
    """limit caps homepage/probe reads; source-API calls pass limit=0 for the full body.

    Runs the request in a worker thread with a HARD deadline: urlopen's own timeout
    covers socket reads but NOT DNS resolution, and guessed NXDOMAIN hosts can hang
    getaddrinfo for minutes (a long backfill once wedged for hours on exactly this)."""
    global _POOL
    import concurrent.futures
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=16)

    def _do():
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, (r.read() if not limit else r.read(limit))
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            return 0, b""

    try:
        return _POOL.submit(_do).result(timeout=timeout + 5)
    except Exception:
        return 0, b""  # deadline hit (stuck DNS etc.) — treat as dead


def registrable(host: str) -> str:
    """Cheap registrable-domain heuristic: last two labels (three for co.uk-style)."""
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


# --------------------------------------------------------------------------- sources
def source_hn() -> list[dict]:
    """Show HN / Launch HN posts from the lookback window whose title smells like an API
    or agent-tool launch. Algolia HN API, no token."""
    since = int(time.time()) - LOOKBACK_DAYS * 86400
    out = []
    for query in ("API", "MCP"):
        url = ("https://hn.algolia.com/api/v1/search_by_date?"
               + urllib.parse.urlencode({
                   "query": query, "tags": "show_hn",
                   "numericFilters": f"created_at_i>{since}",
                   "hitsPerPage": 100}))
        status, body = fetch(url, limit=0)
        if status != 200:
            continue
        try:
            hits = json.loads(body).get("hits", [])
        except Exception:
            continue
        for h in hits:
            u, title = h.get("url"), h.get("title") or ""
            if not u:
                continue
            if not re.search(r"\bAPI\b|\bMCP\b|developer|SDK", title, re.I):
                continue
            host = urllib.parse.urlsplit(u).hostname or ""
            if not host:
                continue
            out.append({"domain": registrable(host), "via": "hn_show",
                        "title": title, "url": u})
        time.sleep(0.3)
    return out


GUESS_TLDS = (".com", ".io", ".dev", ".ai", ".app")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def resolve_via_company_api(name: str, label: str) -> list[str]:
    """Company name -> domain via keyless brand APIs (name->domain lookup). Brandfetch search first (best hit rate), Clearbit autocomplete
    second. Strict name match so 'Theneo' can't resolve to The Neon Museum."""
    out = []
    for url in (f"https://api.brandfetch.io/v2/search/{urllib.parse.quote(name)}",
                f"https://autocomplete.clearbit.com/v1/companies/suggest?query={urllib.parse.quote(name)}"):
        status, body = fetch(url, limit=0)
        if status != 200:
            continue
        try:
            rows = json.loads(body)
        except Exception:
            continue
        for r in rows if isinstance(rows, list) else []:
            rname, dom = _norm(r.get("name")), (r.get("domain") or "").lower()
            if not dom or dom in PLATFORM or dom in out:
                continue
            # the API result's name must BE the product name (not a fuzzy cousin)
            if rname == label or (len(label) >= 5 and (label in rname or rname in label)):
                out.append(registrable(dom))
            if len(out) >= 2:
                return out
    return out


def ph_candidates(name: str, slug: str, title: str, via: str) -> list[dict]:
    """All domain candidates for one PH product: resolved (company APIs, then HN
    search) first — they're real lookups — then name.tld guesses as the fallback.
    Shared label means the first queued candidate skips all its siblings."""
    label = _norm(name)
    if not 3 <= len(label) <= 30:
        return []
    url = f"https://www.producthunt.com/posts/{slug}"
    doms = resolve_via_company_api(name, label) + resolve_via_hn(name, label)
    seen, cands = set(), []
    for d in doms:
        if d not in seen:
            seen.add(d)
            cands.append({"domain": d, "via": via + "_resolved", "label": label,
                          "title": title, "url": url})
    for tld in GUESS_TLDS:
        d = label + tld
        if d not in seen:
            seen.add(d)
            cands.append({"domain": d, "via": via + "_guess", "label": label,
                          "title": title, "url": url})
    return cands


def resolve_via_hn(name: str, label: str) -> list[str]:
    """Resolve a product NAME to real domain(s) by searching HN's index — PH hides its
    links, but dev products almost always surface on HN with their actual URL (guessing name.tld misses renamed domains).
    Strict acceptance: the story title must mention the product name, and the domain
    must not be a platform host. Returns up to 2 candidate domains."""
    if len(label) < 4:
        return []   # short names ("Sim") match everything; not worth the collision risk
    url = ("https://hn.algolia.com/api/v1/search?"
           + urllib.parse.urlencode({"query": name, "hitsPerPage": 8}))
    status, body = fetch(url, limit=0)
    if status != 200:
        return []
    try:
        hits = json.loads(body).get("hits", [])
    except Exception:
        return []
    out = []
    for h in hits:
        u, title = h.get("url"), (h.get("title") or "").lower()
        if not u or not re.search(r"\b" + re.escape(name.lower()) + r"\b", title):
            continue   # whole-word title match only — "sim" must not match "simulation"
        host = urllib.parse.urlsplit(u).hostname or ""
        dom = registrable(host)
        if dom and dom not in PLATFORM and dom not in out:
            out.append(dom)
        if len(out) >= 2:
            break
    return out


def source_producthunt() -> list[dict]:
    """This week's Product Hunt launches with developer/API signals. Gated on
    PRODUCTHUNT_TOKEN (personal developer token, free) — without it this source is
    silently skipped, so HN keeps working alone. Check Product Hunt's API terms for your own use case.

    PH wraps every outbound URL in a bot-challenged redirect (checked 2026-07-25: the
    API's `website`/`productLinks`, the /r/ hop, and post pages all 403 to non-browser
    agents). We don't fight bot walls — so instead we GUESS the product's domain from
    its name over common TLDs and let probe_signals() decide. A guess only survives if
    the domain itself shows strong API signals, and records are keyed by domain (never
    by PH's claim), so a name collision can only ever add a real, correctly-attributed
    API — or nothing."""
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        return []
    since = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(time.time() - LOOKBACK_DAYS * 86400))
    query = {
        "query": """
        query($after: DateTime!) {
          posts(order: NEWEST, first: 50, postedAfter: $after) {
            edges { node {
              name tagline slug
              topics(first: 6) { edges { node { slug } } }
            } }
          }
        }""",
        "variables": {"after": since},
    }
    req = urllib.request.Request(
        "https://api.producthunt.com/v2/api/graphql",
        data=json.dumps(query).encode(),
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.loads(r.read())
    except Exception as e:
        print(f"  producthunt source failed (non-fatal): {e}", file=sys.stderr)
        return []
    out = []
    devish = re.compile(r"\bAPI\b|\bMCP\b|\bSDK\b|developer", re.I)
    for edge in (payload.get("data", {}).get("posts", {}).get("edges") or []):
        node = edge.get("node") or {}
        topics = " ".join(t["node"]["slug"] for t in
                          (node.get("topics", {}).get("edges") or []))
        blurb = f'{node.get("name", "")} {node.get("tagline", "")} {topics}'
        if not (devish.search(blurb) or "developer-tools" in topics):
            continue
        title = f'{node.get("name", "")} — {node.get("tagline", "")}'
        out += ph_candidates(node.get("name") or "", node.get("slug") or "", title,
                             "producthunt")
    return out


def source_producthunt_history(months: int) -> list[dict]:
    """One-off backfill: walk PH's archive month-by-month (top-voted launches in the
    developer-tools / API topics) and emit the same name->TLD guess candidates as the
    weekly source. PH's link wall applies to history too, so the guess+strict-probe
    gauntlet is still the resolver. Run via: discover.py backfill-ph [months]."""
    token = os.environ.get("PRODUCTHUNT_TOKEN")
    if not token:
        print("  backfill needs PRODUCTHUNT_TOKEN", file=sys.stderr)
        return []
    out, seen_labels = [], set()
    devish = re.compile(r"\bAPI\b|\bMCP\b|\bSDK\b|developer|agent", re.I)
    per_window = int(os.environ.get("PH_PER_WINDOW", "20"))
    topics = tuple(os.environ.get("PH_TOPICS", "developer-tools,api-1").split(","))
    now = time.time()
    for m in range(months):
        before = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(now - m * 30 * 86400))
        after = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(now - (m + 1) * 30 * 86400))
        for topic in topics:
            query = {
                "query": """
                query($topic: String!, $after: DateTime!, $before: DateTime!) {
                  posts(order: VOTES, topic: $topic, postedAfter: $after,
                        postedBefore: $before, first: """ + str(per_window) + """) {
                    edges { node { name tagline slug } }
                  }
                }""",
                "variables": {"topic": topic, "after": after, "before": before},
            }
            req = urllib.request.Request(
                "https://api.producthunt.com/v2/api/graphql",
                data=json.dumps(query).encode(),
                headers={"User-Agent": UA, "Content-Type": "application/json",
                         "Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    payload = json.loads(r.read())
            except Exception as e:
                print(f"  ph window {after[:10]} {topic} failed (non-fatal): {e}",
                      file=sys.stderr)
                continue
            for edge in (payload.get("data", {}).get("posts", {}).get("edges") or []):
                node = edge.get("node") or {}
                blurb = f'{node.get("name", "")} {node.get("tagline", "")}'
                if topic != "api-1" and not devish.search(blurb):
                    continue    # api-1 topic is self-selecting; developer-tools needs the regex
                label = _norm(node.get("name"))
                if not 3 <= len(label) <= 30 or label in seen_labels:
                    continue
                seen_labels.add(label)
                title = f'{node.get("name", "")} — {node.get("tagline", "")}'
                out += ph_candidates(node.get("name") or "", node.get("slug") or "",
                                     title, "producthunt_backfill")
            time.sleep(0.5)
    print(f"  ph history: {len(seen_labels)} distinct products over {months} months")
    return out


# --------------------------------------------------------------------------- probing
def probe_signals(dom: str) -> dict | None:
    """Fetch cheap signals that this is a real, documented API vendor. None = dead."""
    status, body = fetch(f"https://{dom}", timeout=10)
    if status != 200 or len(body) < 200:
        return None
    home = body.decode("utf-8", "replace").lower()
    sig = {
        "api_mention": bool(re.search(r"\bapi\b", home)),
        "llms_txt": fetch(f"https://{dom}/llms.txt")[0] == 200,
        "openapi": fetch(f"https://{dom}/openapi.json")[0] == 200,
        "docs": fetch(f"https://{dom}/docs")[0] in (200, 301, 302),
        "pricing": fetch(f"https://{dom}/pricing")[0] in (200, 301, 302),
    }
    sig["score"] = (2 * sig["llms_txt"] + 2 * sig["openapi"]
                    + sig["docs"] + sig["pricing"] + sig["api_mention"])
    return sig


def existing() -> set:
    have = set()
    for p in (CENSUS, QUEUE):
        if p.exists():
            for line in p.open():
                try:
                    have.add(json.loads(line)["domain"])
                except Exception:
                    continue
    for p in (DONE, INBOX, REJECTS):
        if p.exists():
            for line in p.open():
                line = line.strip().lower()
                if line and not line.startswith("#"):
                    have.add(line)
    return have


def main() -> int:
    backfill = len(sys.argv) > 1 and sys.argv[1] == "backfill-ph"
    if backfill:
        months = int(sys.argv[2]) if len(sys.argv) > 2 else 12
        cap = int(os.environ.get("BACKFILL_CAP", "40"))
        print(f"discover: BACKFILL — Product Hunt archive, {months} months, cap {cap}")
        candidates = source_producthunt_history(months)
    else:
        cap = CAP
        ph_on = bool(os.environ.get("PRODUCTHUNT_TOKEN"))
        print(f"discover: scanning launches — HN{' + Product Hunt' if ph_on else ''} "
              f"(last {LOOKBACK_DAYS}d, cap {cap})")
        candidates = source_hn() + source_producthunt()
    print(f"  {len(candidates)} raw candidates")

    have = existing()
    today = time.strftime("%Y-%m-%d")

    # group candidates by product (label) so each product probes sequentially with an
    # early stop at its first keeper; PRODUCTS run in parallel — politeness is per-host
    # and every candidate is a different host, so concurrency across them is fair game.
    groups, order = {}, []
    for c in candidates:
        key = c.get("label") or c["domain"]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)

    seen = set()

    def probe_group(key):
        results = []
        for c in groups[key]:
            dom = c["domain"]
            if dom in seen or dom in have or dom in PLATFORM:
                continue
            seen.add(dom)   # benign race: worst case one duplicate probe
            sig = probe_signals(dom)
            entry = {"date": today, "domain": dom, "via": c["via"], "title": c["title"][:120]}
            need = 3 if c["via"].endswith("_guess") else 2
            if sig is None:
                entry["verdict"] = "dead"
            elif sig["score"] >= need and sig["api_mention"]:
                entry.update(verdict="queued", **{k: v for k, v in sig.items()})
                results.append(entry)
                break           # first keeper wins; skip sibling candidates
            else:
                entry["verdict"] = "weak"
                entry.update({k: v for k, v in (sig or {}).items()})
            results.append(entry)
        return results

    import concurrent.futures
    kept, log = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(probe_group, k) for k in order]
        for fut in concurrent.futures.as_completed(futures):
            for entry in fut.result():
                log.append(entry)
                with LOG.open("a") as f:   # incremental — a killed run keeps progress
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if entry["verdict"] == "queued" and len(kept) < cap:
                    kept.append(entry["domain"])
                    print(f"  + {entry['domain']}  (score {entry.get('score')})  "
                          f"«{entry['title'][:60]}»", flush=True)
                if len(log) % 50 == 0:
                    print(f"  … probed {len(log)}, kept {len(kept)}", flush=True)
            if len(kept) >= cap:
                for f_ in futures:
                    f_.cancel()
                break

    if kept:
        with INBOX.open("a") as f:
            f.write(f"# discovered {today} ({'PH backfill' if backfill else 'weekly scan'}, auto-queued by discover.py)\n")
            for d in kept:
                f.write(d + "\n")

    print(f"discover: {len(kept)} queued -> submissions inbox "
          f"({len(log) - len(kept)} rejected; log: {LOG.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
