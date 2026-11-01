# Delegates container lifecycle to deploy/; keeps Python tooling here.
.PHONY: help bootstrap up stop down reset config preflight verify urls reload-rules test lint fix guards

help:
	@$(MAKE) -C deploy help
	@echo "test       run the whole test suite"
	@echo "lint       ruff check + format check (read-only)"
	@echo "fix        apply the fixes lint reports, then re-run lint"
	@echo "guards     just the structural guards"

bootstrap up stop down reset config preflight verify urls reload-rules:
	$(MAKE) -C deploy $@

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

# `lint` only reports; this applies what can be applied safely.
#
# Order matters: --fix can leave lines the formatter then tidies.
#
# Deliberately NOT --unsafe-fixes. Those can change behaviour rather than
# style, so they stay a conscious choice: inspect first with
#   uv run ruff check --diff --unsafe-fixes .
#
# Some rules are not fixable at all (TRY004, BLE001 in the validator). Those
# need a judgement call and a `# noqa` WITH a comment saying why — a bare noqa
# is silencing, not a decision.
fix:
	uv run ruff check --fix .
	uv run ruff format .
	@$(MAKE) lint

guards:
	uv run pytest tests/test_guards.py -v
