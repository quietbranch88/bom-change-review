"""Model-budget mutations in isolated copies; zero HTTP/provider requests."""

import verify_demo_guards as guards

guards.CASES = (
    ("model_budget.py", "if self.spent + sum(self.held.values()) + amount > self.limit:", "if False:",
     "test_provider_planner.ProviderTests.test_insufficient_budget_never_dispatches_provider_or_tools"),
    ("model_budget.py", "self.blocked = True", "self.held.pop(ticket)",
     "test_provider_planner.ProviderTests.test_controller_deadline_retains_unknown_exposure"),
    ("openrouter_planner.py", 'if transport.kind != "local_fixture":', "if False:",
     "test_provider_planner.ProviderTests.test_non_fixture_transport_is_refused"),
)

if __name__ == "__main__":
    raise SystemExit(guards.main())
