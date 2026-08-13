"""Synthetic sample for CodeSymbol-graph tests (no private data)."""
from gateway import StripeGateway


class PaymentService(Base):
    def process(self, req):
        self.validate(req)                      # sibling method -> SCOPED
        key = self.repo.find_by_key(req.id)     # not defined here -> UNRESOLVED
        return StripeGateway.charge(key)        # not defined here -> UNRESOLVED

    def validate(self, req):
        return req.amount > 0


class RetryManager:
    def execute(self, fn):
        return fn()


def helper(x):
    return x + 1


def test_process():
    process()
