# NSDiggest

Daily Gmail newsletter digest generator.  
It fetches emails, creates one combined digest email, deduplicates overlapping topics, and sends a styled HTML digest. Processing can run without LLM, with LLM only, or in hybrid mode.

## Pipeline

```text
IMAP fetch -> Stage 1 (rule engine and/or LLM extraction) -> Stage 2 (rule or LLM dedupe) -> Render HTML -> Send (SMTP or Gmail API) -> Mark SEEN
```

1. **IMAP fetch**: load `UNSEEN` + recent messages, dedupe by Message-ID.
2. **Stage 1**: extract topics with selected processing mode:
   - `no-llm`: rule-based extraction only
   - `llm-only`: one LLM call per newsletter (`ANTHROPIC_MODEL_STAGE1`)
   - `hybrid`: rule-based extraction first, LLM only for low-quality rule output
3. **Stage 2**: dedupe via:
   - `llm-only`: LLM dedupe (`ANTHROPIC_MODEL_STAGE2`)
   - `no-llm` and `hybrid`: rule-based dedupe
4. **Render**: build HTML email digest.
5. **Send**: delivery via selected transport (`EMAIL_TRANSPORT=smtp` or `gmail-api`).
6. **Mark SEEN**: only after successful send.

## Rule engine (`no-llm`) notes

`no-llm` mode is optimized for newsletter-like formats (sections + numbered items).  
Current extraction strategy in `src/rule_digest.py`:

- strips common newsletter footer/boilerplate (`unsubscribe`, sign-off blocks)
- parses markdown-like sections (`## ...`) and numbered items (`1. ...`)
- builds one topic per numbered item, with section-aware titles
- extracts links from the same item first (`[text](url)`), then falls back to deterministic raw-link matching
- filters noise links (`audio`, `kliknij tutaj`, unsubscribe-style URLs)
- applies sender-specific rule profiles (e.g. different topic/link thresholds for Infopiguła, Puls Biznesu, EXANTE)
- uses parser hierarchy (`BaseNewsletterParser` + sender-specific parsers) where structure is known to be problematic (`src/sender_parsers.py`)
- in `hybrid`, can force LLM only for translation of long single-topic English newsletters from selected senders (currently e.g. EXANTE/Naval)
- in `hybrid`, can force LLM when rule output is not human-readable enough (`human_readability_score`) or when sender needs semantic segmentation (e.g. XYZ)

Practical implications:

- quality is usually lower than `llm-only`, but much better than naive block splitting
- link assignment is now stable and each topic should have at least one relevant link in typical list-based newsletters
- for newsletters without clear list structure, the engine falls back to paragraph heuristics
- `hybrid` keeps per-sender quality history (`data/digests/sender_profiles.json`) and adapts whether to stay on rules or fallback to LLM

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
  release_deploy.sh
  release_rollback.sh
.github/workflows/
  deploy-prod.yml
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

# no-llm processing, no API usage
python -m src.main --processing-mode no-llm --skip-send --skip-mark-seen

# hybrid processing (LLM only when needed)
python -m src.main --processing-mode hybrid --skip-send --skip-mark-seen

# focused low-cost test
python -m src.main --skip-send --skip-mark-seen --only-indices 0,3 --max-newsletters 2
```

## Production deploy (GitHub + DigitalOcean, no Docker)

Deploys are tag-based and run through GitHub Actions (`.github/workflows/deploy-prod.yml`).

### 1. One-time server bootstrap

On the droplet, run:

```bash
sudo bash deploy/install.sh
```

This creates:

- `/opt/nsdiggest/releases/<release_id>`
- `/opt/nsdiggest/current` (active symlink)
- `/etc/nsdiggest/nsdiggest.env` (production env, outside git)
- `nsdiggest.service` and `nsdiggest.timer`

### 2. Configure GitHub secrets

Create `production` environment in GitHub and set:

- `PROD_SSH_HOST`
- `PROD_SSH_USER`
- `PROD_SSH_PORT`
- `PROD_SSH_KEY` (private key for CI runner)
- `PROD_KNOWN_HOSTS` (output from `ssh-keyscan`)

Recommended: enable required reviewers for the `production` environment.

### 3. Release flow

```bash
git tag v1.2.0
git push origin v1.2.0
```

Pipeline behavior:

1. Builds and validates Python artifact on GitHub Actions.
2. Uploads `.tar.gz` to droplet over SSH.
3. Runs `deploy/release_deploy.sh` remotely:
   - extracts to `/opt/nsdiggest/releases/<tag>`
   - creates release-local `.venv`
   - installs/refreshes systemd units
   - switches `/opt/nsdiggest/current` atomically
   - runs smoke check (`--dry-run --skip-send --skip-mark-seen --max-newsletters 1`)
   - keeps only the latest releases (`KEEP_RELEASES`, default `5`)

### 4. Rollback

Rollback to previous release:

```bash
sudo /opt/nsdiggest/current/deploy/release_rollback.sh
```

Rollback to an explicit release id:

```bash
sudo /opt/nsdiggest/current/deploy/release_rollback.sh v1.1.0
```

### 5. Operations cheatsheet

```bash
systemctl status nsdiggest.timer
systemctl status nsdiggest.service
journalctl -u nsdiggest.service -e
tail -f /var/log/nsdiggest/run.log
readlink -f /opt/nsdiggest/current
```

## Configuration

Use `.env` (local) or `/etc/nsdiggest/nsdiggest.env` (server).

| Key | Default | Description |
|---|---|---|
| `GMAIL_USER` | — | Gmail account used as newsletter source |
| `GMAIL_APP_PASSWORD` | — | Gmail app password (required for `EMAIL_TRANSPORT=smtp`) |
| `DIGEST_TO` | — | Recipient email |
| `DIGEST_FROM_NAME` | `Newsletter Digest` | Display name for sender |
| `EMAIL_TRANSPORT` | `smtp` | Delivery transport: `smtp` or `gmail-api` |
| `GMAIL_API_CLIENT_ID` | — | Google OAuth client id (required for `gmail-api`) |
| `GMAIL_API_CLIENT_SECRET` | — | Google OAuth client secret (required for `gmail-api`) |
| `GMAIL_API_REFRESH_TOKEN` | — | Google OAuth refresh token with Gmail send scope (required for `gmail-api`) |
| `ANTHROPIC_API_KEY` | — | Claude API key (required for `llm-only` and `hybrid`) |
| `PROCESSING_MODE` | `llm-only` | `no-llm`, `llm-only`, or `hybrid` |
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

## Metrics artifacts

Each run now persists structured metrics to:

- `data/runs/<run_id>.json` - full run snapshot
- `data/runs/latest.json` - latest run pointer

Run metrics include per-day and per-newsletter quality/cost signals:

- topic counts, duplicates, empty newsletters
- link coverage and missing-link topic counts
- median summary length (words)
- human readability score and unreadable topic count (noise/tracking leak detector)
- `processed_with` source (`rules` vs `llm`)
- stage token counters and estimated LLM cost

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
