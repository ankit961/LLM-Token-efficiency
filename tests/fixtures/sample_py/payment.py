"""Synthetic sample for CodeSymbol-graph tests (no private data)."""
from gateway import StripeGateway


class PaymentService(Base):
    def process(self, req):
        key = self.repo.find_by_key(req.id)
        return StripeGateway.charge(key)


class RetryManager:
    def execute(self, fn):
        return fn()


def helper(x):
    return x + 1


def test_process():
    process()
