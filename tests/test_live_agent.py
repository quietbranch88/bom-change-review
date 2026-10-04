"""Independent no-spend auth, durable attempt and transport invariants."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from concurrent.futures import ThreadPoolExecutor

try:
    from live_model import TrialBudget, OpenRouterTransport, LiveProviderPlanner, MODEL, PROVIDER
    from sso_agent import SSOReview, review_factory
    from oauth_mcp import CaseGrants
    from oauth_provider import READ_SCOPE
    from sso_portal import Portal, BrowserVault
    from starlette.testclient import TestClient
    from agent_control import StopRun
    from model_budget import BudgetError
    import httpx2
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "optional MCP extra required")
class LiveAgentTests(unittest.TestCase):
    def test_competing_clients_have_only_two_durable_admissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.sqlite"
            TrialBudget(path, "0.02")
            def reserve(_):
                try:
                    return TrialBudget(path, "0.02").reserve("0.001")
                except BudgetError as error:
                    return str(error)
            with ThreadPoolExecutor(max_workers=3) as executor:
                results = list(executor.map(reserve, range(3)))
            self.assertEqual(sum(type(value) is int for value in results), 2)
            self.assertEqual(results.count("model_call_limit"), 1)
            self.assertEqual(TrialBudget(path, "0.02").snapshot()["held"], "0.002")

    def test_trial_attempt_limit_is_durable_after_settlement(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trial.sqlite"
            first = TrialBudget(path, "0.02", account="trial")
            for _ in range(2):
                ticket = first.reserve("0.014644")
                first.settle(ticket, "0.0001")
            second = TrialBudget(path, "0.02", account="trial")
            with self.assertRaisesRegex(BudgetError, "model_call_limit"):
                second.reserve("0.014644")
            self.assertEqual(second.snapshot()["spent"], "0.0002")
            self.assertEqual(second.snapshot()["held"], "0")

    def test_unknown_bill_retained_and_new_attempt_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = TrialBudget(Path(tmp) / "trial.sqlite", "0.02")
            first.unknown(first.reserve("0.014644"))
            with self.assertRaisesRegex(BudgetError, "model_budget_blocked"):
                first.reserve("0.001")
            self.assertEqual(first.snapshot()["held"], "0.014644")

    def catalog(self):
        return {"data": {"id": MODEL, "endpoints": [{"tag": PROVIDER, "model_id": MODEL,
            "context_length": 131072, "max_completion_tokens": 4096, "status": 0,
            "supported_parameters": ["tools", "tool_choice", "max_tokens", "temperature"],
            "pricing": {"prompt": "0.0000001", "completion": "0.0000001"}}]}}

    def transport(self):
        transport = OpenRouterTransport("sk-or-v1-synthetic-test-only", enabled=True)
        transport.http = AsyncMock(side_effect=[self.catalog(), {"data": {
            "limit": .1, "limit_remaining": .1, "limit_reset": None, "is_management_key": False}}])
        return transport

    def test_preflight_bound_and_explicit_disabled_default(self):
        with self.assertRaisesRegex(StopRun, "model_transport_disabled"):
            OpenRouterTransport("sk-or-v1-synthetic-test-only")
        t = self.transport()
        self.assertEqual(str(asyncio.run(t.preflight())), "0.014644")
        self.assertEqual([c.args[0] for c in t.http.call_args_list], ["GET", "GET"])

    def test_price_provider_or_uncapped_key_refused_without_post(self):
        for kind in ("price", "provider", "key"):
            t = self.transport()
            catalog = self.catalog()
            key = {"data": {"limit": .1, "limit_remaining": .1, "limit_reset": None, "is_management_key": False}}
            if kind == "price": catalog["data"]["endpoints"][0]["pricing"]["prompt"] = "0.0001"
            if kind == "provider": catalog["data"]["endpoints"][0]["tag"] = "other"
            if kind == "key": key["data"]["limit"] = None
            t.http = AsyncMock(side_effect=[catalog, key])
            with self.assertRaisesRegex(StopRun, "model_transport_disabled"):
                asyncio.run(t.preflight())
            self.assertTrue(all(c.args[0] == "GET" for c in t.http.call_args_list))

    def test_wire_pins_provider_and_rounds_reported_cost_up(self):
        t = self.transport()
        asyncio.run(t.preflight())
        t.http = AsyncMock(return_value={"model": MODEL, "usage": {"cost": .00000021}})
        request = {"model": MODEL, "parallel_tool_calls": False, "tools": [{"uniqueItems": True}],
                   "stream": False, "max_tokens": 512}
        response = asyncio.run(t.complete(request))
        wire = t.http.call_args.args[2]
        self.assertEqual(response["usage"]["cost"], .000001)
        self.assertEqual(wire["provider"]["only"], [PROVIDER])
        self.assertFalse(wire["provider"]["allow_fallbacks"])
        self.assertEqual(wire["max_tokens"], 2048)
        self.assertNotIn("parallel_tool_calls", wire)
        self.assertNotIn("uniqueItems", wire["tools"][0])
        self.assertTrue(request["tools"][0]["uniqueItems"])
        self.assertNotIn("Bearer", json.dumps(wire))

    def test_cross_case_role_inactive_and_subject_denial_before_factory(self):
        issuer = "http://localhost:18880/realms/test"
        grants = CaseGrants(issuer, {"A": {"role": "reader", "tenant": "A", "snapshots": ["a", "b"]}},
                            {"a": {"tenant": "A"}, "b": {"tenant": "B"}})
        for subject, case, active, session in (("A", "b", True, True), ("B", "a", True, True),
                                                ("A", "a", False, True), ("A", "a", True, False)):
            token = SimpleNamespace(subject=subject, claims={"iss": issuer}, scopes=[READ_SCOPE]) if active else None
            provider = SimpleNamespace(verify_token=AsyncMock(return_value=token))
            factory = AsyncMock(side_effect=StopRun("unexpected_planner_creation"))
            review = SSOReview(provider, grants, factory)
            with self.assertRaisesRegex(StopRun, "evidence_unavailable"):
                asyncio.run(review.execute("synthetic-access", "A", case, lambda: session))
            factory.assert_not_called()
        grants.identities["A"]["role"] = "reviewer"
        provider.verify_token.return_value = SimpleNamespace(subject="A", claims={"iss": issuer}, scopes=[READ_SCOPE])
        with self.assertRaisesRegex(StopRun, "evidence_unavailable"):
            asyncio.run(review.execute("synthetic-access", "A", "a", lambda: True))
        factory.assert_not_called()

    def test_live_factory_requires_explicit_paid_flag(self):
        with self.assertRaisesRegex(StopRun, "model_transport_disabled"):
            review_factory(None, None, mode="live", budget_path="unused.sqlite")

    def test_live_planner_revocation_precedes_reservation_and_dispatch(self):
        t = self.transport()
        asyncio.run(t.preflight())
        with tempfile.TemporaryDirectory() as tmp:
            budget = TrialBudget(Path(tmp) / "trial.sqlite", "0.02")
            check = AsyncMock(side_effect=StopRun("evidence_unavailable"))
            planner = LiveProviderPlanner(t, budget, check)
            with self.assertRaisesRegex(StopRun, "evidence_unavailable"):
                asyncio.run(planner.next("question", "case:" + "a" * 64, ()))
            self.assertEqual(planner.calls, 0)
            self.assertEqual(budget.snapshot()["held"], "0")
            self.assertTrue(all(c.args[0] == "GET" for c in t.http.call_args_list))

    def test_actual_http_client_failure_has_one_attempt_and_retains_hold(self):
        requests = []
        def reject(request):
            requests.append(request)
            return httpx2.Response(429, json={"error": {"message": "private-error-must-not-escape"}})
        def http_factory(**options):
            self.assertFalse(options["trust_env"])
            self.assertFalse(options["follow_redirects"])
            return httpx2.AsyncClient(transport=httpx2.MockTransport(reject), **options)
        t = OpenRouterTransport("sk-or-v1-synthetic-test-only", enabled=True, http_factory=http_factory)
        from decimal import Decimal
        t.quote = Decimal("0.014644")
        with tempfile.TemporaryDirectory() as tmp:
            budget = TrialBudget(Path(tmp) / "trial.sqlite", "0.02")
            planner = LiveProviderPlanner(t, budget, AsyncMock())
            with self.assertRaisesRegex(StopRun, "^model_transport_failed$"):
                asyncio.run(planner.next("synthetic question", "case:" + "a" * 64, ()))
            self.assertEqual(len(requests), 1)
            self.assertEqual(str(requests[0].url), "https://openrouter.ai/api/v1/chat/completions")
            self.assertEqual(planner.calls, 1)
            self.assertEqual(budget.snapshot()["held"], "0.014644")
            self.assertTrue(budget.snapshot()["blocked"])

    def test_wrong_model_and_external_byok_never_become_accepted_output(self):
        t = self.transport()
        asyncio.run(t.preflight())
        request = {"model": MODEL, "tools": []}
        t.http = AsyncMock(return_value={"model": "other", "choices": ["untrusted"], "usage": {"cost": .001}})
        result = asyncio.run(t.complete(request))
        self.assertEqual(result["choices"], [])
        self.assertEqual(result["usage"]["cost"], .001)
        t.http = AsyncMock(return_value={"model": MODEL, "usage": {"cost": .001, "is_byok": True}})
        result = asyncio.run(t.complete(request))
        self.assertIsNone(result["usage"]["cost"])

    def test_portal_csrf_disabled_mode_and_session_cleanup(self):
        vault = BrowserVault()
        session = {"subject": "A", "tokens": {"mcp": {"access_token": "synthetic-access"}},
                   "csrf": "synthetic-csrf", "deadline": vault.clock() + 30}
        vault.sessions["synthetic-cookie"] = session
        review = SimpleNamespace(mode="fixture", execute=AsyncMock(return_value={"status": "completed", "paid_model_calls": 0}))
        portal = Portal(None, "http://localhost:18882", {"A": "case-a"}, vault=vault, review=review)
        with TestClient(portal.app(), base_url="http://localhost:18882") as client:
            client.cookies.set("bom_demo", "synthetic-cookie")
            self.assertIn("Review case A with Agent", client.get("/").text)
            bad = client.post("/review/A", data={"csrf": "wrong"}, headers={"Origin": portal.origin})
            self.assertEqual(bad.status_code, 401)
            review.execute.assert_not_called()
            good = client.post("/review/A", data={"csrf": session["csrf"]}, headers={"Origin": portal.origin})
            self.assertEqual(good.status_code, 200)
            self.assertIn("Bounded Agent result", good.text)
            vault.sessions.clear()
            self.assertEqual(client.post("/review/A", data={"csrf": session["csrf"]},
                                        headers={"Origin": portal.origin}).status_code, 401)
            self.assertEqual(review.execute.await_count, 1)
