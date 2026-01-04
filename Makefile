.PHONY: setup bootstrap fmt check test all

DETECT_SCAN_TARGETS = src tests scripts .github/workflows README.md pyproject.toml Makefile .pre-commit-config.yaml .agents.yml AGENTS.md

setup:
	uv sync --dev
	@if [ ! -s .secrets.baseline ]; then uv run detect-secrets scan $(DETECT_SCAN_TARGETS) > .secrets.baseline; fi
	@if [ -d .git ]; then uv run pre-commit install; fi

bootstrap: setup

fmt:
	uv run ruff format .
	uv run ruff check --fix .

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run python -m compileall -q src
	uv run bandit -q -r src
	uv run detect-secrets-hook --baseline .secrets.baseline $(DETECT_SCAN_TARGETS)

test:
	uv run pytest --cov=polymarket_watch --cov-report=term-missing

all: check test
