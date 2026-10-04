"""In-memory event-loop reservation accounting, not a provider billing limit."""

from decimal import Decimal, InvalidOperation


class BudgetError(Exception):
    """Fixed reason only; never contains provider data."""


def money(value):
    if type(value) not in (str, int, float, Decimal):
        raise BudgetError("model_cost_unknown")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise BudgetError("model_cost_unknown") from None
    if not amount.is_finite() or amount < 0:
        raise BudgetError("model_cost_unknown")
    return amount


class Budget:
    def __init__(self, limit_usd):
        self.limit = money(limit_usd)
        self.spent = Decimal(0)
        self.held = {}
        self.blocked = False
        self.overrun = False
        self.sequence = 0

    def reserve(self, maximum_charge_usd):
        amount = money(maximum_charge_usd)
        if amount <= 0:
            raise BudgetError("invalid_model_quote")
        if self.blocked:
            raise BudgetError("model_budget_blocked")
        if self.spent + sum(self.held.values()) + amount > self.limit:
            raise BudgetError("model_budget_exhausted")
        self.sequence += 1
        self.held[self.sequence] = amount
        return self.sequence

    def unknown(self, ticket):
        # Remote cancellation/failure is not evidence of a refund.
        if ticket in self.held:
            self.blocked = True

    def settle(self, ticket, actual_charge_usd):
        reserved = self.held[ticket]
        try:
            actual = money(actual_charge_usd)
        except BudgetError:
            self.unknown(ticket)
            raise
        self.spent += actual
        del self.held[ticket]
        if actual > reserved:
            self.blocked = self.overrun = True
            raise BudgetError("model_cost_overrun")

    def snapshot(self):
        held = sum(self.held.values(), Decimal(0))
        return {"currency": "USD", "limit": str(self.limit), "spent": str(self.spent),
                "held": str(held), "available": str(max(Decimal(0), self.limit - self.spent - held)),
                "blocked": self.blocked, "overrun": self.overrun,
                "scope": "in_memory_single_event_loop"}
