"""Fixed local Keycloak adapter; no dynamic issuer or client registration."""

import time
from urllib.parse import urlencode, urlsplit

import anyio
import httpx2
import jwt
from mcp.server.auth.provider import AccessToken


READ_SCOPE = "bom:evidence:read"


class OAuthFailure(Exception):
    """Fixed public category, never provider response text."""


def loopback_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "localhost" or not parsed.port
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("fixed_loopback_url_required")
    return value.rstrip("/")


class Keycloak:
    def __init__(self, issuer, resource, clients):
        self.issuer = loopback_url(issuer)
        self.resource = loopback_url(resource)
        self.clients = clients
        self.endpoint = self.issuer + "/protocol/openid-connect"
        self.limiter = anyio.CapacityLimiter(4)

    async def request(self, path, form=None):
        # Fixed endpoints only; no cookies, proxy environment, redirects or retry.
        if path not in {"certs", "token", "token/introspect", "revoke"}:
            raise OAuthFailure("oauth_unavailable")
        try:
            async with httpx2.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
                response = (await client.get(self.endpoint + "/" + path) if form is None
                            else await client.post(self.endpoint + "/" + path, data=form))
                if response.status_code != 200 or len(response.content) > 128 * 1024:
                    raise OAuthFailure("oauth_unavailable")
                result = {} if not response.content else response.json()
                if not isinstance(result, dict):
                    raise OAuthFailure("oauth_unavailable")
                return result
        except (httpx2.HTTPError, ValueError):
            raise OAuthFailure("oauth_unavailable") from None

    def credentials(self, client_name):
        client = self.clients[client_name]
        return {"client_id": client["id"], "client_secret": client["secret"]}

    async def claims(self, token, audience):
        if not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise OAuthFailure("invalid_token")
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
                raise OAuthFailure("invalid_token")
            jwks = await self.request("certs")
            keys = [key for key in jwks.get("keys", []) if key.get("kid") == header["kid"]]
            if len(keys) != 1:
                raise OAuthFailure("invalid_token")
            key = jwt.PyJWK.from_dict(keys[0], algorithm="RS256").key
            claims = jwt.decode(token, key, algorithms=["RS256"], issuer=self.issuer, audience=audience,
                                options={"require": ["iss", "sub", "aud", "exp", "iat"]})
            if (not isinstance(claims["sub"], str) or not claims["sub"]
                    or type(claims["exp"]) is not int or type(claims["iat"]) is not int):
                raise OAuthFailure("invalid_token")
            return claims
        except (jwt.PyJWTError, KeyError, TypeError, ValueError):
            raise OAuthFailure("invalid_token") from None

    def authorize_url(self, name, state, nonce, challenge):
        client = self.clients[name]
        params = {"client_id": client["id"], "response_type": "code",
                  "redirect_uri": client["redirect"], "scope": "openid",
                  "state": state, "nonce": nonce, "code_challenge": challenge,
                  "code_challenge_method": "S256"}
        if name == "mcp":
            params.update(scope="openid " + READ_SCOPE, resource=self.resource)
        return self.endpoint + "/auth?" + urlencode(params)

    async def exchange(self, name, code, verifier, nonce):
        form = dict(self.credentials(name), grant_type="authorization_code", code=code,
                    redirect_uri=self.clients[name]["redirect"], code_verifier=verifier)
        if name == "mcp":
            form["resource"] = self.resource
        tokens = await self.request("token", form)
        claims = await self.claims(tokens.get("id_token"), self.clients[name]["id"])
        if claims.get("nonce") != nonce:
            raise OAuthFailure("invalid_token")
        if name == "mcp" and await self.verify_token(tokens.get("access_token")) is None:
            raise OAuthFailure("invalid_token")
        return tokens, claims["sub"]

    async def verify_token(self, token):
        try:
            self.limiter.acquire_nowait()
        except anyio.WouldBlock:
            return None
        try:
            return await self._verify_token(token)
        finally:
            self.limiter.release()

    async def _verify_token(self, token):
        try:
            claims = await self.claims(token, self.resource)
            audience = claims["aud"]
            if (audience not in (self.resource, [self.resource]) or claims.get("typ") != "Bearer"
                    or claims.get("azp") != self.clients["mcp"]["id"]):
                return None
            active = await self.request("token/introspect", dict(self.credentials("resource"), token=token))
            if (active.get("active") is not True or active.get("sub") != claims["sub"]
                    or active.get("exp") != claims["exp"] or claims["exp"] <= int(time.time())):
                return None
            scopes = claims.get("scope", "").split()
            return AccessToken(token=token, client_id=claims["azp"], scopes=scopes,
                               expires_at=claims["exp"], resource=self.resource,
                               subject=claims["sub"], claims={"iss": self.issuer})
        except (OAuthFailure, AttributeError, TypeError):
            return None

    async def revoke(self, name, tokens):
        if tokens.get("refresh_token"):
            await self.request("revoke", dict(self.credentials(name), token=tokens["refresh_token"],
                                              token_type_hint="refresh_token"))
