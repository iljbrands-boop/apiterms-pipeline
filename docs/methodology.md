# Methodology

How a vendor's docs page becomes a verified record. The whole design serves one goal:
**you should be able to audit any value in one click, and we should never state a fact we
can't point to.**

## The schema

One record per API, each field an object of `{value, evidence_url}`:

```json
{
  "domain": "example.com",
  "auth_type":   {"value": "api_key", "evidence_url": "https://docs.example.com/auth"},
  "free_tier":   {"value": "10,000 requests/month free", "evidence_url": "https://example.com/pricing"},
  "rate_limits": {"value": "60 req/min per key", "evidence_url": "https://example.com/pricing"},
  "openapi_spec_url": {"value": null, "evidence_url": null},
  "confidence": "high",
  "last_verified": "2026-07-12"
}
```

Fields: `base_url`, `auth_type`, `free_tier`, `pricing_model`, `pricing_details`,
`rate_limits`, `openapi_spec_url`, `mcp_server`, plus `name`, `what_it_does`, `category`,
`confidence`, `last_verified`, and the list of `evidence_pages` the record was built from.

## Extraction

1. **Crawl** ([`extract.py crawl`](../ingest/extract.py)) fetches a handful of the vendor's
   own pages — homepage, `/pricing`, `/docs`, `llms.txt`, and any terms-relevant pages the
   llms.txt links to. HTML is stripped to text and snapshotted to disk. Polite: honest
   user-agent, low rate, no anti-bot circumvention. Walled sites are marked, not bypassed.

2. **Fill** ([`extract.py fill`](../ingest/extract.py)) sends those pages to an LLM with a
   locked output schema (structured outputs) and one hard rule: **every value must cite one
   of the exact pages it was shown, or be `null`.** No outside knowledge, no guessing.

3. **Evidence guard** (in `fill`) is deterministic, not a prompt: after the model responds,
   every `evidence_url` is checked against the list of pages actually fetched. A citation of a
   page the model only saw *linked* (not read) is re-pointed to the containing page and the
   confidence is capped; a citation of a page we never fetched is nulled. The model cannot
   invent a source and have it survive.

## Verification (QA gate)

[`qa.py`](../ingest/qa.py) runs on every batch and **exits non-zero on any critical flag**, so
it can gate a pipeline:

- **`fabricated_evidence`** — an `evidence_url` that isn't one of the record's own crawled
  pages. This is the check that protects trust.
- **`golden_assertions`** — a set of hand-verified `domain.field` facts that must never
  regress to `null`. Catches a re-fill silently losing a fact we know is documented.
- Structural checks (missing fields, malformed values, over-confident-but-empty records), a
  per-field fill-rate report, and a deterministic 5% manual-review sample.

## Confidence

- **high** — pricing, auth, and limits all evidenced from read pages.
- **medium** — some fields evidenced; others null or thinner.
- **low** — sparse source pages; treat as a stub. The public site holds low-field records out
  of its search index rather than presenting them as authoritative.

## Freshness (the two layers)

- **Layer 1** ([`refresh.py`](../ingest/refresh.py), $0, no API key): re-fetch each record's
  source pages, normalize, and diff against the stored snapshot. Only records whose *source
  actually changed* are re-extracted — this keeps the re-fill cost small.
- **Layer 2** ([`changelog.py`](../ingest/changelog.py)): diff the record *values* before vs
  after a re-fill and emit structured change events (`field, old, new, kind, significance,
  source`). Values are normalized (case/whitespace/punctuation) so an identical fact
  re-phrased by the extractor does not emit a false event. The `diff-git` mode uses the
  committed census as the baseline — git is the snapshot store, so the change history is
  reproducible from repository history.

The full weekly sequence is [`refresh_cycle.sh`](../ingest/refresh_cycle.sh):
`refresh → fill only-what-changed → changelog diff → qa gate → regenerate`.

## Known limitations

- **Multi-product vendors** (AWS, Google, Twilio) are one record per domain today; a
  provider→product hierarchy is future work.
- **`mcp_server`** counts a documented MCP server (including docs references), not a verified
  live endpoint.
- Rates reported over the whole corpus are **floors** — a `null` means "not stated where we
  looked," not "doesn't exist." Rates over the high/medium-confidence subset are the ceiling.
