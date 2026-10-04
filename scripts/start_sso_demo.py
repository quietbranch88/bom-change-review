"""Start owned local synthetic IdP/Neo4j and two Python services; never publish secrets.

Creates private output files and fresh containers. No deletion or shared database.
Ctrl+C stops only these owned containers; generated files remain for checked cleanup.
"""

import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEYCLOAK = "quay.io/keycloak/keycloak@sha256:b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc"
NEO4J = "neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e"
DOCKER = ["rtk", "proxy", "docker"] if shutil.which("rtk") else ["docker"]
PORTS = {"idp": 18880, "mcp": 18881, "portal": 18882, "neo4j": 18883}


def realm_config(config, passwords):
    clients = []
    for name, client in config["clients"].items():
        if name == "resource":
            continue
        clients.append({"clientId": client["id"], "secret": client["secret"], "protocol": "openid-connect",
                        "publicClient": False, "standardFlowEnabled": True, "directAccessGrantsEnabled": False,
                        "redirectUris": [client["redirect"]], "webOrigins": [],
                        "attributes": {"pkce.code.challenge.method": "S256",
                                       "post.logout.redirect.uris": config["origin"] + "/logged-out*"},
                        "defaultClientScopes": [],
                        "optionalClientScopes": ["bom:evidence:read"] if name == "mcp" else [],
                        "protocolMappers": [{"name": "subject", "protocol": "openid-connect",
                             "protocolMapper": "oidc-sub-mapper", "config": {
                                 "access.token.claim": "true", "introspection.token.claim": "true"}}]
                        + ([{"name": "mcp-audience", "protocol": "openid-connect",
                             "protocolMapper": "oidc-audience-mapper", "config": {
                                 "included.client.audience": config["resource"], "id.token.claim": "false",
                                 "access.token.claim": "true"}}] if name == "mcp" else [])})
    clients.append({"clientId": config["resource"], "secret": config["clients"]["resource"]["secret"],
                    "protocol": "openid-connect", "publicClient": False,
                    "standardFlowEnabled": False, "directAccessGrantsEnabled": False,
                    "serviceAccountsEnabled": False,
                    "attributes": {"resource_url": config["resource"]}})
    users = [{"id": config["user_ids"][name], "username": "demo-" + name.lower(), "enabled": True,
              "firstName": "Synthetic", "lastName": "Reader " + name,
              "email": "demo-" + name.lower() + "@example.invalid", "emailVerified": True,
              "credentials": [{"type": "password", "value": passwords[name], "temporary": False}]}
             for name in ("A", "B")]
    return {"realm": config["realm"], "enabled": True, "sslRequired": "none",
            "registrationAllowed": False, "resetPasswordAllowed": False, "rememberMe": False,
            "accessTokenLifespan": 120, "ssoSessionIdleTimeout": 900, "ssoSessionMaxLifespan": 900,
            "revokeRefreshToken": True, "refreshTokenMaxReuse": 0,
            "clientScopes": [{"name": "bom:evidence:read", "protocol": "openid-connect",
                              "attributes": {"include.in.token.scope": "true"}}],
            "clients": clients, "users": users}


def command(args, env=None):
    result = subprocess.run(args, env=env, cwd=ROOT, capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError("demo_command_failed")
    return result


def launch_service(kind, path, *, allow_paid=False):
    logging.disable(logging.CRITICAL)
    import uvicorn
    from evidence_graph_reader import Neo4jSnapshotReader
    from evidence_queries import EvidenceQueries
    from oauth_provider import Keycloak
    from oauth_mcp import CaseGrants, create_http_app
    from sso_portal import Portal
    config = json.loads(path.read_text(encoding="utf-8"))
    provider = Keycloak(config["issuer"], config["resource"], config["clients"])
    if kind == "mcp":
        reader = Neo4jSnapshotReader(config["neo4j_password"], PORTS["neo4j"])
        grants = CaseGrants(config["issuer"], config["identities"], config["case_grants"])
        app = create_http_app(EvidenceQueries(reader), provider, grants)
    else:
        review = None
        if config.get("agent"):
            from sso_agent import review_factory
            settings = config["agent"]
            # Trusted local startup only; HTTP input cannot select mode or budget.
            budget_path = (path.parent / settings["budget_file"]).resolve()
            if budget_path.parent != path.parent.resolve() or not budget_path.name.endswith(".sqlite"):
                raise ValueError("owned_budget_path_required")
            grants = CaseGrants(config["issuer"], config["identities"], config["case_grants"])
            review = review_factory(provider, grants, mode=settings["mode"], budget_path=budget_path,
                                    allow_paid=allow_paid)
        app = Portal(provider, config["origin"], config["cases"], review=review).app()
    uvicorn.run(app, host="127.0.0.1", port=PORTS[kind], access_log=False, log_config=None,
                limit_concurrency=16)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--service", choices=["mcp", "portal"])
    parser.add_argument("--config", type=Path)
    parser.add_argument("--allow-paid", action="store_true", help="Explicitly allow configured synthetic live trial")
    parser.add_argument("--agent-mode", choices=["fixture", "live"], help="Optional synthetic agent for a fresh isolated run")
    args = parser.parse_args()
    if args.service:
        if not args.config:
            parser.error("service requires private config")
        launch_service(args.service, args.config, allow_paid=args.allow_paid)
        return
    if not args.isolated:
        parser.error("explicit --isolated required")
    if args.agent_mode == "live" and not args.allow_paid:
        parser.error("live agent requires explicit --allow-paid and a verified dedicated key")
    for port in PORTS.values():
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    suffix = uuid.uuid4().hex[:12]
    directory = ROOT / "output" / ("sso-demo-" + suffix)
    directory.mkdir(parents=True, exist_ok=False)
    origin = f"http://localhost:{PORTS['portal']}"
    realm = "bom-demo-" + suffix
    config = {"realm": realm, "issuer": f"http://localhost:{PORTS['idp']}/realms/{realm}",
              "resource": f"http://localhost:{PORTS['mcp']}/mcp", "origin": origin,
              "user_ids": {name: str(uuid.uuid4()) for name in ("A", "B")},
              "neo4j_password": secrets.token_urlsafe(32), "clients": {}}
    if args.agent_mode:
        config["agent"] = {"mode": args.agent_mode, "budget_file": "agent-trial.sqlite"}
    for name in ("portal", "mcp"):
        config["clients"][name] = {"id": "bom-" + name + "-client", "secret": secrets.token_urlsafe(32),
                                  "redirect": origin + "/callback/" + name}
    config["clients"]["resource"] = {"id": config["resource"], "secret": secrets.token_urlsafe(32)}
    passwords = {name: secrets.token_urlsafe(24) for name in ("A", "B")}
    realm_path = directory / "realm.private.json"
    realm_path.write_text(json.dumps(realm_config(config, passwords)), encoding="utf-8")
    credential_path = directory / "accounts.private.json"
    credential_path.write_text(json.dumps({name: {"username": "demo-" + name.lower(), "password": passwords[name]}
                                           for name in ("A", "B")}), encoding="utf-8")
    names = {kind: "bom-sso-" + kind + "-" + suffix for kind in ("idp", "neo4j")}
    started, children, logs = [], [], []
    receipt = {"schema_version": "sso-demo-start-v1", "containers": names, "images": {"idp": KEYCLOAK, "neo4j": NEO4J},
               "ports": PORTS, "synthetic_only": True,
               "model_mode": args.agent_mode or "disabled",
               "paid_model_calls": None if args.agent_mode == "live" else 0,
               "billing_counter": "not_available_use_trial_ledger", "status": "starting"}
    receipt_path = directory / "startup.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    try:
        env = dict(os.environ, NEO4J_AUTH="neo4j/" + config["neo4j_password"])
        started.append(names["neo4j"])
        command([*DOCKER, "run", "-d", "--pull", "never", "--name", names["neo4j"],
                 "--label", "bom-change-review=sso-isolated", "--memory", "2g", "--cpus", "2",
                 "--publish", f"127.0.0.1:{PORTS['neo4j']}:7474", "--env", "NEO4J_AUTH",
                 "--env", "NEO4J_server_memory_heap_initial__size=256m",
                 "--env", "NEO4J_server_memory_heap_max__size=512m",
                 "--env", "NEO4J_server_memory_pagecache_size=256m",
                 "--env", "NEO4J_dbms_usage__report_enabled=false", NEO4J], env)
        started.append(names["idp"])
        command([*DOCKER, "run", "-d", "--pull", "never", "--name", names["idp"],
                 "--label", "bom-change-review=sso-isolated", "--memory", "1g", "--cpus", "2",
                 "--publish", f"127.0.0.1:{PORTS['idp']}:8080",
                 "--mount", f"type=bind,source={realm_path},target=/opt/keycloak/data/import/realm.json,readonly",
                 KEYCLOAK, "start-dev", "--import-realm", "--features=resource-indicators",
                 "--hostname", f"http://localhost:{PORTS['idp']}", "--http-enabled=true", "--health-enabled=true"])
        print("Owned Keycloak and Neo4j containers started; bounded readiness check (up to 900s).", flush=True)
        import http.client
        import neo4j_graph as graph
        import interview_demo
        client = graph.Client(config["neo4j_password"], PORTS["neo4j"])
        deadline = time.monotonic() + 900
        while True:
            try:
                client.verify_engine()
                connection = http.client.HTTPConnection("localhost", PORTS["idp"], timeout=5)
                try:
                    connection.request("GET", "/realms/" + realm + "/.well-known/openid-configuration")
                    response = connection.getresponse()
                    if response.status != 200:
                        raise RuntimeError("issuer_not_ready")
                    discovery = json.loads(response.read(128 * 1024))
                    if discovery.get("issuer") != config["issuer"]:
                        raise RuntimeError("issuer_mismatch")
                finally:
                    connection.close()
                break
            except (OSError, http.client.HTTPException, graph.GraphError, RuntimeError, ValueError):
                if time.monotonic() >= deadline:
                    raise RuntimeError("demo_readiness_timeout") from None
                time.sleep(2)
        client.initialize()
        bundle = interview_demo.create_bundle(True)
        config["cases"] = {}
        config["case_grants"] = {}
        config["identities"] = {}
        for name, stage in (("A", "internal/reviewed"), ("B", "tied/reviewed")):
            projection = graph.project_demo(bundle, stage)
            client.import_projection(projection)
            sid = projection["snapshot_id"]
            config["cases"][name] = sid
            config["case_grants"][sid] = {"tenant": "demo-tenant-" + name}
            config["identities"][config["user_ids"][name]] = {"tenant": "demo-tenant-" + name,
                                                              "role": "reader", "snapshots": [sid]}
        config_path = directory / "services.private.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        for kind in ("mcp", "portal"):
            log = (directory / (kind + ".log")).open("xb")
            logs.append(log)
            service_args = [sys.executable, str(Path(__file__).resolve()), "--service", kind,
                            "--config", str(config_path)]
            if kind == "portal" and args.allow_paid:
                service_args.append("--allow-paid")
            children.append(subprocess.Popen(service_args, cwd=ROOT, stdout=log, stderr=log))
        receipt.update(status="services_starting", directory=str(directory), origin=origin,
                       process_ids=[child.pid for child in children],
                       resource=config["resource"], issuer=config["issuer"], cases=config["cases"])
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print("Services starting: " + origin + " | private credentials: " + str(credential_path), flush=True)
        while True:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("demo_service_exited")
            time.sleep(2)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=15)
        for log in logs:
            log.close()
        for name in reversed(started):
            command([*DOCKER, "stop", name])
        receipt["status"] = "stopped"
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        print("sso_demo_failed; see private startup receipt, never publish private configuration", file=sys.stderr)
        raise SystemExit(2)
