"""Independent OAuth/ACL invariants; provider fixtures are not real SSO evidence."""

import base64
import json
import time
import unittest
from unittest.mock import AsyncMock

try:
    import anyio
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from mcp.server.auth.provider import AccessToken
    from oauth_mcp import CaseGrants, create_http_app
    from oauth_provider import Keycloak, OAuthFailure, READ_SCOPE, loopback_url
    from sso_portal import BrowserVault, Portal, challenge
    from starlette.testclient import TestClient
    from evidence_queries import EvidenceQueries
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


ISSUER = "http://localhost:18880/realms/test"
RESOURCE = "http://localhost:18881/mcp"
CLIENTS = {name: {"id": "bom-" + name, "secret": "synthetic-client-fixture",
                  "redirect": "http://localhost:18882/callback/" + name} for name in ("portal", "mcp")}
CLIENTS["resource"] = {"id": RESOURCE, "secret": "synthetic-client-fixture"}


@unittest.skipUnless(AVAILABLE, "optional MCP dependencies required")
class CryptoVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.key.public_key()))
        cls.jwk.update(kid="test-key", alg="RS256", use="sig")

    def setUp(self):
        self.provider = Keycloak(ISSUER, RESOURCE, CLIENTS)
        self.claims = {"iss": ISSUER, "sub": "user-A", "aud": RESOURCE, "typ": "Bearer",
                       "azp": "bom-mcp", "exp": int(time.time()) + 120, "iat": int(time.time()),
                       "scope": "openid " + READ_SCOPE}
        self.active = True
        self.requests = []

        async def request(path, form=None):
            self.requests.append(path)
            if path == "certs":
                return {"keys": [self.jwk]}
            return {"active": self.active, "sub": "user-A", "exp": self.claims["exp"]}
        self.provider.request = request

    def token(self, changes=None, key=None):
        claims = dict(self.claims, **(changes or {}))
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": "test-key"})

    def verify(self, token):
        return anyio.run(self.provider.verify_token, token)

    def test_resource_token_and_no_active_cache(self):
        token = self.token()
        result = self.verify(token)
        self.assertEqual((result.subject, result.resource, result.claims["iss"]), ("user-A", RESOURCE, ISSUER))
        self.active = False
        self.assertIsNone(self.verify(token))
        self.assertEqual(self.requests.count("token/introspect"), 2)

    def test_introspection_authenticates_resource_not_requesting_client(self):
        original = self.provider.request
        self.provider.request = AsyncMock(side_effect=original)
        self.assertIsNotNone(self.verify(self.token()))
        form = self.provider.request.call_args.args[1]
        self.assertEqual(form["client_id"], RESOURCE)

    def test_invalid_issuer_audience_client_type_and_expiry(self):
        for changes in ({"iss": "http://localhost:18880/realms/other"}, {"aud": "bom-portal"},
                        {"aud": [RESOURCE, "other"]}, {"typ": "ID"}, {"azp": "bom-portal"},
                        {"exp": int(time.time())}, {"exp": False}, {"sub": ""}, {"iat": True}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.verify(self.token(changes)))

    def test_bad_signature_malformed_token_and_provider_outage(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertIsNone(self.verify(self.token(key=other)))
        for token in (None, "not-a-token", "x" * 16385):
            self.assertIsNone(self.verify(token))
        self.provider.request = AsyncMock(side_effect=OAuthFailure("oauth_unavailable"))
        self.assertIsNone(self.verify(self.token()))

    def test_id_token_nonce_and_both_resource_requests(self):
        from urllib.parse import parse_qs, urlsplit
        authorize = parse_qs(urlsplit(self.provider.authorize_url("mcp", "state", "nonce", "challenge")).query)
        self.assertEqual(authorize["resource"], [RESOURCE])
        self.assertEqual(authorize["code_challenge_method"], ["S256"])
        token = self.token({"aud": "bom-mcp", "typ": "ID", "nonce": "wrong"})
        self.provider.request = AsyncMock(return_value={"id_token": token})
        self.provider.claims = AsyncMock(return_value={"sub": "user-A", "nonce": "wrong"})
        self.provider.verify_token = AsyncMock(return_value=AccessToken(
            token="synthetic", client_id="bom-mcp", subject="user-A", scopes=[READ_SCOPE]))
        with self.assertRaises(OAuthFailure):
            anyio.run(self.provider.exchange, "mcp", "code", "verifier", "expected")
        args = self.provider.request.call_args.args[1]
        self.assertEqual(args["resource"], RESOURCE)
        self.assertEqual(args["code_verifier"], "verifier")
        self.assertEqual(args["grant_type"], "authorization_code")


@unittest.skipUnless(AVAILABLE, "optional MCP dependencies required")
class GrantsTests(unittest.TestCase):
    def setUp(self):
        self.grants = CaseGrants(ISSUER, {"user-A": {"tenant": "A", "role": "reader", "snapshots": ["case-A", "case-B"]},
                                         "user-B": {"tenant": "B", "role": "reader", "snapshots": ["case-B"]}},
                                {"case-A": {"tenant": "A"}, "case-B": {"tenant": "B"}})
        self.token = AccessToken(token="synthetic-fixture", client_id="bom-mcp", subject="user-A",
                                 scopes=[READ_SCOPE], claims={"iss": ISSUER})

    def test_own_case_allowed_cross_tenant_denied_even_with_grant(self):
        self.assertTrue(self.grants.allowed(self.token, "case-A"))
        self.assertFalse(self.grants.allowed(self.token, "case-B"))

    def test_unknown_identity_issuer_role_scope_and_missing_grant_denied(self):
        for token in (None, self.token.model_copy(update={"subject": "unknown"}),
                      self.token.model_copy(update={"claims": {"iss": "other"}}),
                      self.token.model_copy(update={"scopes": []})):
            self.assertFalse(self.grants.allowed(token, "case-A"))
        self.grants.identities["user-A"]["role"] = "approver"
        self.assertFalse(self.grants.allowed(self.token, "case-A"))
        self.assertFalse(self.grants.allowed(self.token, "absent"))


@unittest.skipUnless(AVAILABLE, "optional MCP dependencies required")
class VaultTests(unittest.TestCase):
    def setUp(self):
        self.now = 10
        self.vault = BrowserVault(clock=lambda: self.now)

    def test_pkce_rfc7636_vector(self):
        self.assertEqual(challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_single_use_browser_bound_state_and_expiry(self):
        state, pending = self.vault.start("portal", "")
        with self.assertRaises(OAuthFailure):
            self.vault.consume(state, "other-cookie", "portal")
        with self.assertRaises(OAuthFailure):
            self.vault.consume(state, pending["cookie"], "portal")
        state, pending = self.vault.start("portal", "")
        self.now += 300
        with self.assertRaises(OAuthFailure):
            self.vault.consume(state, pending["cookie"], "portal")

    def test_token_vault_requires_same_subject_and_expires(self):
        self.vault.store("cookie", "portal", "user-A", {"id_token": "synthetic"})
        with self.assertRaises(OAuthFailure):
            self.vault.store("cookie", "mcp", "user-B", {})
        self.vault.store("cookie", "mcp", "user-A", {"access_token": "synthetic"})
        self.assertIn("mcp", self.vault.session("cookie")["tokens"])
        self.now += 900
        self.assertIsNone(self.vault.session("cookie"))
        with self.assertRaises(OAuthFailure):
            self.vault.store("cookie", "mcp", "user-A", {})


@unittest.skipUnless(AVAILABLE, "optional MCP dependencies required")
class HTTPBoundaryTests(unittest.TestCase):
    def test_logout_handoff_keeps_form_same_origin_and_requires_bound_cookie(self):
        provider = Keycloak(ISSUER, RESOURCE, CLIENTS)
        provider.revoke = AsyncMock(return_value=None)
        portal = Portal(provider, "http://localhost:18882", {})
        portal.vault.store("fixture-cookie", "portal", "user-A", {})
        csrf = portal.vault.session("fixture-cookie")["csrf"]
        with TestClient(portal.app(), base_url="http://localhost:18882", follow_redirects=False) as client:
            client.cookies.set("bom_demo", "fixture-cookie")
            response = client.post("/logout", data={"csrf": csrf}, headers={"Origin": "http://localhost:18882"})
            self.assertEqual(response.status_code, 303)
            self.assertTrue(response.headers["location"].startswith("/finish-logout?state="))
            location = response.headers["location"]
            handoff = client.get(location)
            self.assertEqual(handoff.status_code, 200)
            self.assertIn("Finish SSO sign-out", handoff.text)
            self.assertIn("form-action 'self'", handoff.headers["content-security-policy"])
            client.cookies.clear()
            self.assertEqual(client.get(location).status_code, 401)

    def test_callback_diagnostics_allow_only_fixed_categories(self):
        for category, event in (("oauth_unavailable", "callback_oauth_unavailable"),
                                ("private-provider-sentinel", "callback_unspecified")):
            provider = Keycloak(ISSUER, RESOURCE, CLIENTS)
            provider.exchange = AsyncMock(side_effect=OAuthFailure(category))
            portal = Portal(provider, "http://localhost:18882", {})
            state, pending = portal.vault.start("portal", "")
            with TestClient(portal.app(), base_url="http://localhost:18882", follow_redirects=False) as client:
                client.cookies.set("bom_demo", pending["cookie"])
                response = client.get("/callback/portal", params={"state": state, "iss": ISSUER, "code": "fixture"})
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["location"], "/error")
                self.assertIn(event, portal.events)
                self.assertNotIn("private-provider-sentinel", str(portal.events) + response.text)

    def test_browser_form_policy_preserves_same_origin_and_still_rejects_null_origin(self):
        # Fetch "append a request Origin header": no-referrer makes non-CORS
        # POST Origin null; same-origin preserves it only for same-origin targets.
        portal = Portal(Keycloak(ISSUER, RESOURCE, CLIENTS), "http://localhost:18882", {})
        with TestClient(portal.app(), base_url="http://localhost:18882") as client:
            self.assertEqual(client.get("/").headers.get("referrer-policy"), "same-origin")
            denied = client.post("/logout", data={"csrf": "synthetic-fixture"}, headers={"Origin": "null"})
            self.assertEqual(denied.status_code, 401)
            self.assertIn("post_origin_null", portal.events)

    def test_realm_seed_preserves_subject_and_resource_introspection_audience(self):
        from scripts.start_sso_demo import realm_config
        seed = realm_config({"clients": CLIENTS, "resource": RESOURCE, "origin": "http://localhost:18882",
                             "realm": "test", "user_ids": {"A": "user-A", "B": "user-B"}},
                            {"A": "synthetic-user-fixture", "B": "synthetic-user-fixture"})
        clients = {c["clientId"]: c for c in seed["clients"]}
        resource = clients[RESOURCE]
        self.assertEqual(resource["attributes"]["resource_url"], RESOURCE)
        for grant in ("standardFlowEnabled", "directAccessGrantsEnabled", "serviceAccountsEnabled"):
            self.assertFalse(resource[grant])
        mappers = clients["bom-mcp"]["protocolMappers"]
        subject = next(m for m in mappers if m["protocolMapper"] == "oidc-sub-mapper")
        self.assertEqual(subject["config"], {"access.token.claim": "true", "introspection.token.claim": "true"})
        audience = next(m for m in mappers if m["protocolMapper"] == "oidc-audience-mapper")
        self.assertEqual(audience["config"]["included.client.audience"], RESOURCE)
        self.assertNotIn("allow-token-introspection-without-audience-check", json.dumps(seed))
        for user in seed["users"]:
            self.assertTrue(user["firstName"] and user["lastName"])
            self.assertTrue(user["email"].endswith("@example.invalid"))

    def test_metadata_missing_token_wrong_scope_and_case_guard_before_read(self):
        sid = "case:" + "a" * 64
        other = "case:" + "b" * 64

        class Reader:
            reads = []
            after_read = None

            def read_snapshot(self, snapshot):
                self.reads.append(snapshot)
                if self.after_read:
                    self.after_read()
                return None

        class Provider:
            issuer, resource = ISSUER, RESOURCE
            active = True

            async def verify_token(self, raw):
                if not self.active or raw not in {"valid", "no-scope"}:
                    return None
                return AccessToken(token=raw, client_id="bom-mcp", subject="user-A", resource=RESOURCE,
                                   scopes=[READ_SCOPE] if raw == "valid" else [], claims={"iss": ISSUER})

        reader = Reader()
        grants = CaseGrants(ISSUER, {"user-A": {"tenant": "A", "role": "reader", "snapshots": [sid]}},
                            {sid: {"tenant": "A"}, other: {"tenant": "B"}})
        provider = Provider()
        app = create_http_app(EvidenceQueries(reader), provider, grants)
        with TestClient(app, base_url="http://localhost:18881") as client:
            metadata = client.get("/.well-known/oauth-protected-resource/mcp").json()
            self.assertEqual(metadata["resource"], RESOURCE)
            self.assertEqual(metadata["authorization_servers"], [ISSUER])
            denied = client.post("/mcp", json={})
            self.assertEqual(denied.status_code, 401)
            self.assertIn("resource_metadata=", denied.headers["www-authenticate"])
            self.assertEqual(client.post("/mcp", json={}, headers={"Authorization": "Bearer no-scope"}).status_code, 403)
            headers = {"Authorization": "Bearer valid", "MCP-Protocol-Version": "2026-07-28",
                       "MCP-Method": "tools/call", "MCP-Name": "get_case_gaps",
                       "Accept": "application/json, text/event-stream"}
            params = {"name": "get_case_gaps", "arguments": {"snapshot_id": other},
                      "_meta": {"io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
                                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientCapabilities": {}}}
            body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
            response = client.post("/mcp", json=body, headers=headers)
            self.assertEqual(response.json()["error"]["message"], "access_denied")
            self.assertEqual(reader.reads, [])
            params["arguments"]["snapshot_id"] = sid
            response = client.post("/mcp", json=body, headers=headers)
            self.assertEqual(response.json()["result"]["structuredContent"]["status"], "not_found")
            self.assertEqual(reader.reads, [sid])
            reader.after_read = lambda: setattr(provider, "active", False)
            response = client.post("/mcp", json=body, headers=headers)
            self.assertIn("error", response.json())
            self.assertEqual(response.json()["error"]["message"], "access_denied")
            self.assertNotIn("result", response.json())
            self.assertEqual(reader.reads, [sid, sid])

    def test_portal_cookie_headers_csrf_and_host(self):
        provider = Keycloak(ISSUER, RESOURCE, CLIENTS)
        portal = Portal(provider, "http://localhost:18882", {})
        with TestClient(portal.app(), base_url="http://localhost:18882", follow_redirects=False) as client:
            home = client.get("/")
            self.assertIn("Signed out", home.text)
            self.assertEqual(home.headers["cache-control"], "no-store")
            self.assertEqual(home.headers["referrer-policy"], "same-origin")
            login = client.get("/login/portal")
            self.assertEqual(login.status_code, 303)
            self.assertIn("HttpOnly", login.headers["set-cookie"])
            self.assertIn("SameSite=lax", login.headers["set-cookie"])
            self.assertNotIn(CLIENTS["portal"]["secret"], login.headers["location"])
            self.assertEqual(client.post("/logout", data={"csrf": "fake"}).status_code, 401)
            bad = client.get("/callback/portal?code=do-not-render&state=wrong&iss=other")
            self.assertEqual(bad.headers["location"], "/error")
            self.assertNotIn("do-not-render", bad.text)
            self.assertEqual(client.get("/", headers={"Host": "attacker.example"}).status_code, 400)

    def test_expired_access_token_can_be_reauthorized_without_logging_out(self):
        portal = Portal(Keycloak(ISSUER, RESOURCE, CLIENTS), "http://localhost:18882", {})
        portal.vault.store("synthetic-cookie", "portal", "user-A", {})
        portal.vault.store("synthetic-cookie", "mcp", "user-A", {"access_token": "expired-fixture"})
        with TestClient(portal.app(), base_url="http://localhost:18882", follow_redirects=False) as client:
            client.cookies.set("bom_demo", "synthetic-cookie")
            home = client.get("/")
            self.assertIn("Reauthorize MCP with SSO", home.text)
            self.assertEqual(client.get("/login/mcp").status_code, 303)

    def test_logout_uses_separate_oidc_state_and_browser_bound_callback(self):
        # OIDC RP-Initiated Logout section 2: state is an endpoint parameter,
        # not an unregistered dynamic component of post_logout_redirect_uri.
        from urllib.parse import parse_qs, urlsplit
        provider = Keycloak(ISSUER, RESOURCE, CLIENTS)
        provider.revoke = AsyncMock(return_value=None)
        provider.verify_token = AsyncMock(return_value=None)
        portal = Portal(provider, "http://localhost:18882", {})
        portal.vault.store("synthetic-cookie", "portal", "user-A", {})
        portal.vault.store("synthetic-cookie", "mcp", "user-A", {"access_token": "synthetic-access-fixture"})
        csrf = portal.vault.session("synthetic-cookie")["csrf"]
        with TestClient(portal.app(), base_url="http://localhost:18882", follow_redirects=False) as client:
            client.cookies.set("bom_demo", "synthetic-cookie")
            response = client.post("/logout", data={"csrf": csrf}, headers={"Origin": "http://localhost:18882"})
            self.assertEqual(response.status_code, 303)
            from scripts.verify_sso_live import Form
            handoff = client.get(response.headers["location"])
            links = Form(handoff.text).links
            self.assertEqual(len(links), 1)
            self.assertEqual(urlsplit(links[0]).path, "/realms/test/protocol/openid-connect/logout")
            parameters = parse_qs(urlsplit(links[0]).query)
            self.assertEqual(parameters["post_logout_redirect_uri"], ["http://localhost:18882/logged-out"])
            self.assertEqual(len(parameters["state"]), 1)
            state = parameters["state"][0]
            self.assertIn(state, portal.vault.logout_checks)
            self.assertIsNone(portal.vault.session("synthetic-cookie"))
            self.assertNotIn("synthetic-access-fixture", response.headers["location"] + handoff.text)
            returned = client.get("/logged-out", params={"state": state})
            self.assertEqual(returned.headers["location"], "/")
            self.assertIn("logout_replay_denied", portal.events)
            replay = client.get("/logged-out", params={"state": state})
            self.assertEqual(replay.headers["location"], "/error")

    def test_live_harness_reads_escaped_json_and_relative_logout_action(self):
        from scripts.verify_sso_live import EvidencePage, Form
        page = '<pre>{&quot;engineering_approval&quot;: false, &quot;notice&quot;: &quot;&lt;script&gt;&quot;}</pre>'
        self.assertEqual(EvidencePage(page).evidence(), {"engineering_approval": False, "notice": "<script>"})
        with self.assertRaises(ValueError):
            EvidencePage("<p>No evidence</p>").evidence()
        with self.assertRaises(ValueError):
            EvidencePage("<pre>{}</pre><pre>{}</pre>").evidence()
        form = Form('<form action="/realms/test/protocol/openid-connect/logout/logout-confirm"><input '
                    'type="hidden" name="session_code" value="synthetic-form-fixture"></form>',
                    "http://localhost:18880/realms/test/protocol/openid-connect/logout")
        self.assertEqual(form.actions, ["http://localhost:18880/realms/test/protocol/openid-connect/logout/logout-confirm"])
        self.assertEqual(form.fields, {"session_code": "synthetic-form-fixture"})

    def test_live_harness_group_diagnostics_suppress_exception_values(self):
        from scripts.verify_sso_live import safe_failure_locations
        sensitive_marker = "do-not-disclose-test-marker"
        result = safe_failure_locations(ExceptionGroup("outer", [ValueError(sensitive_marker)]))
        self.assertEqual(result[0]["type"], "ValueError")
        self.assertNotIn(sensitive_marker, json.dumps(result))
        from scripts.verify_sso_live import is_access_denial
        from mcp import MCPError, types
        denial = MCPError(code=types.INVALID_PARAMS, message="access_denied")
        self.assertTrue(is_access_denial(ExceptionGroup("sdk", [denial])))
        self.assertFalse(is_access_denial(ExceptionGroup("sdk", [denial, OSError("fixture-outage")])))
        self.assertFalse(is_access_denial(MCPError(code=types.INVALID_PARAMS, message="invalid_arguments")))


if __name__ == "__main__":
    unittest.main()
