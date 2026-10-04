"""Loopback-only demo BFF: browser has an opaque cookie, never OAuth tokens."""

import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route
from mcp.server.transport_security import RequestBodyLimitMiddleware

from oauth_provider import OAuthFailure, loopback_url


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


class BrowserVault:
    """Single-process bounded synthetic demo state; not a distributed session store."""

    def __init__(self, clock=time.monotonic):
        self.sessions, self.pending, self.logout_checks = {}, {}, {}
        self.clock = clock

    def prune(self):
        now = self.clock()
        for table in (self.sessions, self.pending, self.logout_checks):
            for key in list(table):
                if table[key]["deadline"] <= now:
                    del table[key]

    def session(self, cookie):
        self.prune()
        return self.sessions.get(cookie)

    def start(self, name, cookie):
        self.prune()
        if len(self.sessions) >= 100 or len(self.pending) >= 100:
            raise OAuthFailure("demo_capacity")
        # Bind state to a fresh opaque browser cookie for first login.
        cookie = cookie if cookie in self.sessions else secrets.token_urlsafe(32)
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        self.pending[state] = {"name": name, "cookie": cookie, "nonce": nonce,
                               "verifier": verifier, "deadline": self.clock() + 300}
        return state, self.pending[state]

    def consume(self, state, cookie, name):
        self.prune()
        found = self.pending.pop(state, None)
        if not found or found["cookie"] != cookie or found["name"] != name:
            raise OAuthFailure("invalid_callback")
        return found

    def store(self, cookie, name, subject, tokens):
        existing = self.session(cookie)
        if existing and existing["subject"] != subject:
            raise OAuthFailure("identity_changed")
        if name == "mcp" and not existing:
            raise OAuthFailure("portal_login_required")
        if not existing:
            if len(self.sessions) >= 100:
                raise OAuthFailure("demo_capacity")
            existing = {"subject": subject, "tokens": {}, "csrf": secrets.token_urlsafe(32),
                        "deadline": self.clock() + 900}
            self.sessions[cookie] = existing
        existing["tokens"][name] = tokens


class Portal:
    def __init__(self, provider, origin, cases, vault=None, review=None):
        self.provider, self.origin, self.cases = provider, loopback_url(origin), cases
        self.vault = vault or BrowserVault()
        self.events = []
        self.limiter = anyio.CapacityLimiter(2)
        self.review = review

    def record(self, event):
        self.events.append(event)
        del self.events[:-100]

    def cookie(self, request):
        return request.cookies.get("bom_demo", "")

    def page(self, content, status=200):
        return HTMLResponse("<!doctype html><html lang='en'><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>BOM Review · SSO Demo</title><style>body{font:16px system-ui;"
            "background:#f3f6fa;color:#16263c;max-width:850px;margin:48px auto;padding:24px}"
            "main{background:white;padding:32px;border-radius:16px}button,a{margin:8px 8px 8px 0}"
            "button{padding:12px;background:#204cab;color:white;border:0;border-radius:8px;cursor:pointer}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;padding:16px}"
            "small{color:#52627a}</style><main><h1>BOM Review · SSO + MCP</h1>"
            "<p>Local synthetic demo · Read-only · No engineering approval · "
            + ("Bounded " + html.escape(self.review.mode) + " model mode" if self.review else "No paid model") + "</p>"
            + content + "</main></html>", status_code=status)

    async def home(self, request):
        session = self.vault.session(self.cookie(request))
        if not session:
            return self.page("<h2>Signed out</h2><a href='/login/portal'>Sign in with Keycloak</a>")
        content = "<h2>Portal SSO authenticated</h2>"
        if "mcp" not in session["tokens"]:
            content += "<a href='/login/mcp'>Authorize MCP using SSO</a>"
        else:
            content += "<p>MCP authorized with a separate resource-bound access token.</p>"
            content += "<a href='/login/mcp'>Reauthorize MCP with SSO</a>"
            for name in self.cases:
                content += (f"<form method='post' action='/read/{html.escape(name)}'>"
                            f"<input type='hidden' name='csrf' value='{session['csrf']}'>"
                            f"<button>Read case {html.escape(name)}</button></form>")
                if self.review:
                    content += (f"<form method='post' action='/review/{html.escape(name)}'>"
                                f"<input type='hidden' name='csrf' value='{session['csrf']}'>"
                                f"<button>Review case {html.escape(name)} with Agent</button></form>")
        content += ("<form method='post' action='/logout'><input type='hidden' name='csrf' value='"
                    + session["csrf"] + "'><button>Sign out everywhere</button></form>")
        return self.page(content)

    async def login(self, request):
        name = request.path_params["name"]
        if name not in {"portal", "mcp"}:
            return self.page("Invalid request", 400)
        if name == "mcp" and not self.vault.session(self.cookie(request)):
            return self.page("Portal login required", 401)
        try:
            state, pending = self.vault.start(name, self.cookie(request))
            response = RedirectResponse(self.provider.authorize_url(name, state, pending["nonce"],
                                                                     challenge(pending["verifier"])), 303)
            response.set_cookie("bom_demo", pending["cookie"], httponly=True, samesite="lax", max_age=900)
            return response
        except OAuthFailure:
            return self.page("Demo capacity reached", 503)

    async def callback(self, request):
        name = request.path_params["name"]
        params = request.query_params
        try:
            if (set(params) - {"state", "session_state", "iss", "code"}
                    or any(len(params.getlist(k)) != 1 for k in params)
                    or params.get("iss") != self.provider.issuer or not params.get("code")):
                raise OAuthFailure("invalid_callback")
            pending = self.vault.consume(params.get("state", ""), self.cookie(request), name)
            async with self.limiter:
                tokens, subject = await self.provider.exchange(name, params["code"], pending["verifier"], pending["nonce"])
            self.vault.store(pending["cookie"], name, subject, tokens)
            self.record(name + "_login")
            return RedirectResponse("/", 303)
        except OAuthFailure as failure:
            self.record("callback_denied")
            category = failure.args[0] if failure.args else None
            self.record("callback_" + category if category in {
                "invalid_callback", "oauth_unavailable", "invalid_token", "identity_changed",
                "portal_login_required", "demo_capacity"} else "callback_unspecified")
            # Strip authorization code from the browser URL even on failure.
            return RedirectResponse("/error", 303)

    async def post_session(self, request):
        if request.headers.get("origin") != self.origin:
            self.record("post_origin_null" if request.headers.get("origin") == "null" else "post_origin_denied")
            return None
        session = self.vault.session(self.cookie(request))
        body = await request.body()
        if not session or len(body) > 1024:
            return None
        from urllib.parse import parse_qs
        form = parse_qs(body.decode(errors="replace"), max_num_fields=2)
        submitted = form.get("csrf", [])
        if (set(form) != {"csrf"} or len(submitted) != 1
                or not hmac.compare_digest(submitted[0], session["csrf"])):
            return None
        return session

    async def read(self, request):
        session = await self.post_session(request)
        name = request.path_params["name"]
        if not session or "mcp" not in session["tokens"]:
            return self.page("Authentication required", 401)
        if name not in self.cases:
            return self.page("Invalid request", 400)
        try:
            async with self.limiter, httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + session["tokens"]["mcp"]["access_token"]},
                    timeout=30, trust_env=False) as http:
                transport = streamable_http_client(self.provider.resource, http_client=http)
                with anyio.fail_after(40):
                    async with Client(transport, read_timeout_seconds=30, cache=None) as client:
                        value = await client.call_tool("get_case_gaps", {"snapshot_id": self.cases[name]})
            if not self.vault.session(self.cookie(request)):
                raise OAuthFailure("session_ended")
            evidence = value.structured_content
            if value.is_error or not evidence or evidence["status"] != "historical_snapshot":
                raise OAuthFailure("read_failed")
            self.record("read_" + name + "_allowed")
            return self.page("<h2>Case " + html.escape(name) + " evidence</h2><pre>"
                             + html.escape(json.dumps(evidence, indent=2, ensure_ascii=False))
                             + "</pre><a href='/'>Back to cases</a>")
        except Exception:
            # SDK errors can contain peer input. Never render or log them.
            self.record("read_" + name + "_denied")
            return self.page("<h2>Access denied or evidence unavailable</h2>"
                             "<p>No case evidence was disclosed.</p><a href='/'>Back to cases</a>", 403)

    async def logout(self, request):
        session = await self.post_session(request)
        if not session:
            return self.page("Authentication required", 401)
        self.vault.sessions.pop(self.cookie(request), None)
        state = secrets.token_urlsafe(32)
        self.vault.prune()
        self.vault.logout_checks[state] = {"deadline": self.vault.clock() + 300,
                                          "access": session["tokens"].get("mcp", {}).get("access_token"),
                                          "cookie": self.cookie(request)}
        try:
            async with self.limiter:
                for name, tokens in session["tokens"].items():
                    await self.provider.revoke(name, tokens)
        except OAuthFailure:
            self.record("revocation_unavailable")
        self.record("local_session_ended")
        return RedirectResponse("/finish-logout?" + urlencode({"state": state}), 303)

    async def finish_logout(self, request):
        self.vault.prune()
        state = request.query_params.get("state", "")
        pending = self.vault.logout_checks.get(state)
        if (set(request.query_params) != {"state"} or len(request.query_params.getlist("state")) != 1
                or not pending or pending["cookie"] != self.cookie(request)):
            return self.page("Logout handoff unavailable. Start again from sign-in.", 401)
        destination = self.provider.endpoint + "/logout?" + urlencode({
            "client_id": self.provider.clients["portal"]["id"], "post_logout_redirect_uri": self.origin + "/logged-out",
            "state": state})
        return self.page("<h2>Local session ended</h2><p>Complete the identity provider sign-out next.</p>"
                         "<a href='" + html.escape(destination, quote=True) + "'>Finish SSO sign-out</a>")

    async def review_case(self, request):
        session = await self.post_session(request)
        name = request.path_params["name"]
        if not session or "mcp" not in session["tokens"]:
            return self.page("Authentication required", 401)
        if name not in self.cases:
            return self.page("Invalid request", 400)
        if self.review is None:
            return self.page("Model workflow is disabled", 503)
        try:
            async with self.limiter:
                with anyio.fail_after(90):
                    result = await self.review.execute(session["tokens"]["mcp"]["access_token"],
                        session["subject"], self.cases[name],
                        lambda: self.vault.session(self.cookie(request)) is session)
            if self.vault.session(self.cookie(request)) is not session:
                raise OAuthFailure("session_ended")
            self.record("agent_" + name + "_" + result["status"])
            return self.page("<h2>Bounded Agent result</h2><pre>"
                + html.escape(json.dumps(result, ensure_ascii=False, indent=2))
                + "</pre><a href='/'>Back to cases</a>")
        except Exception:
            self.record("agent_" + name + "_denied")
            return self.page("<h2>Access denied or review unavailable</h2>"
                "<p>No case evidence was disclosed.</p><a href='/'>Back to cases</a>", 403)

    async def logged_out(self, request):
        self.vault.prune()
        pending = self.vault.logout_checks.pop(request.query_params.get("state", ""), None)
        if not pending or pending["cookie"] != self.cookie(request):
            return RedirectResponse("/error", 303)
        active = await self.provider.verify_token(pending["access"]) if pending["access"] else None
        self.record("logout_replay_denied" if active is None else "logout_replay_still_active")
        response = RedirectResponse("/", 303)
        response.delete_cookie("bom_demo")
        return response

    def app(self):
        routes = [Route("/", self.home), Route("/login/{name}", self.login),
                  Route("/callback/{name}", self.callback), Route("/read/{name}", self.read, methods=["POST"]),
                  Route("/review/{name}", self.review_case, methods=["POST"]),
                  Route("/logout", self.logout, methods=["POST"]), Route("/finish-logout", self.finish_logout),
                  Route("/logged-out", self.logged_out),
                  Route("/error", lambda r: self.page("Authentication failed. Please start again.", 400)),
                  Route("/demo-status", lambda r: JSONResponse({"events": self.events,
                    "model_mode": self.review.mode if self.review else "disabled",
                    "paid_model_calls": None if self.review and self.review.mode == "live" else 0,
                    "billing_counter": "not_available_use_trial_ledger"}))]
        app = Starlette(routes=routes)
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost"])
        app.add_middleware(RequestBodyLimitMiddleware, max_body_size=16384)

        async def safe_headers(request: Request, call_next):
            try:
                response = await call_next(request)
            except Exception:
                response = self.page("Request unavailable", 503)
            response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "same-origin",
                                     "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                                     "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"})
            return response
        app.add_middleware(BaseHTTPMiddleware, dispatch=safe_headers)
        return app
