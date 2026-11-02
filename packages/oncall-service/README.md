# On-Call Copilot service

The first incident API and operations console. It is deliberately an incident
state viewer and approval surface, not a chatbot.

From the repository root:

```bash
set -a
source .env
set +a
make ui
```

Open <http://127.0.0.1:8000>. **Run guided demo** uses no model and no live
infrastructure. **Start live investigation** runs the current C1 solo agent:
Prometheus, Jaeger, and OpenSearch reads are live, while writes and verification
still use the scripted shop.

This milestone stores incidents in memory and must run with one process. A
restart clears the queue and any pending approval.
