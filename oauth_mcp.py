"""Protected optional HTTP MCP entrypoint with trusted issuer/subject grants."""

from urllib.parse import urlsplit

from mcp import types
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError

from evidence_mcp import create_server
from evidence_mcp_schema import INPUT_SCHEMA
from jsonschema import Draft202012Validator
from oauth_provider import READ_SCOPE


class CaseGrants:
    def __init__(self, issuer, identities, cases):
        self.issuer, self.identities, self.cases = issuer, identities, cases

    def allowed(self, token, snapshot):
        if token is None or not token.claims or token.claims.get("iss") != self.issuer or READ_SCOPE not in token.scopes:
            return False
        user = self.identities.get(token.subject)
        case = self.cases.get(snapshot)
        return bool(user and case and user["role"] == "reader"
                    and user["tenant"] == case["tenant"] and snapshot in user["snapshots"])


def create_http_app(queries, provider, grants):
    inputs = Draft202012Validator(INPUT_SCHEMA)

    async def authorize(arguments):
        if not inputs.is_valid(arguments):
            raise MCPError(code=types.INVALID_PARAMS, message="invalid_arguments")
        token = get_access_token()
        if (not grants.allowed(token, arguments["snapshot_id"])
                or await provider.verify_token(token.token) is None):
            raise MCPError(code=types.INVALID_PARAMS, message="access_denied")

    origin = urlsplit(provider.resource)
    server = create_server(queries, authorize=authorize)
    return server.streamable_http_app(
        json_response=True, stateless_http=True, max_request_body_size=16384,
        auth=AuthSettings(issuer_url=provider.issuer, resource_server_url=provider.resource,
                          required_scopes=[READ_SCOPE], validate_token_resource=True),
        token_verifier=provider,
        transport_security=TransportSecuritySettings(allowed_hosts=[origin.netloc],
                                                      allowed_origins=[f"{origin.scheme}://{origin.netloc}"]))
