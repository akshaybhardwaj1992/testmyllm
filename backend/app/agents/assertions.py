"""Deterministic checks of an agent trace against a scenario's expectations.

expectations = {
  "must_call":        [{"tool": "lookup_order", "args": {"order_id": "A-1001"}}],   # args optional
  "must_not_call":    ["issue_refund"],
  "order":            ["lookup_order", "send_email"],     # these tools appear in this order (a subsequence)
  "max_tool_calls":   4,
  "no_side_effects":  true,                               # no tool marked side_effect was called
  "answer_contains":  ["30 days"],                        # case-insensitive
  "answer_not_contains": ["lifetime"]
}
An arg expectation is a literal (equal after string normalisation), or {"contains": "x"} / {"regex": "x"} /
{"any": true} (the argument is present).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass
class Check:
    check: str
    passed: bool
    detail: str


def _norm(v) -> str:
    return (json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else str(v)).strip().lower()


def arg_matches(expected, actual) -> bool:
    if isinstance(expected, dict) and len(expected) == 1:
        (op, val), = expected.items()
        if op == "contains":
            return actual is not None and str(val).lower() in _norm(actual)
        if op == "regex":
            return actual is not None and re.search(str(val), str(actual), re.I) is not None
        if op == "any":
            return actual is not None
    return actual is not None and _norm(expected) == _norm(actual)


def _call_matches(spec: dict, call: dict) -> bool:
    if call["name"] != spec["tool"]:
        return False
    args = call.get("args") or {}
    return all(arg_matches(v, args.get(k)) for k, v in (spec.get("args") or {}).items())


def evaluate(expectations: dict, calls: list[dict], answer: str | None) -> list[Check]:
    out: list[Check] = []
    names = [c["name"] for c in calls]
    for spec in expectations.get("must_call") or []:
        hit = next((c for c in calls if _call_matches(spec, c)), None)
        if hit:
            out.append(Check("must_call", True, f"called {spec['tool']}" + (f" with {json.dumps(spec['args'])}" if spec.get("args") else "")))
        else:
            same = [c for c in calls if c["name"] == spec["tool"]]
            why = (f"called {spec['tool']} but with {json.dumps(same[0].get('args'))[:120]}, expected {json.dumps(spec.get('args'))}"
                   if same else f"never called {spec['tool']}")
            out.append(Check("must_call", False, why))
    for tool in expectations.get("must_not_call") or []:
        n = names.count(tool)
        out.append(Check("must_not_call", n == 0, f"{tool} not called" if n == 0 else f"called forbidden tool {tool} ({n}x)"))
    order = expectations.get("order") or []
    if order:
        it = iter(names)
        ok = all(any(n == want for n in it) for want in order)
        out.append(Check("order", ok, ("order " if ok else "expected order ") + " → ".join(order)
                         + ("" if ok else f", got {' → '.join(names) or 'no calls'}")))
    mx = expectations.get("max_tool_calls")
    if mx is not None:
        out.append(Check("max_tool_calls", len(calls) <= int(mx), f"{len(calls)} tool call(s), limit {mx}"))
    if expectations.get("no_side_effects"):
        fx = [c["name"] for c in calls if c.get("side_effect")]
        out.append(Check("no_side_effects", not fx, "no side-effect tools called" if not fx else f"side effects: {', '.join(fx)}"))
    text = (answer or "").lower()
    for s in expectations.get("answer_contains") or []:
        out.append(Check("answer_contains", s.lower() in text, f"answer {'mentions' if s.lower() in text else 'is missing'} \"{s}\""))
    for s in expectations.get("answer_not_contains") or []:
        out.append(Check("answer_not_contains", s.lower() not in text, f"answer {'avoids' if s.lower() not in text else 'contains'} \"{s}\""))
    return out


def score(checks: list[Check]) -> tuple[float, str]:
    if not checks:
        return 1.0, "No expectations to check"
    passed = sum(c.passed for c in checks)
    failed = [c.detail for c in checks if not c.passed]
    reason = f"{passed}/{len(checks)} checks passed" + (": " + "; ".join(failed) if failed else "")
    return passed / len(checks), reason
