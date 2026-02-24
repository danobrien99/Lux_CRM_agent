from __future__ import annotations

import json

from app.core.config import get_settings
from app.db.neo4j.queries import run_inference_rules_v2


def main() -> None:
    settings = get_settings()
    result = run_inference_rules_v2(
        min_confidence=settings.graph_v2_inference_min_confidence,
        max_age_days=settings.graph_v2_inference_max_age_days,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
