# Demo lifecycle. LAYER 1: the Astronomy Shop only.
#
# deploy/compose.oncall.yaml is in the list, but in Layer 1 it carries only
# an upstream health-check fix — none of our own services. When
# incident-api and the MCP servers join it, add their checks to `verify`
# IN THE SAME COMMIT. A target that asserts something which does not exist
# teaches everyone to ignore it.

ONCALL_ROOT ?= $(shell cd .. && pwd)
export ONCALL_ROOT

DEMO := vendor/opentelemetry-demo

# One definition of the file list. `down` MUST take the same -f list as `up`:
# measured on a running stack, a bare `docker compose down` removed 20 of 25
# containers and silently orphaned Prometheus, Grafana, Jaeger and OpenSearch.
COMPOSE := cd $(ONCALL_ROOT) && docker compose \
	     --env-file $(DEMO)/.env \
	     --env-file $(DEMO)/.env.override \
	     --env-file .env \
	     -f $(DEMO)/compose.yaml \
	     -f $(DEMO)/compose.full.yaml \
	     -f $(DEMO)/compose.observability.yaml \
	     -f deploy/compose.oncall.yaml

.PHONY: help bootstrap preflight up stop down reset config verify urls reload-rules

help:
	@echo "bootstrap  one command after a clone: submodule, .env, uv sync, up, verify"
	@echo "up         start the shop and its telemetry backends"
	@echo "stop       halt containers, keep data ('up' resumes in seconds)"
	@echo "down       remove containers, keep volumes"
	@echo "reset      also WIPE Prometheus/OpenSearch data"
	@echo "config     render the merged compose config"
	@echo "verify     curl the health endpoints"
	@echo "urls       print where everything is"

# The one post-clone command. Idempotent: re-run it whenever.
bootstrap:
	@cd $(ONCALL_ROOT) && git submodule update --init --recursive
	@cd $(ONCALL_ROOT) && test -f .env || { cp .env.example .env; \
	  echo "created .env from .env.example"; }
	@cd $(ONCALL_ROOT) && grep -qE '^OPENROUTER_API_KEY=.+' .env || { \
	  echo ""; \
	  echo "NOTE: OPENROUTER_API_KEY is empty in .env."; \
	  echo "      Not needed yet — nothing calls a model. You WILL need it"; \
	  echo "      before the agent runs. https://openrouter.ai/keys"; \
	  echo ""; }
	@cd $(ONCALL_ROOT) && uv sync
	$(COMPOSE) up --force-recreate --remove-orphans --detach --wait --wait-timeout 600
	@$(MAKE) verify
	@$(MAKE) urls

# Names the three things a fresh clone lacks, with the exact remedy.
preflight:
	@test -f $(ONCALL_ROOT)/$(DEMO)/compose.yaml || { \
	  echo "ERROR: $(DEMO)/compose.yaml not found."; \
	  echo "  The demo is a submodule. Run:"; \
	  echo "    git submodule update --init --recursive"; exit 1; }
	@test -f $(ONCALL_ROOT)/.env || { \
	  echo "ERROR: .env not found."; \
	  echo "  Run: cp .env.example .env"; exit 1; }
	@test -n "$(ONCALL_ROOT)" || { echo "ERROR: ONCALL_ROOT is empty."; exit 1; }
	@echo "preflight OK (ONCALL_ROOT=$(ONCALL_ROOT))"

up: preflight
	$(COMPOSE) up --force-recreate --remove-orphans --detach --wait --wait-timeout 600

stop: preflight
	$(COMPOSE) stop

down: preflight
	$(COMPOSE) down --remove-orphans

# Wipes the Prometheus TSDB and OpenSearch indices. Use between evaluation
# runs for a clean telemetry baseline; never right before a demo.
reset: preflight
	$(COMPOSE) down -v --remove-orphans

config: preflight
	$(COMPOSE) config

# Clean-machine install evidence. -f makes a bad HTTP status fail the target.
# Add the incident-api and MCP checks when those services join the overlay.
verify:
	@printf 'prometheus    '; curl -fsS localhost:9090/-/ready && echo
	@printf 'shop frontend '; curl -fsS -o /dev/null -w '%{http_code}\n' localhost:8080/

urls:
	@echo ""
	@echo "  Web store       http://localhost:8080/"
	@echo "  Grafana         http://localhost:8080/grafana/"
	@echo "  Jaeger          http://localhost:8080/jaeger/ui/"
	@echo "  Feature flags   http://localhost:8080/feature"
	@echo "  Prometheus      http://localhost:9090"

reload-rules:
	curl -X POST http://localhost:9090/-/reload
