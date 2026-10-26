# Delegates container lifecycle to deploy/; keeps Python tooling here.
.PHONY: help bootstrap up stop down reset config preflight verify urls reload-rules test lint guards

help:
	@$(MAKE) -C deploy help
	@echo "test       run the whole test suite"
	@echo "lint       ruff check + format check"
	@echo "guards     just the structural guards"

bootstrap up stop down reset config preflight verify urls reload-rules:
	$(MAKE) -C deploy $@

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .

guards:
	uv run pytest tests/test_guards.py -v
