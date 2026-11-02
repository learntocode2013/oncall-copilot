# On-Call Copilot service

The first incident API and operations console. It is deliberately an incident
state viewer and approval surface, not a chatbot.

To start the complete application from the repository root:

```bash
make up
```

This builds the incident and lab images, starts the Astronomy Shop and telemetry
backends, waits for them to become healthy, checks the health endpoints, and
prints the URLs. The incident API/UI is on port 8000. The lab is on port 8020.
`make stop` pauses the
containers, and the next `make up` starts them again. `make down` removes the
containers and keeps volumes. `make reset` also wipes Prometheus and OpenSearch
data. Use `make ui` only to run the service on the host for debugging when its
external dependencies already run elsewhere.

A firing shop alert starts the agent on its own. Grafana evaluates
`CheckoutErrorRateHigh` and posts the notification group to `POST /alerts`.
The rule waits until the checkout error rate has stayed above 5% for five
minutes, so a quiet shop does not open an incident. The console follows that
incident when none is selected. A repeat notification for an investigation
that is still open does not start a second agent. The guided demo and the
evaluation harness are separate: the demo never calls the model, and the
harness posts one alert itself so a scored run does not wait five minutes.

Fault controls are not on this console. The lab at <http://127.0.0.1:8020/>
arms a fault from the manifest, or pages this API with the checkout symptom.
The root cause stays on the lab page.

Open <http://127.0.0.1:8000>. **Run guided demo** uses no model and no live
infrastructure. **Start live investigation** runs the current C1 solo agent:
Prometheus, Jaeger, and OpenSearch reads are live, while writes and verification
still use the scripted shop.

The console also subscribes to `GET /api/incidents/stream`. Incidents created
from another page, `curl`, Alertmanager, or any client calling `POST /alerts`
appear in the queue immediately. If no incident is selected, the console opens
the new incident and follows its investigation automatically.

Command-line clients and backend services can call the API directly. For a
separate browser frontend, allow its exact origin before starting the service:

```bash
export ONCALL_CORS_ORIGINS=http://localhost:5173
```

Multiple origins can be comma-separated. There is intentionally no wildcard
default because this API can start an agent and expose incident data.

For example:

```bash
curl -X POST 'http://127.0.0.1:8000/alerts?incident_id=checkout-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "status": "firing",
    "labels": {
      "alertname": "CheckoutErrorRateHigh",
      "service": "checkout",
      "severity": "critical"
    },
    "annotations": {"summary": "Checkout failures exceed 5%"},
    "startsAt": "2026-11-02T00:00:00Z"
  }'
```

This milestone stores incidents in memory and must run with one process. A
restart clears the queue and any pending approval.
