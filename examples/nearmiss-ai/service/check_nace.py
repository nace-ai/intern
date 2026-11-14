"""B1 connectivity check: list models (free, doubles as key check) and make ONE live
Drex `choice` decision. Prints the real response; never prints the key.

Usage:  python -m service.check_nace
For the NDI half of B1 run:  python -m evidence.ingest --parse-only --only faa-ac-120-74b
"""

from __future__ import annotations

import json
import sys

from service.config import get_settings


def main() -> int:
    settings = get_settings()
    if not settings.nace_configured:
        print("NACE_API_KEY is not set (expected a nace_sk_ key in the environment or .env).", file=sys.stderr)
        return 2

    from nace_sdk import Choice, NaceAPIError, NaceClient, NaceError

    try:
        with NaceClient(model=settings.decision_model, client_info="nearmiss-ai-check/0.1") as client:
            models = client.models.list()
            print("Models available to this key:")
            for m in models.models:
                print(f"  {m.name}  alias_for={m.alias_for}  released={m.release_date}")

            result = client.system_one(
                state="Crew read back 'hold short runway 27' but the controller had cleared them to cross runway 27.",
                questions={
                    "family": Choice(
                        instructions="Which precursor family best matches this aviation report?",
                        criteria={
                            "communication_coordination": "Miscommunication between pilots and controllers.",
                            "runway_surface": "Runway or taxiway surface operations.",
                            "other": "Anything else.",
                        },
                    )
                },
            )
    except NaceAPIError as error:
        print(f"Drex error: status={error.status} type={error.type} request_id={error.request_id}", file=sys.stderr)
        return 1
    except NaceError as error:
        print(f"Nace SDK error: {type(error).__name__}", file=sys.stderr)
        return 1

    answer = result.choices["family"]
    print(
        json.dumps(
            {
                "model": result.model,
                "request_id": result.request_id,
                "choice": answer.choice,
                "probabilities": answer.probabilities,
                "evaluation_time_ms": result.evaluation_time_ms,
                "input_tokens": result.usage.input_tokens,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
