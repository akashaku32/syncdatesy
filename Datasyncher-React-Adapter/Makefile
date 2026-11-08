.PHONY: help format lint check fix install clean

help:
	@echo "📋 Available commands:"
	@echo "  make install  - Install formatting tools"
	@echo "  make format   - Format all code"
	@echo "  make lint     - Run all linters"
	@echo "  make check    - Check formatting without changes"
	@echo "  make fix      - Fix all auto-fixable issues"
	@echo "  make clean    - Clean cache files"

install:
	pip install -r .formatting-requirements.txt
	pre-commit install

format:
	@echo "🎨 Formatting code..."
	black .
	isort .

lint:
	@echo "🔍 Running linters..."
	flake8 .

check:
	@echo "✅ Running format checks..."
	black --check .
	isort --check-only .

fix: format

clean:
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete

