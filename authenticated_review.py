"""Session-derived identity -> bounded admission -> guarded read-only controller."""

from auth_contract import AuthError, Authorization
from agent_control import run, StopRun
from evidence_queries import TOOLS


class AuthorizedTools:
    def __init__(self, auth: Authorization, token, snapshot_id, principal, tools):
        self.auth, self.token, self.snapshot_id = auth, token, snapshot_id
        self.principal, self.tools = principal, tools

    def check(self):
        if self.auth.authorize(self.token, self.snapshot_id) != self.principal:
            raise AuthError("authentication_required")

    async def call(self, name, arguments):
        if name not in TOOLS or arguments != {"snapshot_id": self.snapshot_id}:
            raise StopRun("invalid_tool_arguments")
        self.check()
        result = await self.tools.call(name, arguments)
        self.check()  # A read already in flight cannot be undone; withhold its result.
        return result


class AuthenticatedReview:
    def __init__(self, auth: Authorization, admission, planner_factory, tools):
        self.auth, self.admission = auth, admission
        self.planner_factory, self.tools = planner_factory, tools

    async def execute(self, token, snapshot_id, question):
        def denied(reason):
            return {"status": "denied", "reason": reason, "answer": None,
                    "identity_mode": "local_sqlite_session", "paid_model_calls": 0,
                    "engineering_approval": False}
        try:
            principal = self.auth.authorize(token, snapshot_id)
            guarded = AuthorizedTools(self.auth, token, snapshot_id, principal, self.tools)

            async def work():
                guarded.check()  # Recheck after admission/queue wait, before planner/budget work.
                return await run(question, snapshot_id, self.planner_factory(principal), guarded)

            result = await self.admission.execute(principal.user_id, snapshot_id, work)
            guarded.check()  # Controller/admission may have converted a revoked read to a safe error.
            result.update(identity_mode="local_sqlite_session", engineering_approval=False)
            return result
        except AuthError as error:
            return denied(str(error))
