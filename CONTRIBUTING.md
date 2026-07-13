# Contributing

Thanks for helping expand the census. The most valuable contribution is telling us **which
APIs to cover** — and the golden rule keeps this project trustworthy:

> **You contribute a domain to cover. You never contribute the field values.**

That boundary is the whole point. If anyone could edit "Stripe's rate limit is X" directly,
the evidence-or-null guarantee — the thing that makes this data worth trusting — would die
the first time someone submitted a wrong number. So the pipeline does all the extraction and
verification; contributors point it at new targets.

## Add an API (the common case)

1. Open a pull request adding the domain to [`data/seed_domains.txt`](data/seed_domains.txt),
   one per line, kept sorted.
2. In the PR description, optionally note the docs or pricing URL — it speeds up crawling.
3. That's it. A maintainer runs it through the pipeline (`add_domains.py` → `extract.py crawl`
   → `fill` → `qa.py`), and once it passes the QA gate it publishes to
   [apiterms.com](https://apiterms.com) with a record page.

Not comfortable with a PR? The form at [apiterms.com/add](https://apiterms.com/add/) does the
same thing.

## What happens to your submission

```
your PR: +1 line in seed_domains.txt (a domain)
   │
   ├─ crawl   — fetch the vendor's own docs / pricing / llms.txt pages
   ├─ fill    — extract auth, pricing, free tier, limits, spec, MCP — each with a source URL
   ├─ qa      — reject any value citing a page we didn't read; hold thin records back
   └─ publish — a record page, every field linking the page that proves it
```

Anything the vendor doesn't document is published as `null`. We don't fill gaps with guesses.

## Fixing a wrong value

Found a field that's out of date or wrong? **Don't edit the data in a PR** — report it at
[apiterms.com/correct](https://apiterms.com/correct/) with the source URL that proves the
correct value, and we re-verify against the vendor's page. Same principle: corrections flow
through verification, not around it.

## Working on the pipeline code

- **Stdlib only.** No third-party dependencies — `urllib`, `json`, `html.parser`, etc. This
  is a hard constraint; PRs adding dependencies won't be merged.
- **Python 3.9+.**
- Keep the ingest scripts single-purpose and each source isolated in `try/except` so one weird
  docs site never cascades.
- Polite crawling only: honest user-agent, low request rate, **no anti-bot circumvention.** If
  a site walls the crawler, we mark it and move on.
- Run `python3 ingest/qa.py` before proposing changes that touch extraction — it must exit 0.

## Code of conduct

Be decent. This is a small project with a simple mission: make API terms machine-readable and
keep them honest.
