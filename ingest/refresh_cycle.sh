#!/usr/bin/env bash
# Weekly freshness ritual — freshness layer 2 + community onboarding.
# The committed census.jsonl (git HEAD) is the baseline; this cycle onboards any newly
# submitted APIs, re-verifies existing records against their source, refills what changed,
# and diffs it into the change ledger.
#
#   ANTHROPIC_API_KEY=... ingest/refresh_cycle.sh
#
# Runs the compute only. Commit (census.jsonl + changelog.jsonl) and deploy are handled
# by the GitHub Actions workflow that calls this. The refill/onboard steps need the key.
set -euo pipefail
cd "$(dirname "$0")/.."

# How many freshly-submitted domains to onboard per cycle (bounds cost: ~$0.09/domain).
SUB_CAP="${SUB_CAP:-25}"

echo "[0/7] submissions — onboard community-added domains (data/submissions.txt) into the queue"
QUEUED=0
if SUB_OUT=$(python3 ingest/submissions.py); then
  echo "$SUB_OUT"
  QUEUED=$(echo "$SUB_OUT" | sed -n 's/^QUEUED //p' | tail -1)
  QUEUED=${QUEUED:-0}
fi
if [ "$QUEUED" -gt 0 ]; then
  : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY to onboard submitted APIs}"
  echo "     crawling + filling $QUEUED newly submitted domain(s) (cap $SUB_CAP)"
  python3 ingest/extract.py crawl "$SUB_CAP"
  python3 ingest/extract.py fill "$SUB_CAP"
fi

echo "[1/7] refresh — re-fetch every record's source pages, flag value-relevant changes (\$0)"
python3 ingest/refresh.py

WORK=data/needs_reextraction.txt
CHANGED=0
if [ -s "$WORK" ]; then
  ONLY=$(tr '\n' ',' < "$WORK" | sed 's/,$//')
  CHANGED=$(wc -l < "$WORK" | tr -d ' ')
  echo "[2/7] refill $CHANGED changed record(s) from the refreshed pages"
  : "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY for the refill step}"
  python3 ingest/extract.py fill --only "$ONLY"
else
  echo "[2/7] no source changes this cycle — nothing to refill."
fi

# Nothing new and nothing changed → the census is untouched; skip straight to the site.
if [ "$QUEUED" -eq 0 ] && [ "$CHANGED" -eq 0 ]; then
  echo "census unchanged (no submissions, no source changes). Change ledger unchanged."
  echo "[7/7] regenerate site (no data delta, but keeps build fresh)"
  python3 site/generate.py --base https://apiterms.com
  exit 0
fi

echo "[3/7] quarantine — self-heal: revert any bad refill to last-known-good, log for review"
python3 ingest/quarantine.py

echo "[4/7] changelog — diff committed census (last cycle) vs healed working tree"
python3 ingest/changelog.py diff-git

echo "[5/7] qa gate — final sanity (should already be clean post-quarantine)"
python3 ingest/qa.py

echo "[6/7] regenerate site (change feed, ticker, record history now reflect new events)"
python3 site/generate.py --base https://apiterms.com

echo
echo "cycle complete."
echo "  onboarded: $QUEUED new · refreshed: $CHANGED changed"
echo "  review data/changelog.jsonl + data/quarantine.jsonl, then commit + deploy (the workflow does this)."
