PY=.venv/bin/python
install:
	python3.13 -m venv .venv && .venv/bin/pip install -q -e . --group dev
test:
	$(PY) -m pytest
lint:
	.venv/bin/ruff check src tests && .venv/bin/ruff format --check src tests
serve:
	$(PY) -m sandbox_mcp.cli serve
worker:
	$(PY) -m sandbox_mcp.cli worker
compose-up:
	docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
compose-down:
	docker compose -f deploy/compose.yaml --env-file deploy/.env down
