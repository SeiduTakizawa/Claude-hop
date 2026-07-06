.PHONY: test lint fmt e2e

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff check --fix .

e2e:
	bash e2e/run.sh
