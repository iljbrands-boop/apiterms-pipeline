#!/usr/bin/env python3
"""Gold-set targeted crawl: fetch hand-picked doc URLs (data/gold_pages.json) and
merge them into data/pages/{domain}/pages.json so `extract.py fill --only` re-fills
from the RIGHT sources. Fixes the generic crawl's failure mode on big vendors
(geo-redirected marketing pages, llms.txt links to unrelated products).

  python3 ingest/gold_crawl.py [domain ...]     # default: every domain in gold_pages.json

Keeps existing pages that still look useful, drops obvious junk (geo-localized
marketing paths like /nl/...), caps each page at the same 20k chars as crawl."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract import get, strip_html, PAGES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GOLD = json.loads((ROOT / "data" / "gold_pages.json").read_text())
CAP = 20_000
MIN_CHARS = 500  # thinner than this = fetch failed or empty shell, don't keep


def looks_junk(url: str) -> bool:
    return "/nl/" in url or url.endswith("/nl")


def main(domains):
    for dom in domains:
        targets = GOLD.get(dom)
        if not targets:
            print(f"{dom}: not in gold_pages.json, skipping")
            continue
        pfile = PAGES / dom / "pages.json"
        old = json.loads(pfile.read_text()) if pfile.exists() else {"domain": dom, "pages": {}}
        pages = {u: t for u, t in old["pages"].items() if not looks_junk(u)}
        added, failed = 0, 0
        for url in targets:
            final, ctype, body = get(url)
            text = strip_html(body)[:CAP] if body else ""
            if final and len(text) >= MIN_CHARS:
                pages[final] = text
                added += 1
            else:
                failed += 1
                print(f"  {dom}: SKIP {url} (unreachable or <{MIN_CHARS} chars)")
            time.sleep(0.5)  # polite
        dropped = len(old["pages"]) - sum(1 for u in old["pages"] if not looks_junk(u))
        pfile.parent.mkdir(parents=True, exist_ok=True)
        pfile.write_text(json.dumps({"domain": dom, "crawled_at": time.strftime("%Y-%m-%d"),
                                     "pages": pages}, ensure_ascii=False))
        print(f"{dom}: {added} gold pages added, {failed} failed, {dropped} junk dropped "
              f"-> {len(pages)} total")


if __name__ == "__main__":
    main(sys.argv[1:] or [d for d in GOLD if not d.startswith("_")])
