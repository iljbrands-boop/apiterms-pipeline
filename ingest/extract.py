#!/usr/bin/env python3
"""S2-S3 extraction: crawl docs/pricing pages per API domain, then LLM-fill the
census schema with per-field evidence URLs.

Two phases, runnable separately:
  python3 ingest/extract.py crawl [N]     -> fetch candidate pages for the first N
                                             un-crawled queue domains into data/pages/
  python3 ingest/extract.py fill [N]      -> LLM-fill census records for the first N
                                             crawled-but-unfilled domains -> data/census.jsonl

Conventions (CLAUDE.md): stdlib only, polite crawling (honest UA, ~1 req/s/domain,
no anti-bot circumvention), per-field evidence URLs, publish nulls honestly.

The fill phase calls the Anthropic Messages API directly over HTTPS (stdlib urllib —
no SDK, per the zero-dependency rule) using structured outputs (output_config.format
json_schema). Model: $CENSUS_MODEL or claude-opus-4-8. Needs $ANTHROPIC_API_KEY.
"""
import concurrent.futures as cf
import html.parser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "data" / "extract_queue.jsonl"
PAGES = ROOT / "data" / "pages"
CENSUS = ROOT / "data" / "census.jsonl"
UA = "apicensus-crawler/0.1 (+https://apiterms.com)"
MODEL = os.environ.get("CENSUS_MODEL", "claude-opus-4-8")
MAX_PAGES_PER_DOMAIN = 6
MAX_TEXT_PER_PAGE = 20_000  # chars of stripped text kept per page

# ---------------------------------------------------------------- crawl phase

CANDIDATE_PATHS = ["/", "/pricing", "/docs", "/api", "/rate-limits", "/developers"]
# words that make a llms.txt-linked page worth fetching
INTERESTING = re.compile(r"pric|rate.?limit|auth|quota|plan|getting.?started|api.?ref|limits", re.I)


class TextExtractor(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def strip_html(body: str) -> str:
    p = TextExtractor()
    try:
        p.feed(body)
    except Exception:
        return body[:MAX_TEXT_PER_PAGE]
    return "\n".join(p.parts)[:MAX_TEXT_PER_PAGE]


def get(url: str, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = r.headers.get("Content-Type", "")
            body = r.read(500_000).decode("utf-8", "replace")
            return r.geturl(), ctype, body
    except Exception:
        return None, None, None


def llms_txt_links(text: str, base: str) -> list:
    """Pull markdown links out of an llms.txt whose titles look terms-relevant."""
    out = []
    for title, href in re.findall(r"\[([^\]]+)\]\(([^)\s]+)\)", text):
        if INTERESTING.search(title) or INTERESTING.search(href):
            out.append(urllib.parse.urljoin(base, href))
    return out


def crawl_domain(rec: dict) -> dict:
    dom = rec["domain"]
    outdir = PAGES / dom
    outdir.mkdir(parents=True, exist_ok=True)
    fetched = {}

    urls = []
    if rec.get("llms_txt"):
        urls.append(rec["llms_txt"])
    urls += [f"https://{dom}{p}" for p in CANDIDATE_PATHS]

    for url in urls:
        if len(fetched) >= MAX_PAGES_PER_DOMAIN:
            break
        final_url, ctype, body = get(url)
        if body is None:
            continue
        is_llms = url.endswith("llms.txt")
        text = body[:MAX_TEXT_PER_PAGE] if is_llms or "text/plain" in (ctype or "") else strip_html(body)
        if len(text.strip()) < 200:  # empty shells / JS-only pages
            continue
        fetched[final_url or url] = text
        if is_llms:
            for link in llms_txt_links(body, url)[:3]:
                if len(fetched) >= MAX_PAGES_PER_DOMAIN:
                    break
                fu, fc, fb = get(link)
                if fb and len(fb.strip()) > 200:
                    fetched[fu or link] = fb[:MAX_TEXT_PER_PAGE] if link.endswith((".txt", ".md")) else strip_html(fb)
        time.sleep(0.3)

    (outdir / "pages.json").write_text(json.dumps(
        {"domain": dom, "crawled_at": time.strftime("%Y-%m-%d"), "pages": fetched},
        ensure_ascii=False))
    return {"domain": dom, "pages": len(fetched)}


def cmd_crawl(n: int):
    queue = [json.loads(l) for l in QUEUE.open()]
    todo = [r for r in queue if not (PAGES / r["domain"] / "pages.json").exists()][:n]
    print(f"crawling {len(todo)} domains (max {MAX_PAGES_PER_DOMAIN} pages each)")
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(crawl_domain, todo):
            print(f"  {res['domain']}: {res['pages']} pages")


# ----------------------------------------------------------------- fill phase

def FIELD(t, enum=None):
    # structured outputs reject `type` union + `enum` on the same node — for enum
    # fields express null by including None in the enum and drop the type.
    value = {"enum": list(enum) + [None]} if enum else {"type": t}
    return {
        "type": "object",
        "properties": {"value": value, "evidence_url": {"type": ["string", "null"]}},
        "required": ["value", "evidence_url"],
        "additionalProperties": False,
    }

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "what_it_does": {"type": "string"},
        "base_url": FIELD(["string", "null"]),
        "auth_type": FIELD(["string", "null"], ["none", "api_key", "oauth2", "bearer_token", "basic", "other"]),
        "free_tier": FIELD(["string", "null"]),
        "pricing_model": FIELD(["string", "null"], ["free", "freemium", "paid", "usage_based", "contact_sales", "other"]),
        "pricing_details": FIELD(["string", "null"]),
        "rate_limits": FIELD(["string", "null"]),
        "openapi_spec_url": FIELD(["string", "null"]),
        "mcp_server": FIELD(["string", "null"]),
        "category": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["name", "what_it_does", "base_url", "auth_type", "free_tier",
                 "pricing_model", "pricing_details", "rate_limits", "openapi_spec_url",
                 "mcp_server", "category", "confidence", "notes"],
    "additionalProperties": False,
}

SYSTEM = """You fill one record of a public-API census from crawled pages of the API's website.

Rules — accuracy is the product's trust moat:
- Every field is {value, evidence_url}. evidence_url must be EXACTLY one of the "===== PAGE:"
  URLs above — a page whose text you can actually see. NEVER cite a URL that is merely linked
  or mentioned inside a page (e.g. a path listed in llms.txt): if a fact is only implied by
  such a link, cite the page that contains the link (the llms.txt itself) and reflect the
  weaker evidence in confidence. If the pages don't document a fact, set value to null and
  evidence_url to null. NEVER guess or use outside knowledge for values — "not documented" is
  honest data. free_tier / pricing_details / rate_limits values are short verbatim-ish
  summaries (e.g. "10,000 requests/month free", "600 req/min per token").
- what_it_does: one plain sentence, no marketing language.
- confidence: high = pricing+auth+limits all evidenced; medium = some evidenced; low = thin pages.
- notes: anything a QA reviewer should double-check, else null."""


def api_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY")
    if not k:
        sys.exit("ANTHROPIC_API_KEY not set — export it, then re-run `fill`.")
    return k


def call_claude(prompt: str) -> dict:
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 8000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM,
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"Content-Type": "application/json", "x-api-key": api_key(),
                 "anthropic-version": "2023-06-01"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                resp = json.loads(r.read())
            if resp.get("stop_reason") == "refusal":
                raise RuntimeError("refusal")
            text = next(b["text"] for b in resp["content"] if b["type"] == "text")
            return json.loads(text), resp.get("usage", {})
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 529) and attempt < 3:
                time.sleep(15 * (attempt + 1))
                continue
            raise RuntimeError(f"API {e.code}: {e.read()[:300]}")
    raise RuntimeError("retries exhausted")


FIELD_KEYS = ["base_url", "auth_type", "free_tier", "pricing_model", "pricing_details",
              "rate_limits", "openapi_spec_url", "mcp_server"]
CONF_ORDER = {"high": 2, "medium": 1, "low": 0}


def enforce_evidence(out: dict, pages: dict) -> dict:
    """Deterministic guard behind the prompt: every evidence_url must be a fetched page.
    If the model cited a URL that is merely *linked inside* a fetched page, re-point the
    evidence to the containing page (weaker but honest) and cap confidence at medium.
    If the cited URL appears nowhere in what we fetched, null the evidence, cap confidence
    at low, and note it — the value survives only as an unevidenced claim for QA to see."""
    fixes = []
    for f in FIELD_KEYS:
        obj = out.get(f)
        if not isinstance(obj, dict):
            continue
        ev = obj.get("evidence_url")
        if not ev or ev in pages:
            continue
        container = next((u for u, t in pages.items() if ev in t
                          or ev.split(out.get("domain") or "\x00")[-1] in t), None)
        if container:
            obj["evidence_url"] = container
            fixes.append(f"{f}: cited un-fetched {ev} -> re-pointed to containing page")
            if CONF_ORDER.get(out.get("confidence"), 0) > CONF_ORDER["medium"]:
                out["confidence"] = "medium"
        else:
            obj["evidence_url"] = None
            fixes.append(f"{f}: cited unknown {ev} -> evidence nulled")
            out["confidence"] = "low"
    if fixes:
        note = "; ".join(fixes)
        out["notes"] = (out.get("notes") + " | " if out.get("notes") else "") + \
                       f"[evidence-guard] {note}"
    return out


def fill_domain(rec: dict) -> dict:
    dom = rec["domain"]
    pages = json.loads((PAGES / dom / "pages.json").read_text())["pages"]
    parts = [f"Domain: {dom}",
             f"Seed name: {rec.get('name')} | seed description: {rec.get('description')} | seed category: {rec.get('category')}",
             f"Known spec URL from registries: {rec.get('spec_url') or rec.get('openapi_probe') or 'none'}",
             "", "Crawled pages:"]
    for url, text in pages.items():
        parts.append(f"\n===== PAGE: {url} =====\n{text}")
    out, usage = call_claude("\n".join(parts))
    out["domain"] = dom
    out = enforce_evidence(out, pages)
    out["evidence_pages"] = list(pages)
    out["last_verified"] = time.strftime("%Y-%m-%d")
    out["extractor"] = MODEL
    with (ROOT / "data" / "fill_usage.jsonl").open("a") as uf:
        uf.write(json.dumps({"domain": dom, "model": MODEL,
                             "input_tokens": usage.get("input_tokens"),
                             "output_tokens": usage.get("output_tokens")}) + "\n")
    return out


def has_pages(domain: str) -> bool:
    """True if we crawled at least one usable page — else fill has nothing to read
    and would spend an API call producing a near-null record. Skip those."""
    f = PAGES / domain / "pages.json"
    return f.exists() and bool(json.loads(f.read_text()).get("pages"))


def cmd_refill(domains: list):
    """Re-extract specific domains and REPLACE their census records in place.
    Used by the gold-set audit and (later) the freshness layer-2 re-fill."""
    queue = {json.loads(l)["domain"]: json.loads(l) for l in QUEUE.open()}
    recs = [json.loads(l) for l in CENSUS.open()] if CENSUS.exists() else []
    by_dom = {r["domain"]: i for i, r in enumerate(recs)}
    print(f"refilling {len(domains)} records with {MODEL} (replace in place)")
    for dom in domains:
        if dom not in queue:
            print(f"  {dom}: not in extract queue, skipped")
            continue
        if not has_pages(dom):
            print(f"  {dom}: no crawled pages, skipped")
            continue
        try:
            out = fill_domain(queue[dom])
            if dom in by_dom:
                recs[by_dom[dom]] = out
            else:
                recs.append(out)
            print(f"  {dom}: ok ({out['confidence']})")
        except Exception as e:
            print(f"  {dom}: FAILED — {e}")
    with CENSUS.open("w") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def cmd_fill(n: int):
    queue = [json.loads(l) for l in QUEUE.open()]
    done = set()
    if CENSUS.exists():
        done = {json.loads(l)["domain"] for l in CENSUS.open()}
    todo = [r for r in queue if r["domain"] not in done and has_pages(r["domain"])][:n]
    skipped = sum(1 for r in queue if r["domain"] not in done
                  and (PAGES / r["domain"] / "pages.json").exists() and not has_pages(r["domain"]))
    print(f"filling {len(todo)} records with {MODEL}"
          + (f" (skipping {skipped} zero-page domains)" if skipped else ""))
    with CENSUS.open("a") as f:
        for rec in todo:
            try:
                out = fill_domain(rec)
                f.write(json.dumps(out, ensure_ascii=False) + "\n")
                f.flush()
                print(f"  {rec['domain']}: ok ({out['confidence']})")
            except Exception as e:
                print(f"  {rec['domain']}: FAILED — {e}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "crawl"
    if cmd == "fill" and len(sys.argv) > 2 and sys.argv[2].startswith("--only"):
        # extract.py fill --only dom1,dom2   (or --only=dom1,dom2)
        arg = sys.argv[2].split("=", 1)[1] if "=" in sys.argv[2] else sys.argv[3]
        cmd_refill([d.strip() for d in arg.split(",") if d.strip()])
    else:
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        {"crawl": cmd_crawl, "fill": cmd_fill}[cmd](n)
