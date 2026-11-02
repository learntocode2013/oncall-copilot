# On-Call Copilot service

The first incident API and operations console. It is deliberately an incident
state viewer and approval surface, not a chatbot.

To start the complete local development stack from the repository root:

```bash
make dev
```

This starts the Astronomy Shop and telemetry backends, waits for them to become
healthy, loads `.env`, and then starts the incident API/UI on port 8000. Use
`make ui` when the external dependencies are already running elsewhere.

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
