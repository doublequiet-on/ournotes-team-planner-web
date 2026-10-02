"""Bounded aggregate timings. Nested stages are inclusive, never additive."""
from collections import Counter, deque
from contextlib import contextmanager
import time
import sys
from collections import OrderedDict


class SignatureCache(OrderedDict):
    """One request-wide byte budget; evictions only cause exact recomputation."""
    def __init__(self, byte_budget=32 * 1024 * 1024):
        super().__init__()
        self.byte_budget, self.bytes, self.sizes = byte_budget, 0, {}

    @staticmethod
    def size(value):
        seen = set()
        def visit(item):
            if id(item) in seen:
                return 0
            seen.add(id(item))
            return sys.getsizeof(item) + (sum(visit(v) for v in item) if isinstance(item, (tuple, list)) else 0)
        return visit(value)

    def __setitem__(self, key, value):
        if key in self:
            self.pop(key)
        size = self.size(key) + self.size(value) + 256
        if size > self.byte_budget:
            return
        while self and self.bytes + size > self.byte_budget:
            self.popitem(last=False)
        super().__setitem__(key, value)
        self.sizes[key] = size
        self.bytes += size

    def pop(self, key):
        value = super().pop(key)
        self.bytes -= self.sizes.pop(key)
        return value

    def popitem(self, last=True):
        key, value = super().popitem(last=last)
        self.bytes -= self.sizes.pop(key)
        return key, value

    def clear(self):
        super().clear()
        self.bytes, self.sizes = 0, {}


class Trace:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.seconds, self.calls, self.counters = Counter(), Counter(), Counter()
        self.signatures = SignatureCache()
        self.solver_log = deque(maxlen=128)

    def record_solver(self, context, **fields):
        if self.enabled:
            self.solver_log.append({**context, **fields})

    @contextmanager
    def measure(self, stage):
        if not self.enabled:
            yield
            return
        begin = time.perf_counter()
        try:
            yield
        finally:
            self.seconds[stage] += time.perf_counter() - begin
            self.calls[stage] += 1

    def count(self, name, amount=1):
        if self.enabled:
            self.counters[name] += amount

    def report(self):
        return {"timing_scope": "inclusive stages; do not sum nested timings",
                "seconds": {k: round(v, 6) for k, v in self.seconds.items()},
                "calls": dict(self.calls), "counters": dict(self.counters),
                "signature_cache_estimated_bytes": self.signatures.bytes,
                "signature_cache_budget_bytes": self.signatures.byte_budget,
                "solver_calls_last_128": list(self.solver_log)}
