#!/usr/bin/env python3
"""Classify pass: probe every seed domain for liveness + machine-readable surfaces.

Per domain (max 4 cheap requests, honest UA, 10s timeout, full TLS verification —
a broken cert counts as not-alive, which is honest census data):
  - GET https://{domain}/            -> alive? (status)
  - GET https://{domain}/llms.txt    -> llms.txt adoption (also try docs.{domain})
  - GET https://{domain}/openapi.json | /swagger.json (first hit wins; skipped if seed
    already has a spec_url)

Writes data/seed_classified.jsonl = seed record + {alive, llms_txt, openapi_probe}.
Zero deps; ThreadPoolExecutor(12), at most 4 requests per domain total.
"""
import concurrent.futures as cf
import json
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed.jsonl"
OUT = ROOT / "data" / "seed_classified.jsonl"
UA = "apiterms-probe/0.1 (+https://apiterms.com)"


def get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(2048)
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return None, b""


def looks_llms(body: bytes) -> bool:
    t = body.decode("utf-8", "replace").lstrip()
    return t.startswith("#") or t.lower().startswith("> ") or "](" in t[:500]


def looks_spec(body: bytes) -> bool:
    t = body.decode("utf-8", "replace")
    return '"openapi"' in t or '"swagger"' in t


def probe(rec: dict) -> dict:
    dom = rec["domain"]
    status, _ = get(f"https://{dom}/")
    rec["alive"] = status is not None and status < 500

    llms = None
    for host in (dom, f"docs.{dom}"):
        s, body = get(f"https://{host}/llms.txt")
        if s == 200 and looks_llms(body):
            llms = f"https://{host}/llms.txt"
            break
    rec["llms_txt"] = llms

    spec = None
    if not rec.get("spec_url"):
        for path in ("/openapi.json", "/swagger.json"):
            s, body = get(f"https://{dom}{path}")
            if s == 200 and looks_spec(body):
                spec = f"https://{dom}{path}"
                break
    rec["openapi_probe"] = spec
    return rec


def main() -> None:
    recs = [json.loads(l) for l in SEED.open()]
    done = 0
    with OUT.open("w") as f, cf.ThreadPoolExecutor(max_workers=12) as ex:
        for rec in ex.map(probe, recs):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done += 1
            if done % 100 == 0:
                print(f"{done}/{len(recs)}", flush=True)
    alive = llms = spec = 0
    for line in OUT.open():
        r = json.loads(line)
        alive += bool(r["alive"])
        llms += bool(r["llms_txt"])
        spec += bool(r["openapi_probe"] or r.get("spec_url"))
    print(f"done. alive: {alive}/{len(recs)}, llms.txt: {llms}, with spec: {spec}")


if __name__ == "__main__":
    main()
