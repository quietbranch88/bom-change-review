"""Local synthetic login demo. No network, model charges, raw-token output or real users."""

import argparse
import asyncio
import getpass
import json
from pathlib import Path
import secrets
import sys

from authenticated_review import AuthenticatedReview
from auth_contract import AuthError
from demo_service import DemoService
from evidence_queries import EvidenceQueries
from examples.agent_control.provider_fixture import FixtureProvider
import interview_demo
import neo4j_graph
from openrouter_planner import ProviderPlanner
from projection_adapters import FixtureProjectionTarget, expected_view
from projection_sync import SyncInput
from sqlite_auth import SQLiteAuth
from sqlite_budget import SQLiteBudget
from model_budget import BudgetError
from system_demo import DelayedTools, FixtureReader


def compose(directory, passwords):
    """Fresh private synthetic store. No runtime imports from tests or user-key reads."""
    target = FixtureProjectionTarget()
    bundle = interview_demo.create_bundle(True)
    snapshots = []
    for stage in ("tied/reviewed", "internal/reviewed"):
        projection = neo4j_graph.project_demo(bundle, stage)
        target.apply(SyncInput(projection["snapshot_id"], projection, expected_view(projection)))
        snapshots.append(projection["snapshot_id"])
    auth = SQLiteAuth.initialize(directory / "auth.sqlite",
        [("SIM-reader-A", "SIM-tenant-A", "reader", passwords[0]),
         ("SIM-reviewer-B", "SIM-tenant-B", "reviewer", passwords[1])],
        [(snapshots[0], "SIM-tenant-A"), (snapshots[1], "SIM-tenant-B")],
        [("SIM-reader-A", snapshots[0]), ("SIM-reviewer-B", snapshots[1])])
    budget = SQLiteBudget(directory / "budget.sqlite", "0.02", account="auth-demo")
    planners = []

    def planner_factory(principal):
        # Shared synthetic budget; authenticated identity does not imply billing ownership.
        planner = ProviderPlanner(FixtureProvider("success"), budget)
        planners.append(planner)
        return planner

    admission = DemoService({"SIM-reader-A": {snapshots[0]}, "SIM-reviewer-B": {snapshots[1]}})
    tools = DelayedTools(EvidenceQueries(FixtureReader(target)), 0)
    service = AuthenticatedReview(auth, admission, planner_factory, tools)
    return auth, service, snapshots, budget, planners


async def demonstrate(directory):
    passwords = [secrets.token_urlsafe(24), secrets.token_urlsafe(24)]
    auth, service, snapshots, budget, planners = compose(directory, passwords)
    first = auth.login("SIM-reader-A", passwords[0])
    second = auth.login("SIM-reviewer-B", passwords[1])
    question = "What evidence and next actions remain?"
    results = {}
    results["anonymous"] = await service.execute(None, snapshots[0], question)
    before = len(planners)
    results["cross_tenant"] = await service.execute(second, snapshots[0], question)
    refused_before_planner = len(planners) == before
    results["owner"] = await service.execute(first, snapshots[0], question)
    results["reviewer_own_case"] = await service.execute(second, snapshots[1], question)
    try:
        auth.login("SIM-reader-A", secrets.token_urlsafe(24))
        bad_password = "unexpected_success"
    except AuthError as error:
        bad_password = str(error)
    auth.logout(first)
    results["logged_out_replay"] = await service.execute(first, snapshots[0], question)
    auth.logout(second)
    return {"mode": "local_synthetic_accounts_real_sqlite_auth", "results": results,
            "wrong_password": bad_password, "cross_tenant_zero_planner_dispatch": refused_before_planner,
            "planner_instances": len(planners), "budget": budget.snapshot(),
            "raw_credentials_or_tokens_in_output": False, "graph": "fixture",
            "model": "local_provider_fixture", "paid_model_calls": 0, "engineering_approval": False}


async def interactive(directory):
    if not sys.stdin.isatty():
        raise AuthError("interactive_terminal_required")
    print("Synthetic local accounts only. Do not reuse a real password. 15 to 128 characters.")
    passwords = [getpass.getpass("Set SIM-reader-A password: "), getpass.getpass("Set SIM-reviewer-B password: ")]
    auth, service, snapshots, _, _ = compose(directory, passwords)
    passwords.clear()
    token = None
    try:
        while True:
            action = input("login / case-a / case-b / logout / exit: ").strip()
            if action == "exit":
                break
            try:
                if action == "login":
                    if token is not None:
                        auth.logout(token)
                        token = None
                    username = input("Account (SIM-reader-A or SIM-reviewer-B): ").strip()
                    token = auth.login(username, getpass.getpass("Password: "))
                    print("Logged in. Session stays in memory; it is never printed.")
                elif action == "logout":
                    if token is not None:
                        auth.logout(token)
                        token = None
                    print("Logged out.")
                elif action in ("case-a", "case-b"):
                    result = await service.execute(token, snapshots[0 if action == "case-a" else 1],
                                                   "What evidence and next actions remain?")
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("Unknown command.")
            except AuthError as error:
                print(json.dumps({"error": str(error)}))
    finally:
        if token is not None:
            auth.logout(token)
    return {"mode": "local_interactive_synthetic_login", "paid_model_calls": 0, "engineering_approval": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--simulate-users", action="store_true")
    mode.add_argument("--interactive", action="store_true")
    parser.add_argument("--out", type=Path, required=True, help="New private output directory; never overwrite")
    args = parser.parse_args()
    try:
        args.out.mkdir(parents=True, exist_ok=False)
        result = asyncio.run(interactive(args.out) if args.interactive else demonstrate(args.out))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.simulate_users:
            checks = (result["wrong_password"] == "invalid_credentials",
                      result["cross_tenant_zero_planner_dispatch"], result["planner_instances"] == 2,
                      all(result["results"][key]["status"] == "denied"
                          for key in ("anonymous", "cross_tenant", "logged_out_replay")),
                      all((result["results"][key].get("answer") or {}).get("status") == "completed"
                          for key in ("owner", "reviewer_own_case")))
            return 0 if all(checks) else 2
        return 0
    except (AuthError, BudgetError, OSError, ValueError, EOFError, KeyboardInterrupt):
        print(json.dumps({"error": "local_auth_demo_failed", "paid_model_calls": 0, "engineering_approval": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
