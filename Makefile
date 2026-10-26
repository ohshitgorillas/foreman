.PHONY: check format smoke

# tests/ is absent until step 2; ruff and pytest only see directories that exist.
SRC := $(wildcard foreman tests)

format:
	uv run ruff format $(SRC)

check:
	uv run ruff format --check $(SRC)
	uv run ruff check $(SRC)
	uv run pytest -q

# Opt-in: drives one toy group through every stage with real `claude -p` agents.
smoke:
	uv run pytest -m smoke -s tests/smoke
