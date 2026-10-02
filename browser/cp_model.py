"""Planner's CP-SAT modeling surface, serialized for the browser WASM solver.

This adapter does not solve or approximate anything. Integer coefficients and
domains travel as decimal strings so JavaScript cannot round 64-bit values.
"""
from copy import deepcopy
import json
import time
from types import SimpleNamespace

INT_MIN = -(1 << 63)
INT_MAX = (1 << 63) - 1
OPTIMAL = 4
FEASIBLE = 2


class LinearExpr:
    def __init__(self, terms=None, constant=0):
        self.terms = {i: c for i, c in (terms or {}).items() if c}
        self.constant = int(constant)

    @staticmethod
    def cast(value):
        if isinstance(value, LinearExpr):
            return value
        if type(value) in (int, bool):
            return LinearExpr(constant=int(value))
        raise TypeError(f"Unsupported CP-SAT expression: {type(value).__name__}")

    @staticmethod
    def sum(expressions):
        terms, constant = {}, 0
        for value in expressions:
            expression = LinearExpr.cast(value)
            constant += expression.constant
            for index, coefficient in expression.terms.items():
                terms[index] = terms.get(index, 0) + coefficient
        return LinearExpr(terms, constant)

    def __add__(self, other):
        other = self.cast(other)
        terms = dict(self.terms)
        for i, c in other.terms.items():
            terms[i] = terms.get(i, 0) + c
        return LinearExpr(terms, self.constant + other.constant)

    __radd__ = __add__

    def __neg__(self):
        return self * -1

    def __sub__(self, other):
        return self + -self.cast(other)

    def __rsub__(self, other):
        return self.cast(other) + -self

    def __mul__(self, other):
        if type(other) is not int:
            raise TypeError("CP-SAT linear multiplication requires an integer")
        return LinearExpr({i: c * other for i, c in self.terms.items()}, self.constant * other)

    __rmul__ = __mul__

    def __eq__(self, other):
        return Bound(self - other, [0, 0])

    def __ne__(self, other):
        return Bound(self - other, [INT_MIN, -1, 1, INT_MAX])

    def __ge__(self, other):
        return Bound(self - other, [0, INT_MAX])

    def __gt__(self, other):
        return Bound(self - other, [1, INT_MAX])

    def __le__(self, other):
        return Bound(self - other, [INT_MIN, 0])

    def __lt__(self, other):
        return Bound(self - other, [INT_MIN, -1])

    def __bool__(self):
        raise TypeError("A CP-SAT expression cannot be used as a Python Boolean")

    def proto(self):
        items = sorted(self.terms.items())
        return {"vars": [i for i, _ in items], "coeffs": [str(c) for _, c in items],
                "offset": str(self.constant)}


class Var(LinearExpr):
    def __init__(self, index):
        super().__init__({index: 1})
        self.index = index


class Bound:
    def __init__(self, expression, domain):
        self.expression = expression
        self.domain = domain

    def __bool__(self):
        raise TypeError("A CP-SAT constraint cannot be used as a Python Boolean")


class Constraint:
    def __init__(self, proto):
        self.proto = proto

    def only_enforce_if(self, flags):
        if isinstance(flags, Var):
            flags = [flags]
        self.proto.setdefault("enforcementLiteral", []).extend(flag.index for flag in flags)
        return self


class CpModel:
    def __init__(self):
        self.model = {"variables": [], "constraints": []}

    def new_int_var(self, low, high, name):
        if type(low) is not int or type(high) is not int or not INT_MIN <= low <= high <= INT_MAX:
            raise ValueError("Invalid CP-SAT variable bounds")
        variable = Var(len(self.model["variables"]))
        self.model["variables"].append({"name": name, "domain": [str(low), str(high)]})
        return variable

    def new_bool_var(self, name):
        return self.new_int_var(0, 1, name)

    def _constraint(self, proto):
        self.model["constraints"].append(proto)
        return Constraint(proto)

    def add(self, bound):
        if type(bound) is bool:
            return self._constraint({"boolOr": {"literals": []}}) if not bound else self._constraint({"boolAnd": {"literals": []}})
        if not isinstance(bound, Bound):
            raise TypeError("Expected a bounded CP-SAT linear expression")
        expr = bound.expression
        shifted = [str(max(INT_MIN, min(INT_MAX, v - expr.constant))) for v in bound.domain]
        items = sorted(expr.terms.items())
        return self._constraint({"linear": {"vars": [i for i, _ in items],
            "coeffs": [str(c) for _, c in items], "domain": shifted}})

    def add_division_equality(self, target, numerator, denominator):
        return self._constraint({"intDiv": {"target": LinearExpr.cast(target).proto(),
            "exprs": [LinearExpr.cast(numerator).proto(), LinearExpr.cast(denominator).proto()]}})

    def add_multiplication_equality(self, target, factors):
        return self._constraint({"intProd": {"target": LinearExpr.cast(target).proto(),
            "exprs": [LinearExpr.cast(f).proto() for f in factors]}})

    def add_element(self, selector, values, target):
        return self._constraint({"element": {"linearIndex": LinearExpr.cast(selector).proto(),
            "linearTarget": LinearExpr.cast(target).proto(),
            "exprs": [LinearExpr.cast(v).proto() for v in values]}})

    def maximize(self, value):
        expr = LinearExpr.cast(value)
        items = sorted(expr.terms.items())
        self.model["objective"] = {"vars": [i for i, _ in items], "coeffs": [str(-c) for _, c in items],
            "offset": -expr.constant, "scalingFactor": -1}

    def clear_hints(self):
        self.model.pop("solutionHint", None)

    def add_hint(self, variable, value):
        hint = self.model.setdefault("solutionHint", {"vars": [], "values": []})
        hint["vars"].append(variable.index)
        hint["values"].append(str(value))

    def clone(self):
        other = CpModel()
        other.model = deepcopy(self.model)
        return other

    def validate(self):
        return ""  # The WASM solver validates the serialized model before solving.


class CpSolver:
    def __init__(self):
        self.parameters = SimpleNamespace(num_search_workers=1, random_seed=19471)
        self.solution = None

    def solve(self, model):
        from js import browser_solve
        import planner_core as p
        begin = time.perf_counter()
        raw = json.dumps({"model": model.model, "parameters": {
            "numSearchWorkers": self.parameters.num_search_workers,
            "randomSeed": self.parameters.random_seed}}, separators=(",", ":"))
        response = json.loads(str(browser_solve(raw, len(model.model["variables"]))))
        self.diagnostics = {"roundtrip_seconds": time.perf_counter() - begin,
                            "model_json_bytes": len(raw.encode("utf-8")),
                            "variables": len(model.model["variables"]),
                            "constraints": len(model.model["constraints"]),
                            "worker_timings": response.get("timings", {}),
                            "protobuf_bytes": response.get("model_bytes", 0)}
        if response.get("cancelled"):
            raise p.Cancelled()
        if response.get("error"):
            raise p.InputError(response["error"])
        self.solution = [int(v) for v in response.get("solution", [])]
        if response["status"] not in (OPTIMAL, FEASIBLE):
            raise p.InputError(f"浏览器求解未完成（状态 {response['status']}）：{response.get('solutionInfo', '')}")
        return response["status"]

    def value(self, expression):
        expr = LinearExpr.cast(expression)
        return expr.constant + sum(self.solution[i] * c for i, c in expr.terms.items())
