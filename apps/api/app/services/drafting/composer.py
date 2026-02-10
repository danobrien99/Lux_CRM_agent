from __future__ import annotations


def compose_draft(bundle: dict, tone: dict) -> str:
    display_name = bundle["contact"].get("display_name") or "there"
    objective = bundle.get("objective") or "check in"

    if tone["tone_band"] == "cool_professional":
        opener = f"Hello {display_name},"
        body = f"I wanted to {objective} and share a quick update."
    elif tone["tone_band"] == "warm_professional":
        opener = f"Hi {display_name},"
        body = f"Hope you are doing well. I wanted to {objective}."
    else:
        opener = f"Hey {display_name},"
        body = f"Hope all is good on your side. I wanted to {objective} and reconnect."

    closer = "Best,\n[Your Name]"
    return f"{opener}\n\n{body}\n\n{closer}"
