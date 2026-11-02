"""Build the notification group posted to the incident API.

The group carries the published symptom only. Callers must not add the flag,
the root cause, or the correct action.
"""

from __future__ import annotations

from typing import Any

from .catalog import Symptom


def notification(symptom: Symptom, starts_at: str) -> dict[str, Any]:
    return {
        "alerts": [
            {
                "status": "firing",
                "fingerprint": symptom.fingerprint,
                "startsAt": starts_at,
                "labels": {
                    "alertname": symptom.alertname,
                    "service": symptom.service,
                    "severity": symptom.severity,
                },
                "annotations": {"summary": symptom.summary},
            }
        ]
    }
