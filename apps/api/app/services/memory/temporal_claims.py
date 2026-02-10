from __future__ import annotations


def apply_memory_ops(existing_claims: list[dict], ops: list[dict]) -> list[dict]:
    updated = list(existing_claims)
    for op in ops:
        claim = op["claim"]
        operation = op["op"]
        if operation == "REJECT":
            claim["status"] = "rejected"
        elif operation in {"ADD", "UPDATE", "SUPERSEDE"}:
            updated.append(claim)
    return updated
