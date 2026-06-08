# Historical Raid Backfill Policy

Historical raids are an analytics and model-training source only.

They should not appear in normal user-facing Isabel views unless staff explicitly promotes a specific dataset into the current event system.

## Separation Rules

- Keep historical crawl outputs under `analytics/output/`.
- Do not insert historical candidate matches into the live `events` table during discovery.
- Do not use historical candidate matches for public career totals, current raid reports, or current user-facing leaderboards.
- If a historical-trained CELO model is added to `CELO_MODEL_CONFIGS`, set `public: false`.
- User-facing CELO commands should show current/live public models only.

## Intended Workflow

1. Staff provides high-volume raid gamertags or XUIDs.
2. Offline crawler resolves XUIDs and pulls available match history.
3. Candidate matches are deduped against already logged games.
4. Candidate matches are scored as `likely_raid`, `possible_raid`, or `not_raid`.
5. Staff reviews likely/possible raids before any promotion.
6. Model experiments may consume reviewed historical datasets, but public Isabel UX remains focused on current raids.

## Promotion Rule

Historical data can influence future model tuning after review, but it should remain invisible to players by default. Public commands should answer: "What is true for current Isabel-tracked raids?" not "What did an offline backfill crawler find?"
