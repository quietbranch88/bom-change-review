"""Explicit synthetic-only OpenRouter adapter; no retry or implicit paid enablement."""
import copy
import json
from decimal import Decimal, ROUND_CEILING

import httpx2

from agent_control import StopRun
from model_budget import BudgetError, money
from openrouter_planner import ProviderPlanner, unique_object
from sqlite_budget import SQLiteBudget, units

MODEL = "mistralai/ministral-3b-2512"
PROVIDER = "mistral/zdr"
MAX_TOKENS = 2048
ENDPOINT = "https://openrouter.ai/api/v1"


class TrialBudget(SQLiteBudget):
    """Two durable attempts for this account/file, not two per process/request."""
    def reserve(self, maximum_charge_usd):
        quote = units(maximum_charge_usd)
        if quote <= 0:
            raise BudgetError("invalid_model_quote")
        with self.transaction(write=True) as db:
            count = db.execute("SELECT COUNT(*) FROM budget_reservations WHERE account=?", (self.account,)).fetchone()[0]
            if count >= 2:
                raise BudgetError("model_call_limit")
            limit, blocked = db.execute("SELECT limit_units,blocked FROM budget_accounts WHERE name=?", (self.account,)).fetchone()
            if blocked:
                raise BudgetError("model_budget_blocked")
            spent, held = self.totals(db)
            if spent + held + quote > limit:
                raise BudgetError("model_budget_exhausted")
            return db.execute("INSERT INTO budget_reservations(account,quote_units,state) VALUES (?,?,'reserved')",
                              (self.account, quote)).lastrowid


class OpenRouterTransport:
    kind = "openrouter_http"

    def __init__(self, key, *, enabled=False, http_factory=httpx2.AsyncClient):
        if enabled is not True or not isinstance(key, str) or not key.startswith("sk-or-v1-"):
            raise StopRun("model_transport_disabled")
        self.key, self.http_factory = key, http_factory
        self.quote = None
        self.attempts = 0
        self.preflight_stage = "not_started"
        self.last_http_status = None

    async def http(self, method, path, payload=None):
        # Fixed destination, no environment proxy, redirect, HTTP or provider retry.
        async with self.http_factory(timeout=25, trust_env=False, follow_redirects=False) as http:
            async with http.stream(method, ENDPOINT + path,
                    headers={"Authorization": "Bearer " + self.key}, json=payload) as response:
                self.last_http_status = response.status_code
                if response.status_code != 200:
                    raise StopRun("model_transport_failed")
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 64 * 1024:
                        raise StopRun("invalid_model_response")
        return json.loads(raw, object_pairs_hook=unique_object)

    async def preflight(self):
        try:
            self.preflight_stage = "endpoint_read"
            catalog = (await self.http("GET", "/models/" + MODEL + "/endpoints"))["data"]
            self.preflight_stage = "endpoint_validation"
            matches = [e for e in catalog["endpoints"] if e.get("tag") == PROVIDER and e.get("model_id") == MODEL]
            if catalog["id"] != MODEL or len(matches) != 1:
                raise ValueError()
            endpoint = matches[0]
            context = endpoint["context_length"]
            if (endpoint["status"] != 0 or type(context) is not int or not 0 < context <= 131072
                    or endpoint["max_completion_tokens"] < MAX_TOKENS
                    or not {"tools", "tool_choice", "max_tokens", "temperature"}.issubset(endpoint["supported_parameters"])):
                raise ValueError()
            prices = endpoint["pricing"]
            prompt, completion = money(prices["prompt"]), money(prices["completion"])
            if max(prompt, completion) > Decimal("0.0000001"):
                raise ValueError()
            for name in ("request", "image", "web_search", "internal_reasoning"):
                if money(prices.get(name, 0)) != 0:
                    raise ValueError()
            if money(prices.get("input_cache_read", prompt)) > prompt:
                raise ValueError()
            # Full context plus output and 10% headroom; not a tokenizer estimate.
            quote = ((context * prompt + MAX_TOKENS * completion) * Decimal("1.10")).quantize(
                Decimal("0.000001"), rounding=ROUND_CEILING)
            if not 0 < quote <= Decimal("0.02"):
                raise ValueError()
            self.preflight_stage = "key_read"
            key = (await self.http("GET", "/key"))["data"]
            self.preflight_stage = "key_validation"
            if (not 0 < money(key["limit"]) <= Decimal("0.10") or key["limit_reset"] is not None
                    or not quote <= money(key["limit_remaining"]) <= money(key["limit"])
                    or key.get("is_management_key") is not False):
                raise ValueError()
            self.quote = quote
            self.preflight_stage = "passed"
            return quote
        except Exception:
            raise StopRun("model_transport_disabled") from None

    async def complete(self, request):
        if self.quote is None or self.attempts >= 2:
            raise StopRun("model_call_limit")
        if request.get("model") != MODEL or len(json.dumps(request).encode()) > 20000:
            raise StopRun("invalid_model_request")
        payload = copy.deepcopy(request)
        payload.pop("parallel_tool_calls", None)  # Endpoint does not advertise this parameter.
        payload["max_tokens"] = MAX_TOKENS
        payload["plugins"] = []
        payload["provider"] = {"only": [PROVIDER], "order": [PROVIDER], "allow_fallbacks": False,
            "require_parameters": True, "data_collection": "deny", "zdr": True,
            "max_price": {"prompt": 0.10, "completion": 0.10}}
        # This provider rejects uniqueItems; the local parser still rejects duplicates.
        def schema(value):
            if isinstance(value, dict):
                value.pop("uniqueItems", None)
                for child in value.values(): schema(child)
            elif isinstance(value, list):
                for child in value: schema(child)
        schema(payload["tools"])
        self.attempts += 1
        response = await self.http("POST", "/chat/completions", payload)
        # Preserve a reported bill even if output/provider identity is invalid.
        cost = response.get("usage", {}).get("cost")
        if type(cost) in (int, float) and money(cost).is_finite():
            response["usage"]["cost"] = float(money(cost).quantize(Decimal("0.000001"), rounding=ROUND_CEILING))
        if response.get("usage", {}).get("is_byok") is True:
            response["usage"]["cost"] = None  # External invoices are outside the quoted credit budget.
        if response.get("model") != MODEL:
            response["choices"] = []  # Preserve a reported charge, but never trust another model's output.
        return response


class LiveProviderPlanner(ProviderPlanner):
    kind = "provider_live"
    transport_kind = "openrouter_http"
    model = MODEL

    def __init__(self, transport, budget, check):
        if not isinstance(transport, OpenRouterTransport) or transport.quote is None:
            raise StopRun("model_transport_disabled")
        super().__init__(transport, budget, maximum_charge_usd=transport.quote)
        self.check = check

    async def request(self, function, parameters, context):
        await self.check()  # Revoked/foreign case never dispatches a model request.
        return await super().request(function, parameters, context)

    def accounting(self):
        value = super().accounting()
        value.update(mode="bounded_live_model", model=MODEL, provider=PROVIDER,
                     pricing="published_full_context_reservation_not_provider_billing_cap",
                     reported_cost_rounding="upward_to_micro_usd")
        return value
