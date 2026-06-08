# Security Policy

## Secrets

Never commit live secrets to this repository. This includes:

- Discord bot tokens
- OpenAI API keys
- Xbox/Halo API keys
- Halo/spnkr token JSON files
- production SQLite databases or backups
- logs that may contain IDs, operational details, or request metadata

Use `.env.example`, `config.example.json`, and `config/tokens.example.json` as templates.

## Data

Isabel stores Discord IDs, XUIDs, gamertags, event reports, match stats, CELO ledgers, and roster metadata in SQLite. Treat production database files as private operational data.

Generated analytics output may contain match IDs, XUIDs, gamertags, and derived performance data. Keep it out of public commits unless it has been reviewed and intentionally anonymized.

## Permissions

Prefer least privilege for distributed installs. `Manage Messages` is optional and only needed when a server wants Isabel to delete stale bot-authored report embeds during report refreshes.

## Reporting

If you find a security issue, notify the project maintainer privately rather than opening a public issue with secrets, tokens, or exploit details.
