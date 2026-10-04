import asyncio
import unittest

from demo_service import DemoService, Limits


class DemoServiceTests(unittest.IsolatedAsyncioTestCase):
    def service(self, **kwargs):
        return DemoService({str(i): {"snapshot-" + str(i)} for i in range(20)}, Limits(**kwargs))

    async def test_denied_never_executes_work(self):
        calls = []
        async def work():
            calls.append(True)
        service = self.service()
        self.assertEqual((await service.execute("0", "snapshot-1", work))["status"], "denied")
        self.assertEqual((await service.execute("intruder", "snapshot-0", work))["status"], "denied")
        self.assertEqual(calls, [])

    async def test_finite_queue_peak_and_fifo(self):
        service = self.service(active=1, waiting=2)
        entered, release = asyncio.Event(), asyncio.Event()
        order = []
        async def work(index):
            order.append(index)
            entered.set()
            if index == 0:
                await release.wait()
            return index
        tasks = []
        for i in range(3):
            tasks.append(asyncio.create_task(service.execute(str(i), "snapshot-" + str(i), lambda i=i: work(i))))
            await asyncio.sleep(0)
        await entered.wait()
        self.assertEqual(len(service.queue), 2)
        self.assertEqual((await service.execute("3", "snapshot-3", lambda: work(3)))["status"], "busy")
        release.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual(order, [0, 1, 2])
        self.assertEqual([row["answer"] for row in results], [0, 1, 2])
        self.assertEqual(service.peak_active, 1)
        self.assertEqual((service.active, len(service.queue), len(service.users)), (0, 0, 0))

    async def test_user_cap_including_waiting(self):
        service = self.service(active=1)
        event = asyncio.Event()
        first = asyncio.create_task(service.execute("0", "snapshot-0", event.wait))
        second = asyncio.create_task(service.execute("1", "snapshot-1", event.wait))
        await asyncio.sleep(0)
        self.assertEqual((await service.execute("1", "snapshot-1", event.wait))["status"], "user_busy")
        event.set()
        await asyncio.gather(first, second)

    async def test_queue_expiry_zero_dispatch_and_no_slot_leak(self):
        service = self.service(active=1, queue_seconds=.01)
        event = asyncio.Event()
        first = asyncio.create_task(service.execute("0", "snapshot-0", event.wait))
        await asyncio.sleep(0)
        calls = []
        async def work():
            calls.append(True)
        second = await service.execute("1", "snapshot-1", work)
        self.assertEqual(second["status"], "queue_expired")
        self.assertEqual(calls, [])
        self.assertEqual(len(service.queue), 0)
        event.set()
        await first
        self.assertEqual(service.active, 0)

    async def test_cancel_waiter_does_not_release_running_slot(self):
        service = self.service(active=1)
        event = asyncio.Event()
        first = asyncio.create_task(service.execute("0", "snapshot-0", event.wait))
        second = asyncio.create_task(service.execute("1", "snapshot-1", event.wait))
        await asyncio.sleep(0)
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertEqual(service.active, 1)
        self.assertEqual(service.users, {"0"})
        event.set()
        await first
        self.assertEqual(service.active, 0)

    async def test_cancel_running_cleanup_before_slot_reuse(self):
        service = self.service(active=1)
        entered, clean = asyncio.Event(), asyncio.Event()
        async def work():
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(.01)
                clean.set()
        first = asyncio.create_task(service.execute("0", "snapshot-0", work))
        await entered.wait()
        async def following():
            self.assertTrue(clean.is_set())
            return "next"
        second = asyncio.create_task(service.execute("1", "snapshot-1", following))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertEqual((await second)["answer"], "next")
        self.assertEqual(service.active, 0)

    async def test_deadline_stops_work_and_allows_next(self):
        service = self.service(task_seconds=.01)
        self.assertEqual((await service.execute("0", "snapshot-0", asyncio.Event().wait))["status"], "deadline_exceeded")
        async def immediate():
            return 1
        self.assertEqual((await service.execute("0", "snapshot-0", immediate))["answer"], 1)

    async def test_failure_redaction_cleanup(self):
        service = self.service()
        async def fail():
            raise RuntimeError("private key")
        result = await service.execute("0", "snapshot-0", fail)
        self.assertEqual(result["status"], "work_failed")
        self.assertNotIn("private", str(result))
        self.assertEqual(service.users, set())

    async def test_cancel_after_promotion_before_resume_releases_slot(self):
        service = self.service(active=1)
        event = asyncio.Event()
        first = asyncio.create_task(service.execute("0", "snapshot-0", event.wait))
        second = asyncio.create_task(service.execute("1", "snapshot-1", event.wait))
        await asyncio.sleep(0)
        event.set()
        await first
        second.cancel()
        try:
            await second
        except asyncio.CancelledError:
            pass
        self.assertEqual((service.active, len(service.queue), len(service.users)), (0, 0, 0))

    def test_invalid_capacity(self):
        for item in ({"active": 0}, {"waiting": -1}, {"task_seconds": float("nan")}, {"active": True}):
            with self.assertRaises(ValueError):
                Limits(**item)


if __name__ == "__main__":
    unittest.main()
