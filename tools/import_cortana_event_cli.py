from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import cortana_import


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Import a Cortana event into Isabel.")
    parser.add_argument("--cortana-event-id", type=int, required=True)
    parser.add_argument("--isabel-guild-id", required=True)
    parser.add_argument("--opponent-guild-id")
    parser.add_argument("--opponent-name")
    parser.add_argument("--coordinator-id", required=True)
    parser.add_argument("--category", default="Raid")
    args = parser.parse_args()

    result = await cortana_import.import_cortana_event(
        cortana_event_id=args.cortana_event_id,
        isabel_guild_id=args.isabel_guild_id,
        isabel_opponent_guild_id=args.opponent_guild_id,
        opponent_name=args.opponent_name,
        coordinator_id=args.coordinator_id,
        category=args.category,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_main())
