#!/usr/bin/env bash
# Weekly freshness ritual — freshness layer 2 (FRESHNESS_SPEC.md).
# The committed census.jsonl (git HEAD) is the baseline; this cycle re-verifies against
# the source, refills what changed, and diffs it into the change ledger.
#
#   ANTHROPIC_API_KEY=... ingest/refresh_cycle.sh
#
# Runs the compute only. Commit (census.jsonl + changelog.jsonl) and deploy are left to
# the operator / a later automation step — deploy needs a Netlify token (STATUS).
# This is the sequence GitHub Actions will call once secret storage is authorized.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/5] refresh — re-fetch every record's source pages, flag value-relevant changes (\$0)"
python3 ingest/refresh.py

WORK=data/needs_reextraction.txt
if [ ! -s "$WORK" ]; then
  echo "no source changes this cycle — nothing to refill. Change ledger unchanged."
  exit 0
fi
ONLY=$(tr '\n' ',' < "$WORK" | sed 's/,$//')
echo "[2/5] refill $(wc -l < "$WORK" | tr -d ' ') changed record(s) from the refreshed pages"
: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY for the refill step}"
python3 ingest/extract.py fill --only "$ONLY"

echo "[3/5] changelog — diff committed census (last cycle) vs refilled working tree"
python3 ingest/changelog.py diff-git

echo "[4/5] qa gate — must exit 0 (fabricated evidence / golden regression blocks publish)"
python3 ingest/qa.py

echo "[5/5] regenerate site (change feed, ticker, record history now reflect new events)"
python3 site/generate.py --base https://apiterms.com

echo
echo "cycle complete. Review data/changelog.jsonl, then:"
echo "  git add data/census.jsonl data/changelog.jsonl && git commit && push"
echo "  redeploy site/dist to Netlify"
