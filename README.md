# API Terms — tracking the pricing, auth, and rate limits of public APIs

Every public API documents its authentication, pricing, free tier, and rate limits. But only on pages built for humans, which vendors can change without notice. (and they do...). 
This pipeline turns those pages into structured records (auth type, pricing, free tier, rate limits, OpenAPI spec, MCP server — with a
source link on every field), then keeps going back: re-checking every record against its
source and logging every change. It's the open-source machinery behind
**[apiterms.com](https://apiterms.com)** and its [change feed](https://apiterms.com/changes/).

## Why a tracker, not another directory

The open API directory, [apis.guru](https://apis.guru), froze in April 2023 — and it only
ever tracked OpenAPI specs. The spec is the *rarest* machine-readable artifact in the API
economy; the data agents and integrators actually need (how do I auth, what does it cost,
what are the limits) has never existed as structured data at all.

A one-time scrape of that data would rot the same way. The value isn't the snapshot —
it's the loop: re-fetch, detect real changes, re-extract, diff, publish. That loop is
what this repo contains.

> **One finding from the full corpus:** more public APIs now ship a **documented MCP server**
> than expose an **OpenAPI spec URL** — **2.4× as many** (17% vs 7%). The tooling
> ecosystem is still organized around the spec; the APIs have moved to the agent interface.

## The tracking loop

One command — [`ingest/refresh_cycle.sh`](ingest/refresh_cycle.sh) — runs the whole cycle
(in production it runs on a weekly GitHub Actions cron; if a cycle breaks, the site stays
on the last good build):

1. **Onboard** community-submitted domains ([`submissions.py`](ingest/submissions.py)) —
   crawl and extract any APIs added via PR or the site form since the last cycle.
2. **Re-fetch** every record's source pages ([`refresh.py`](ingest/refresh.py)) — $0, no
   API key.
3. **Detect real changes** — each page is reduced to a signature of its *terms-relevant*
   lines (pricing, limits, auth), with volatile page noise stripped, so a docs redesign
   doesn't count as a pricing change.
4. **Re-extract only what changed** ([`extract.py`](ingest/extract.py)), under a hard
   per-cycle cap that bounds LLM cost.
5. **QA-gate the batch** ([`qa.py`](ingest/qa.py)) — a deterministic guard rejects any
   evidence citation of a page the extractor didn't actually read, plus golden assertions
   on hand-verified records. A critical failure blocks publish.
6. **Self-heal** — a refill that fails QA reverts to last-known-good
   ([`quarantine.py`](ingest/quarantine.py)) and lands in a review queue, so one broken
   vendor page can't corrupt the census.
7. **Diff the values into the change ledger** ([`changelog.py`](ingest/changelog.py)) —
   the append-only history behind the change feed. This is the part that can't be
   backfilled after the fact: either you were watching when the terms changed, or the
   event is gone.

## What's in this repo

Every line of code that builds the census and keeps it current, plus a **250-record
sample** ([`data/sample.jsonl`](data/sample.jsonl)) and the full **coverage list** of
1,727 domains ([`data/seed_domains.txt`](data/seed_domains.txt)).

```
build the census
  ingest/seed_pull.py     seed registries (apis.guru + public-apis) -> data/seed.jsonl
  ingest/classify.py      liveness + llms.txt + openapi.json probes -> extraction queue
  ingest/add_domains.py   read data/seed_domains.txt (the PR target) -> extraction queue
  ingest/extract.py       crawl: fetch candidate docs pages | fill: LLM + strict schema + evidence
  ingest/qa.py            QA gate: rejects fabricated evidence, checks golden assertions

keep it current (the weekly cycle — ingest/refresh_cycle.sh)
  ingest/submissions.py   community-submitted domains -> extraction queue (the /add flow)
  ingest/refresh.py       re-fetch source pages, flag terms-relevant changes ($0, no API key)
  ingest/quarantine.py    self-healing: a refill that fails QA reverts to last-known-good
  ingest/changelog.py     diff record values -> the change feed

publish
  ingest/stats.py         "State of the API Economy" report from the corpus
  site/generate.py        the whole static site (record pages, categories, change feed, RSS)
```

Zero-dependency **stdlib Python only**. Flat JSONL files, no database, no framework.

> The shipped sample is a **quality-filtered slice** (high/medium-confidence records only), so
> its field-fill rates run higher than the full-corpus figures above — in the sample, ~31% ship
> an MCP server and ~16% a spec. The MCP-over-OpenAPI direction holds in both; the exact
> corpus-wide percentages come from the full dataset, not this sample.

The **full live dataset** (all records + the change history that powers the
[change feed](https://apiterms.com/changes/)) lives at apiterms.com — see
[dataset access](https://apiterms.com/dataset/).

## Data principles

- **Evidence or null.** Every field value cites the exact crawled page that states it. A
  deterministic guard ([`qa.py`](ingest/qa.py)) rejects any citation of a page the extractor
  didn't actually read. When a vendor doesn't document a fact, it's published as `null` —
  never guessed. "Not documented" is honest data.
- **Freshness is the product.** apis.guru died of invisible staleness. Every record carries
  a `last_verified` date backed by the tracking loop above, and every change lands in the
  ledger with a timestamp.
- **Accuracy is the trust moat.** One wrong pricing claim kills the product. Per-field
  evidence + confidence levels + a QA gate on every batch + a public corrections path.

## Quickstart

No dependencies to install — **Python 3.9+ stdlib only**, no API key, no network.

```bash
# Build the entire site from the 250-record sample, and pass the QA gate:
cp data/sample.jsonl data/census.jsonl               # the sample becomes your working DB
python3 ingest/qa.py                                 # QA gate — exits 0, 0 criticals
python3 site/generate.py --base https://example.com  # -> site/dist/ (record, category, change-feed pages)
```

That's the whole thing running on a fresh clone. To run the **full pipeline from scratch**
(the last step needs an Anthropic API key):

```bash
python3 ingest/seed_pull.py                # fetch seed registries (apis.guru + public-apis) -> data/seed.jsonl
python3 ingest/classify.py                 # probe liveness + llms.txt + openapi.json -> data/seed_classified.jsonl
python3 ingest/add_domains.py              # populate the extraction queue with curated high-value domains
python3 ingest/extract.py crawl 50         # fetch docs pages for the first 50 queued domains ($0)
ANTHROPIC_API_KEY=... python3 ingest/extract.py fill 50   # extract records with per-field evidence
python3 ingest/qa.py                        # gate the batch (must exit 0)
python3 site/generate.py --base https://example.com
```

From there, `ANTHROPIC_API_KEY=... ingest/refresh_cycle.sh` is the whole tracking loop —
run it on a schedule and you have a live terms tracker for your own corpus.

`add_domains.py` is what seeds `data/extract_queue.jsonl` (the list of domains to crawl,
and the PR target — see [CONTRIBUTING.md](CONTRIBUTING.md)); edit its `ADDITIONS` list to
point the crawler at whatever you want to cover.

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
coverage expands without ever compromising the evidence-or-null guarantee. Submissions are
onboarded automatically by the next weekly cycle — zero human touch from form to published,
tracked record.

## License

Code: [MIT](LICENSE). Sample data: see [DATA_LICENSE.md](DATA_LICENSE.md).
