"""Opt-in real HTTP chain using synthetic accounts; this is not Chrome UI evidence."""

import argparse
from html.parser import HTMLParser
from http.cookiejar import DefaultCookiePolicy
import json
from pathlib import Path
import secrets
import sys
from urllib.parse import parse_qs, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Form(HTMLParser):
    def __init__(self, page, base_url=""):
        super().__init__()
        self.base_url = base_url
        self.actions, self.fields, self.links = [], {}, []
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "form" and attrs.get("action"):
            self.actions.append(urljoin(self.base_url, attrs["action"]))
        if tag == "a" and attrs.get("href"):
            self.links.append(urljoin(self.base_url, attrs["href"]))
        if tag == "input" and attrs.get("name") and attrs.get("type") == "hidden":
            self.fields[attrs["name"]] = attrs.get("value", "")


class EvidencePage(HTMLParser):
    """Decode the displayed JSON, not its escaped HTML representation."""

    def __init__(self, page):
        super().__init__()
        self.blocks, self.current = [], None
        self.feed(page)

    def handle_starttag(self, tag, attributes):
        if tag == "pre":
            self.current = []
            self.blocks.append(self.current)

    def handle_endtag(self, tag):
        if tag == "pre":
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def evidence(self):
        if len(self.blocks) != 1:
            raise ValueError("one_displayed_evidence_block_required")
        return json.loads("".join(self.blocks[0]))


class LoopbackCookiePolicy(DefaultCookiePolicy):
    """Test-client substitute for Chromium's Secure-cookie localhost exception."""

    def return_ok_secure(self, cookie, request):
        if (urlsplit(request.get_full_url()).hostname == "localhost"
                and cookie.domain in {"localhost", "localhost.local"}):
            return True
        return super().return_ok_secure(cookie, request)


def safe_failure_locations(error):
    """Unwrap SDK task groups without exposing exception/request values."""
    pending, leaves = [error], []
    while pending and len(leaves) < 16:
        current = pending.pop()
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions[:16])
            continue
        trace, chosen = current.__traceback__, None
        while trace:
            if chosen is None or Path(trace.tb_frame.f_code.co_filename).name == Path(__file__).name:
                chosen = trace
            trace = trace.tb_next
        leaves.append({"type": type(current).__name__,
                       "file": Path(chosen.tb_frame.f_code.co_filename).name if chosen else None,
                       "line": chosen.tb_lineno if chosen else None,
                       "known_access_denial": getattr(current, "message", None) == "access_denied"})
    return leaves


def is_access_denial(error):
    """Only the expected protocol denial may pass; outages remain failures."""
    from mcp import MCPError, types
    if isinstance(error, BaseExceptionGroup):
        return all(is_access_denial(item) for item in error.exceptions)
    return isinstance(error, MCPError) and error.message == "access_denied" and error.code == types.INVALID_PARAMS


async def verify(directory, agent_fixture=False, portal_agent_fixture=False):
    import anyio
    import httpx2
    from mcp import Client, MCPError
    from mcp.client.streamable_http import streamable_http_client
    from oauth_provider import Keycloak, READ_SCOPE
    from sso_portal import challenge
    import neo4j_graph as graph

    config = json.loads((directory / "services.private.json").read_text(encoding="utf-8"))
    accounts = json.loads((directory / "accounts.private.json").read_text(encoding="utf-8"))
    provider = Keycloak(config["issuer"], config["resource"], config["clients"])
    db = graph.Client(config["neo4j_password"], 18883)

    def durable_state():
        return graph.review.fingerprint({"nodes": db.query("MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id"),
            "edges": db.query("MATCH (a)-[r]->(b) RETURN a.id AS a, type(r) AS kind, b.id AS b, properties(r) AS props ORDER BY a, kind, b")})

    before = await anyio.to_thread.run_sync(durable_state)
    observations = []
    async with httpx2.AsyncClient(timeout=40, trust_env=False, follow_redirects=True) as browser:
        browser.cookies.jar.set_policy(LoopbackCookiePolicy())

        async def local_cookies(request):
            # Prevent the test-only local Cookie policy from ever reaching a third party.
            if request.url.host != "localhost" or request.url.port not in {18880, 18881, 18882}:
                raise RuntimeError("unexpected_test_origin")
            request.headers.pop("cookie", None)
            browser.cookies.set_cookie_header(request)

        browser.event_hooks["request"].append(local_cookies)
        unauth = await browser.post(config["resource"], json={})
        assert unauth.status_code == 401 and "resource_metadata" in unauth.headers["www-authenticate"]
        metadata = (await browser.get(config["resource"].replace("/mcp", "/.well-known/oauth-protected-resource/mcp"))).json()
        assert metadata["resource"] == config["resource"] and metadata["authorization_servers"] == [config["issuer"]]
        observations.append("unauthenticated_401_and_resource_discovery")

        for name, own_status in (("A", "matches_requirement"), ("B", "violates_requirement")):
            first = await browser.get(config["origin"] + "/login/portal")
            login = Form(first.text)
            actions = [a for a in login.actions if a.startswith(config["issuer"] + "/login-actions/authenticate?")]
            assert len(actions) == 1, "real_login_form_missing"
            signed_in = await browser.post(actions[0], data={**login.fields, **accounts[name], "credentialId": ""})
            # Older owned seed files lacked the mandatory synthetic profile fields.
            # Complete only this named test user's local required action, not a real profile.
            if "Update Account Information" in signed_in.text:
                profile = Form(signed_in.text)
                actions = [a for a in profile.actions if a.startswith(config["issuer"] + "/login-actions/required-action?")]
                assert len(actions) == 1
                signed_in = await browser.post(actions[0], data={**profile.fields, "firstName": "Synthetic",
                    "lastName": "Reader " + name, "email": "demo-" + name.lower() + "@example.invalid"})
                observations.append(name + "_old_seed_synthetic_profile_completed")
            assert signed_in.url.path == "/" and "Portal SSO authenticated" in signed_in.text
            second = await browser.get(config["origin"] + "/login/mcp")
            assert second.url.path == "/" and "MCP authorized" in second.text, "second_client_sso_failed"
            csrf = Form(second.text).fields["csrf"]
            for case in ("A", "B"):
                response = await browser.post(config["origin"] + "/read/" + case, data={"csrf": csrf},
                                              headers={"Origin": config["origin"]})
                if case == name:
                    assert response.status_code == 200
                    displayed = EvidencePage(response.text).evidence()
                    assert displayed["data"]["assessment_status"] == own_status
                    assert displayed["data"]["snapshot"]["overall"] == "needs_engineering_review"
                    assert displayed["engineering_approval"] is False
                else:
                    assert response.status_code == 403 and "No case evidence was disclosed" in response.text
                    assert config["cases"][case] not in response.text
            observations.append(name + "_two_client_sso_and_case_isolation")

            if portal_agent_fixture:
                assert "Review case " in second.text and "Bounded fixture model mode" in second.text
                for case in ("A", "B"):
                    response = await browser.post(config["origin"] + "/review/" + case,
                        data={"csrf": csrf}, headers={"Origin": config["origin"]})
                    if case != name:
                        assert response.status_code == 403 and "No case evidence was disclosed" in response.text
                        assert config["cases"][case] not in response.text
                    else:
                        assert response.status_code == 200
                        displayed = EvidencePage(response.text).evidence()
                        assert displayed["paid_model_calls"] == 0 and displayed["engineering_approval"] is False
                        if name == "A":
                            assert displayed["status"] == "completed" and displayed["answer"]["result"] == own_status
                            assert displayed["model_usage"]["provider_attempts"] == 2
                        else:
                            # One shared fixture trial has only two attempts, already used by A.
                            assert displayed["status"] == "stopped" and displayed["reason"] == "model_call_limit"
                            assert displayed["answer"] is None and displayed["model_usage"]["provider_attempts"] == 0
                observations.append(name + "_real_portal_agent_route_and_trial_limit")

            # Independently obtain a real MCP access token using the same SSO cookie
            # and a valid code+PKCE exchange. Do not forward callback into the BFF.
            state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
            authorization = await browser.get(provider.authorize_url("mcp", state, nonce, challenge(verifier)), follow_redirects=False)
            assert authorization.status_code in (302, 303)
            params = parse_qs(urlsplit(authorization.headers["location"]).query)
            assert params["state"] == [state] and params["iss"] == [provider.issuer]
            tokens, subject = await provider.exchange("mcp", params["code"][0], verifier, nonce)
            assert subject == config["user_ids"][name]

            review = None
            if agent_fixture:
                from oauth_mcp import CaseGrants
                from sso_agent import review_factory
                from agent_control import StopRun
                from live_model import TrialBudget
                # Separate synthetic fixture trials, never a reset of a paid ledger.
                trial_path = directory / ("agent-fixture-" + name + "-" + secrets.token_hex(6) + ".sqlite")
                grants = CaseGrants(config["issuer"], config["identities"], config["case_grants"])
                review = review_factory(provider, grants, mode="fixture", budget_path=trial_path)
                allowed = await review.execute(tokens["access_token"], subject, config["cases"][name], lambda: True)
                assert allowed["status"] == "completed" and allowed["answer"]["result"] == own_status
                assert allowed["answer"]["overall"] == "needs_engineering_review"
                assert allowed["paid_model_calls"] == 0 and allowed["model_usage"]["provider_attempts"] == 2
                budget = TrialBudget(trial_path, "0.02", account="sso-trial")
                balance = budget.snapshot()
                try:
                    await review.execute(tokens["access_token"], subject,
                        config["cases"]["B" if name == "A" else "A"], lambda: True)
                except StopRun as error:
                    assert str(error) == "evidence_unavailable"
                else:
                    raise AssertionError("cross_case_agent_not_denied")
                assert budget.snapshot() == balance, "denial_changed_budget"
                observations.append(name + "_real_authorized_workflow_fixture_model_and_cross_case_denial")

            async def tool_call(raw, case):
                async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + raw}, timeout=30, trust_env=False) as http:
                    async with Client(streamable_http_client(provider.resource, http_client=http), cache=None) as client:
                        return await client.call_tool("get_case_gaps", {"snapshot_id": config["cases"][case]})
            result = await tool_call(tokens["access_token"], name)
            assert result.structured_content["data"]["assessment_status"] == own_status
            try:
                denied = await tool_call(tokens["access_token"], "B" if name == "A" else "A")
                assert denied.is_error and denied.structured_content is None
            except (MCPError, BaseExceptionGroup) as error:
                assert is_access_denial(error)

            # Validly signed ID token is not a resource access token.
            wrong = await browser.post(provider.resource, json={}, headers={"Authorization": "Bearer " + tokens["id_token"]})
            assert wrong.status_code == 401
            assert await provider.verify_token(tokens["access_token"]) is not None
            observations.append(name + "_real_mcp_token_and_id_token_denial")

            logged_out = await browser.post(config["origin"] + "/logout", data={"csrf": csrf},
                                            headers={"Origin": config["origin"]})
            if logged_out.url.path == "/finish-logout":
                links = [link for link in Form(logged_out.text).links
                         if link.startswith(provider.endpoint + "/logout?")]
                assert len(links) == 1, "bound_logout_handoff_missing"
                logged_out = await browser.get(links[0])
            if "Signed out" not in logged_out.text:
                logout_form = Form(logged_out.text, str(logged_out.url))
                actions = [a for a in logout_form.actions if a.startswith(provider.endpoint + "/logout")]
                assert len(actions) == 1, "logout_confirmation_missing"
                logged_out = await browser.post(actions[0], data=logout_form.fields)
            assert logged_out.url.path == "/" and "Signed out" in logged_out.text
            replay = await browser.post(provider.resource, json={}, headers={"Authorization": "Bearer " + tokens["access_token"]})
            assert replay.status_code == 401, "retained_access_token_still_active"
            if review is not None:
                try:
                    await review.execute(tokens["access_token"], subject, config["cases"][name], lambda: True)
                except StopRun as error:
                    assert str(error) == "evidence_unavailable"
                else:
                    raise AssertionError("revoked_agent_not_denied")
                assert budget.snapshot() == balance, "revocation_changed_budget"
            observations.append(name + "_logout_retained_token_401")
            # Cookie cleared and IdP actually requires a password again.
            reenter = await browser.get(config["origin"] + "/login/portal")
            assert any(a.startswith(config["issuer"] + "/login-actions/authenticate?") for a in Form(reenter.text).actions)
            browser.cookies.clear()

    after = await anyio.to_thread.run_sync(durable_state)
    assert after == before, "read_only_graph_changed"
    observations.append("full_graph_properties_unchanged")
    return {"schema_version": "sso-live-v1", "observations": observations, "result": "passed",
            "boundary": "real_http_keycloak_bff_mcp_neo4j_not_chrome",
            "agent_workflow": "real_auth_mcp_neo4j_with_fixture_model" if agent_fixture else "not_exercised",
            "portal_agent_route": "real_http_with_fixture_model" if portal_agent_fixture else "not_exercised",
            "substituted_boundary": "test_client_secure_cookie_localhost_policy_not_browser_cookie_evidence",
            "paid_model_calls": 0,
            "engineering_approval": False, "synthetic_only": True}


def main():
    if not __debug__:
        raise SystemExit("verification requires non-optimized Python; assertions must execute")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated-output", type=Path, required=True)
    parser.add_argument("--agent-fixture", action="store_true", help="Exercise authenticated workflow with a non-paid model")
    parser.add_argument("--portal-agent-fixture", action="store_true", help="Use a configured fresh shared fixture trial through HTTP portal")
    args = parser.parse_args()
    directory = args.isolated_output.resolve()
    if directory.parent != ROOT / "output" or not directory.name.startswith("sso-demo-"):
        parser.error("owned local demo output required")
    import anyio
    try:
        result = anyio.run(verify, directory, args.agent_fixture, args.portal_agent_fixture)
    except Exception as error:
        # No assertion values, HTTP request URLs or tokens in failure logs.
        trace = error.__traceback__
        while trace and trace.tb_next:
            trace = trace.tb_next
        result = {"result": "failed", "failure_type": type(error).__name__,
                  "failure_file": Path(trace.tb_frame.f_code.co_filename).name if trace else None,
                  "failure_line": trace.tb_lineno if trace else None, "boundary": "real_http_not_chrome",
                  "safe_failure_locations": safe_failure_locations(error)}
    path = directory / ("portal-agent-verification.json" if args.portal_agent_fixture else
                        "agent-fixture-verification.json" if args.agent_fixture else "live-verification.json")
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["result"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
