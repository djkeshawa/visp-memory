.PHONY: help build build-frontend build-python clean install install-dev test lint format release-check docker-build docker-run

PYTHON ?= python3
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest
RUFF ?= $(PYTHON) -m ruff

help:
	@echo "Visp Memory - Build Commands"
	@echo "============================"
	@echo "  make build           - Build both frontend and Python package"
	@echo "  make build-frontend  - Build Next.js dashboard"
	@echo "  make build-python    - Build Python wheel"
	@echo "  make install         - Install package locally"
	@echo "  make install-dev     - Install with dev dependencies"
	@echo "  make clean           - Remove build artifacts"
	@echo "  make test            - Run tests"
	@echo "  make lint            - Run linter"
	@echo "  make format          - Format code"
	@echo "  make release-check   - Run local release readiness checks"
	@echo "  make docker-build    - Build Docker image"
	@echo "  make docker-run      - Run Docker container"

build: build-frontend build-python

build-frontend:
	@echo "Building Next.js dashboard..."
	$(PYTHON) build_frontend.py

build-python:
	@echo "Building Python package..."
	$(PIP) install build
	$(PYTHON) -m build --wheel

clean:
	@echo "Cleaning build artifacts..."
	rm -rf dist/ build/ *.egg-info
	rm -rf visp-memory-dashboard/out visp-memory-dashboard/.next
	rm -rf src/visp_memory/server/static
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

install: build
	@echo "Installing visp-memory..."
	$(PIP) install dist/*.whl

install-dev:
	@echo "Installing visp-memory with dev dependencies..."
	$(PIP) install -e ".[all]"

test:
	@echo "Running tests..."
	$(PYTEST) -v

lint:
	@echo "Running linter..."
	$(RUFF) check .

format:
	@echo "Formatting code..."
	$(RUFF) format .

release-check:
	@echo "Running release readiness checks..."
	$(PYTHON) scripts/release_check.py

docker-build:
	@echo "Building Docker image..."
	docker build -t visp-memory:latest .

docker-run:
	@echo "Running Docker container..."
	docker run -p 8000:8000 -v $(PWD)/.visp-memory:/data visp-memory:latest
