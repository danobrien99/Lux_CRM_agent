from __future__ import annotations

import os

from app.core.config import get_settings
from app.services.extraction.cognee_client import extract_candidates
from app.services.memory.mem0_client import propose_memory_ops


def test_cognee_local_adapter_contract_shape() -> None:
    os.environ["COGNEE_BACKEND"] = "unknown"
    os.environ["COGNEE_ENABLE_HEURISTIC_FALLBACK"] = "true"
    get_settings.cache_clear()
    result = extract_candidates("interaction-1", "Alex joined a new role at Contoso")
    assert result["interaction_id"] == "interaction-1"
    assert isinstance(result["entities"], list)
    assert isinstance(result["relations"], list)
    assert isinstance(result["topics"], list)


def test_mem0_local_adapter_auto_accepts_high_confidence() -> None:
    os.environ["MEM0_BACKEND"] = "unknown"
    os.environ["MEM0_ENABLE_RULES_FALLBACK"] = "true"
    get_settings.cache_clear()
    ops = propose_memory_ops(
        {
            "auto_accept_threshold": 0.9,
            "cognee_candidates": [
                {
                    "claim_id": "c1",
                    "claim_type": "employment",
                    "confidence": 0.95,
                    "status": "proposed",
                    "value_json": {"company": "Contoso"},
                    "evidence_refs": [],
                }
            ],
        }
    )
    assert len(ops) == 1
    assert ops[0]["claim"]["status"] == "accepted"
