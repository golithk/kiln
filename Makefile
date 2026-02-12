.PHONY: lint lint-fix format format-check test test-parallel test-timing setup check-config check-orphans check-dead-code check-all

# Ensure venv exists and has dev deps
setup:
	@if [ ! -d ".venv" ]; then python3.13 -m venv .venv; fi
	@.venv/bin/pip install -q -e ".[dev]"

# Run linter (installs ruff if needed)
lint: setup
	.venv/bin/ruff check src/ tests/

# Run linter with auto-fix
lint-fix: setup
	.venv/bin/ruff check src/ tests/ --fix

# Format code
format: setup
	.venv/bin/ruff format src/ tests/

# Check formatting without changes
format-check: setup
	.venv/bin/ruff format --check src/ tests/

# Run tests (parallel + timing sequentially)
test: setup
	.venv/bin/pytest tests/ -m "not timing" -n auto --dist loadscope -v
	.venv/bin/pytest tests/ -m "timing" -v

# Run only parallel-safe tests
test-parallel: setup
	.venv/bin/pytest tests/ -m "not timing" -n auto --dist loadscope -v

# Run only timing-sensitive tests
test-timing: setup
	.venv/bin/pytest tests/ -m "timing" -v

# Proactive code checks
check-config:
	python scripts/check_config_sync.py

check-orphans:
	python scripts/check_orphan_modules.py

check-dead-code: setup
	.venv/bin/vulture src/ vulture_whitelist.py

check-all: lint check-config check-orphans check-dead-code
