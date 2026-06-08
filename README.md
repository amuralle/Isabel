# Isabel (Community ELO Bot)

Isabel is a multi-guild Discord bot for cross-community Halo event tracking and CELO (Community ELO).

## Core Design

- Global user tracking across guilds (by Discord user ID)
- Guild registry and user allegiance mapping
- XUID linking and Halo match ingestion
- Event career statistics, logged match medals, combat leaderboards, and CELO
- CELO ladder with dynamic K-factor and confidence-style deviation
- Hybrid commands: slash + prefix `&`

## Project Layout

- `bot.py`: bot bootstrap, extension loading, command sync
- `cogs/registry.py`: guild registration + allegiance commands
- `cogs/identity.py`: XUID linking commands
- `cogs/events.py`: event report + match ingest + CELO update
- `cogs/career.py`: event-only career and CELO profile display
- `cogs/combat_intel.py`: combat profiles, medal reports, and stat leaderboards
- `cogs/celo.py`: CELO profile and leaderboard commands
- `cogs/assistant.py`: Isabel ChatGPT command with CELO-oriented system prompt
- `helpers/db.py`: schema setup, persistence, CELO calculations
- `helpers/match_data.py`: Halo match API ingestion via `spnkr`
- `analytics/`: offline model evaluation, historical raid backfill experiments, and reports that are not user-facing by default

## Setup

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill required values.
3. Copy `config.example.json` to `config.json` and fill required values:
   - `token`: Discord bot token
   - `application_id`: Isabel Discord application ID
   - `prefix`: `&`
   - `owners`: Discord user IDs allowed to run owner-only commands
4. If using Halo match ingestion, copy `config/tokens.example.json` to `config/tokens.json` and populate it with local Halo/spnkr tokens.
5. Run:
   - `python bot.py`

## Repository Safety

The public repo intentionally excludes runtime secrets and generated/private data:

- `.env`
- `config.json`
- `config/tokens.json`
- SQLite databases under `database/`
- database backups
- logs
- generated analytics output under `analytics/output/`

Use the included `*.example` files as templates. Do not commit live Discord bot tokens, OpenAI API keys, Halo tokens, production databases, or exported player/event datasets.

## Discord Permissions

The default lower-permission invite value in `config.example.json` is `326417632256`, which covers the normal public workflow: viewing/sending messages, embeds/files, reading message history, and managing/using report threads.

If a server wants Isabel to clean up stale bot-authored match embeds during operation-report refreshes, add `Manage Messages`. That changes the permissions value to `326417640448`. Without it, Isabel can still function, but refreshed reports may accumulate duplicate or stale bot-generated match cards.

## Initial Commands

- `&help`
- `&clan_setup_help`
- `&register_clan`
- `&unregister_clan`
- `&clan_profile`
- `&registered_clans`
- `&clan_roster`
- `&set_allegiance` (run in the target server)
- `&my_allegiances`
- `&link_xuid <gamertag> [xuid]`
- `&bulk_register <roster>`
- `&unlink_xuid <xuid>`
- `&my_xuids [member_id]`
- `&set_event_channel <#forum>`
- `&add_event_reporter_role <@role>`
- `&remove_event_reporter_role <@role>`
- `&list_event_reporter_roles`
- `&report_event`
- `&import_cortana_event <cortana_event_id> <opponent_clan_id> [category] [post_reports]`
- `&recalc_event_celo <event_id>`
- `&career [member]`
- `&combat_profile [member] [scope]`
- `&combat_leaderboard [board] [limit] [scope]`
- `&medal_report [member] [scope]`
- `&backfill_medals [limit]` (owner-only)
- `&celo [member_id]`
- `&celo_models`
- `&celo_leaderboard [limit] [model]`
- `&ask_isabel <prompt>`
- `&invite_isabel`

## Cortana Compatibility

For UNSC, Cortana can remain the visible event logger while Isabel acts as the distributed CELO layer.

- Cortana external-event logs can be pushed into Isabel through Isabel's Cortana import bridge.
- Isabel imports Cortana event, game, player stat, medal, and XUID rows, then applies official CELO and training models.
- Isabel report posting can be suppressed for Cortana-led UNSC imports so Cortana remains the only visible event-log thread.
- Opponents can be registered Isabel clans or unregistered external teams; CELO still updates at the gamertag/XUID level.
- Isabel also exposes `/import_cortana_event` for staff/manual recovery when an event needs to be imported after the fact.

## CELO Model (MVP)

Isabel is currently in training mode and displays multiple CELO models side by side:

- `official`: current Isabel CELO profile table.
- `baseline`: team-result Elo with no individual performance modifier.
- `conservative`: current-style CELO with a light individual performance modifier.
- `performance`: current-style CELO with a stronger individual performance modifier.
- `raid_flat`: raid-oriented CELO with only a tiny capped stat modifier.
- `raid_stepwise`: raid-oriented CELO with bucketed stat impact to dampen power-weapon/stat-role inflation.

Historical raid backfill work is analytics-only. Candidate historical raids should stay under `analytics/output/` and must not affect public career totals, current raid reports, or user-facing leaderboards unless staff explicitly promotes a reviewed dataset. Hidden experiment models can be kept out of CELO commands with `public: false` in `CELO_MODEL_CONFIGS`.

- Baseline rating: `1000`
- Baseline deviation: `350`
- Team expected score: logistic function using team average ratings
- Dynamic K-factor:
  - Higher for new players
  - Modulated by deviation
- Sliding performance modifier (within-team percentiles):
  - Kills, assists, damage, and low deaths are converted to team-relative percentiles
  - Percentiles scale each player's CELO delta with bounded multipliers
- CELO updates are applied from ingested game outcomes and recorded in `celo_ledger`

## Event Reporting UX

- Events are currently **guild-vs-guild only**:
  - The opponent must be an active entry in `guild_registry`.
- Report permissions are scoped per guild:
  - Administrators can always report.
  - Additional reporter roles can be whitelisted per guild.
- Duplicate protection:
  - If any match ID is already logged in `games`, the event report is rejected.
- Match entry options:
  - `report_event` is DM-driven and requires a linked XUID.
  - In DM flow, choose recent matches from linked XUID history or paste manual match IDs.
- Operation report broadcast:
  - Configure a report forum per guild with `&set_event_channel`.
  - On successful report, Isabel posts the operation report as forum threads for both participating guilds.

## Notes

- This is a fresh Isabel codebase, not a direct Cortana clone.
- It intentionally reuses Cortana-compatible token/tooling patterns for Halo APIs.
- Combat intelligence is computed from Isabel's own logged events and linked XUIDs. Use `scope:server` to limit reports to the current guild, or keep the default `global` for cross-guild totals.
