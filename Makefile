.PHONY: check format

# tests/ is absent until step 2; ruff and pytest only see directories that exist.
SRC := $(wildcard foreman tests)

format:
	uv run ruff format $(SRC)

# pytest exits 5 when no tests are collected; tolerated until tests land (steps 2-3).
check:
	uv run ruff format --check $(SRC)
	uv run ruff check $(SRC)
	uv run pytest -q; code=$$?; [ $$code -eq 0 ] || [ $$code -eq 5 ]
