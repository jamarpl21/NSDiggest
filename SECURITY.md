
# Security Policy

## Reporting a vulnerability

If you discover a security issue, please do **not** open a public GitHub issue with sensitive details.

Instead, share:

- a short description of the issue
- reproduction steps
- potential impact

through a private communication channel agreed with the maintainer.

## Secrets and credentials

This repository must never contain:

- `.env` files with real credentials
- API keys, app passwords, access tokens
- production host private keys

Use `.env.example` as a template and keep real values local.

## Operational recommendations

- Rotate credentials immediately if accidental exposure is suspected.
- Enable GitHub secret scanning and push protection.
- Use least-privilege credentials for mailbox and API access.
