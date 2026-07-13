# API Terms — the open pipeline

**Current, source-linked access terms for every public API — auth, pricing, free tier,
rate limits, OpenAPI spec, MCP server — one structured record each, with an evidence URL
on every field.** This is the open-source pipeline behind **[apiterms.com](https://apiterms.com)**.

The incumbent open API directory, [apis.guru](https://apis.guru), froze in April 2023 and
only ever tracked OpenAPI specs. But the spec is the *rarest* machine-readable artifact in
the whole API economy — and the data agents and integrators actually need (how do I auth,
what does it cost, what are the limits) has never existed as data. This is the pipeline that
compiles it, and keeps it current.

> **One finding from the corpus:** across ~1,340 public APIs, **2.25× more ship a documented
> MCP server than expose an OpenAPI spec URL** (16.3% vs 7.2%). The tooling is organized
> around the spec; the APIs have moved to the agent interface.

## What's in this repo

The **pipeline is fully open** — every line of code that turns a vendor's docs page into a
verified record. A **250-record sample** ([`data/sample.jsonl`](data/sample.jsonl)) and the
full **coverage list** ([`data/seed_domains.txt`](data/seed_domains.txt)) are here too.

The **full live dataset** (all records + the change history that powers the
[change feed](https://apiterms.com/changes/)) lives at apiterms.com — see
[dataset access](https://apiterms.com/dataset/).

```
ingest/seed_pull.py     seed registries (apis.guru + public-apis) -> data/seed.jsonl
ingest/classify.py      liveness + llms.txt + openapi.json probes -> extraction queue
ingest/add_domains.py   hand-add high-value domains (the PR target: data/seed_domains.txt)
ingest/extract.py       crawl: fetch candidate docs pages | fill: LLM + strict schema + evidence
ingest/qa.py            QA gate: rejects fabricated evidence, checks golden assertions
ingest/refresh.py       layer 1 — re-fetch source pages, detect changes ($0, no API key)
ingest/changelog.py     layer 2 — diff record values -> the change feed
ingest/stats.py         "State of the API Economy" report from the corpus
site/generate.py        the whole static site (record pages, categories, change feed, RSS)
```

Zero-dependency **stdlib Python only**. Flat JSONL files, no database, no framework.

## Data principles

- **Evidence or null.** Every field value cites the exact crawled page that states it. A
  deterministic guard ([`qa.py`](ingest/qa.py)) rejects any citation of a page the extractor
  didn't actually read. When a vendor doesn't document a fact, it's published as `null` —
  never guessed. "Not documented" is honest data.
- **Freshness is the product.** apis.guru died of invisible staleness. Every record is
  snapshotted at its source; [`changelog.py`](ingest/changelog.py) diffs re-verifications
  into a structured change ledger. That history can't be backfilled after the fact.
- **Accuracy is the trust moat.** One wrong pricing claim kills the product. Per-field
  evidence + confidence levels + a QA gate on every batch + a public corrections path.

## Quickstart

```bash
# no dependencies to install — stdlib Python 3.9+ only
python3 ingest/classify.py          # probe seed domains for liveness + machine-readable surfaces
python3 ingest/extract.py crawl 50  # fetch docs pages for the first 50 queued domains ($0)
ANTHROPIC_API_KEY=... python3 ingest/extract.py fill 50   # extract records with evidence
python3 ingest/qa.py                # gate the batch (must exit 0)
python3 site/generate.py --base https://example.com       # build the static site
```

The `fill` step calls the Anthropic Messages API directly over stdlib `urllib` (no SDK), with
structured outputs and a hard rule that every value must cite one of the exact pages it was
shown, or be null. See [`docs/methodology.md`](docs/methodology.md) for the full extraction
and verification design.

## Add an API

Using an API we don't cover yet? Two ways to get it into the census:

1. **Open a PR** adding the domain to [`data/seed_domains.txt`](data/seed_domains.txt) — see
   [CONTRIBUTING.md](CONTRIBUTING.md).
2. **Use the form** at [apiterms.com/add](https://apiterms.com/add/).

Either way, **you contribute a domain, not data.** The pipeline crawls the vendor's own docs,
extracts the terms with a source link on every field, and runs the QA gate — so community
coverage expands without ever compromising the evidence-or-null guarantee.

## License

Code: [MIT](LICENSE). Sample data: see [DATA_LICENSE.md](DATA_LICENSE.md).
