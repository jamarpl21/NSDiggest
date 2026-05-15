# NSDiggest

Daily Gmail newsletter digest generator.  
It fetches emails, summarizes them in Polish with Claude, deduplicates overlapping topics, and sends a styled HTML digest.

## Pipeline

```text
IMAP fetch -> Stage 1 (parallel per newsletter) -> Stage 2 (daily dedupe) -> Render HTML -> SMTP send -> Mark SEEN
```

1. **IMAP fetch**: load `UNSEEN` + recent messages, dedupe by Message-ID.
2. **Stage 1**: one LLM call per newsletter (`ANTHROPIC_MODEL_STAGE1`) to extract topics.
3. **Stage 2**: one lightweight LLM call (`ANTHROPIC_MODEL_STAGE2`) on compact Stage 1 output to mark duplicates.
4. **Render**: build HTML email digest.
5. **Send**: SMTP delivery.
6. **Mark SEEN**: only after successful send.

## Project structure

```text
src/
  config.py
  fetch.py
  digest.py
  render.py
  send.py
  main.py
deploy/
  install.sh
  nsdiggest.service
  nsdiggest.timer
.env.example
requirements.txt
```

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# no send / no mark seen
python -m src.main --dry-run

# focused low-cost test
python -m src.main --skip-send --skip-mark-seen --only-indices 0,3 --max-newsletters 2
```

## Configuration

Use `.env` (local) or `/etc/nsdiggest/nsdiggest.env` (server).

| Key | Default | Description |
|---|---|---|
| `GMAIL_USER` | — | Gmail account used as newsletter source |
| `GMAIL_APP_PASSWORD` | — | Gmail app password |
| `DIGEST_TO` | — | Recipient email |
| `DIGEST_FROM_NAME` | `Newsletter Digest` | Display name for sender |
| `ANTHROPIC_API_KEY` | — | Claude API key |
| `ANTHROPIC_MODEL_STAGE1` | `claude-sonnet-4-6` | Stage 1 model |
| `ANTHROPIC_MODEL_STAGE2` | `claude-sonnet-4-6` | Stage 2 model |
| `STAGE1_MAX_WORKERS` | `5` | Max parallel Stage 1 calls |
| `STAGE1_INPUT_USD_PER_MTOK` | `3.0` | Sonnet input price ($ / 1M tokens) |
| `STAGE1_OUTPUT_USD_PER_MTOK` | `15.0` | Sonnet output price ($ / 1M tokens) |
| `STAGE1_CACHE_WRITE_USD_PER_MTOK` | `3.75` | Sonnet cache write (5m) price ($ / 1M tokens) |
| `STAGE1_CACHE_READ_USD_PER_MTOK` | `0.30` | Sonnet cache read price ($ / 1M tokens) |
| `STAGE2_INPUT_USD_PER_MTOK` | `3.0` | Sonnet input price ($ / 1M tokens) |
| `STAGE2_OUTPUT_USD_PER_MTOK` | `15.0` | Sonnet output price ($ / 1M tokens) |
| `STAGE2_CACHE_WRITE_USD_PER_MTOK` | `3.75` | Sonnet cache write (5m) price ($ / 1M tokens) |
| `STAGE2_CACHE_READ_USD_PER_MTOK` | `0.30` | Sonnet cache read price ($ / 1M tokens) |
| `ANTHROPIC_MODEL` | — | Legacy fallback for both stages |
| `LOOKBACK_DAYS` | `1` | How many days back to include |
| `DATA_DIR` | `./data` | Artifacts directory |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DRY_RUN` | `0` | If `1`, do not send and do not mark as SEEN |

Pricing defaults above follow Anthropic Claude API pricing for Sonnet 4.6 at the time of writing.

## Cost visibility

- Per-newsletter estimated LLM cost is attached to each newsletter in:
  - digest JSON (`estimated_cost_usd`, token counters)
  - rendered HTML (`cost LLM ~ $...`)
- Cost is estimated from token usage returned by Anthropic API.

## Public GitHub repository checklist

Before publishing:

1. **Never commit secrets**
   - Keep `.env` local only.
   - Rotate any key that may have been exposed in history.
2. **Keep generated/runtime data out of git**
   - `data/`, logs, virtual environments should be ignored.
3. **Use placeholder infrastructure values**
   - Avoid hardcoded personal IPs, account emails, and internal hostnames in docs.
4. **Document required env vars**
   - `.env.example` should include all required configuration keys.
5. **Run a dry-run before release**
   - `python -m src.main --dry-run`
6. **Optional hardening**
   - Enable GitHub secret scanning / push protection.
   - Add `LICENSE` and `SECURITY.md`.

## Reliability notes

- Emails are marked as SEEN only after successful send.
- Stage 2 failure does not block the run (digest can be sent without dedupe).
- Stage 1 includes retry and malformed JSON recovery paths.
