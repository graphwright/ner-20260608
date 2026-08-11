#!/bin/bash -e

uv run ruff check .
uv run ruff check --fix . && uv run ruff format .
MYPYPATH=src uv run mypy --strict -p ner_20260608
uv run pytest .