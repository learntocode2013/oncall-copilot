"""Turn one Alertmanager or Grafana notification into the alert contract.

Both send a group: ``{"alerts": [ {status, labels, annotations, startsAt} ]}``.
The evaluation harness and the console post one alert object, with no ``alerts``
list. ``POST /alerts`` accepts either shape.
"""

from __future__ import annotations

from typing import Any

from oncall_contracts.state import Alert


class AlertPayloadError(ValueError):
    """The body is not a firing alert and not a notification group."""


def is_notification_group(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("alerts"), list)


def firing_alerts(body: dict[str, Any]) -> list[tuple[Alert, str]]:
    """Firing alerts in a notification, each with a stable dedupe key.

    Resolved alerts are skipped. Grafana keeps posting the group while the
    condition holds, so the key is the fingerprint when the sender provides
    one, otherwise the alert name plus ``startsAt``.
    """
    raw_alerts = body.get("alerts")
    if not isinstance(raw_alerts, list):
        raise AlertPayloadError("notification alerts must be a list")
    firing: list[tuple[Alert, str]] = []
    for raw in raw_alerts:
        if not isinstance(raw, dict):
            raise AlertPayloadError("alert must be an object")
        status = raw.get("status")
        if status == "resolved":
            continue
        if status != "firing":
            raise AlertPayloadError("alert status must be firing or resolved")
        firing.append(_one_firing_alert(raw))
    return firing


def _one_firing_alert(raw: dict[str, Any]) -> tuple[Alert, str]:
    labels = raw.get("labels")
    annotations = raw.get("annotations")
    if not isinstance(labels, dict) or not isinstance(annotations, dict):
        raise AlertPayloadError("alert labels and annotations must be objects")
    starts_at = raw.get("startsAt")
    if not isinstance(starts_at, str) or not starts_at.strip():
        raise AlertPayloadError("alert startsAt is required")
    alertname = labels.get("alertname")
    if not isinstance(alertname, str) or not alertname.strip():
        raise AlertPayloadError("alert labels.alertname is required")
    service = labels.get("service") or labels.get("service_name") or "unknown"
    if not isinstance(service, str) or not service.strip():
        service = "unknown"
    summary = annotations.get("summary") or annotations.get("description") or alertname
    if not isinstance(summary, str) or not summary.strip():
        summary = alertname
    alert: Alert = {
        "status": "firing",
        "labels": {"alertname": alertname.strip(), "service": service.strip()},
        "annotations": {"summary": summary.strip()},
        "startsAt": starts_at.strip(),
    }
    severity = labels.get("severity")
    if isinstance(severity, str) and severity.strip():
        alert["labels"]["severity"] = severity.strip()
    description = annotations.get("description")
    if isinstance(description, str) and description.strip():
        alert["annotations"]["description"] = description.strip()
    fingerprint = raw.get("fingerprint")
    if isinstance(fingerprint, str) and fingerprint.strip():
        key = fingerprint.strip()
    else:
        key = f"{alert['labels']['alertname']}:{alert['startsAt']}"
    return alert, key
