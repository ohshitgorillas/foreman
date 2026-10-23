.PHONY: check format

# tests/ is absent until step 2; ruff and pytest only see directories that exist.
SRC := $(wildcard foreman tests)

format:
	uv run ruff format $(SRC)

check:
	uv run ruff format --check $(SRC)
	uv run ruff check $(SRC)
	uv run pytest -q
