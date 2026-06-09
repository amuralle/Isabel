import asyncio
import time
import unittest

from cogs.events import Events


class EventFlowGuardTests(unittest.IsolatedAsyncioTestCase):
    async def _events(self) -> Events:
        events = object.__new__(Events)
        events._active_report_flows = {}
        events._report_flow_lock = asyncio.Lock()
        return events

    async def test_duplicate_report_flow_for_same_user_is_blocked(self):
        events = await self._events()

        self.assertTrue(await events._begin_report_flow("user-1"))
        self.assertFalse(await events._begin_report_flow("user-1"))

        await events._end_report_flow("user-1")
        self.assertTrue(await events._begin_report_flow("user-1"))

    async def test_stale_report_flow_can_be_replaced(self):
        events = await self._events()
        events._active_report_flows["user-1"] = time.monotonic() - 1900

        self.assertTrue(await events._begin_report_flow("user-1"))

