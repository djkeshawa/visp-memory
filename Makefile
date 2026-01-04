.PHONY: help build build-frontend build-python clean install install-dev test lint format docker-build docker-run

help:
	@echo "LLM Memory - Build Commands"
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
	@echo "  make docker-build    - Build Docker image"
	@echo "  make docker-run      - Run Docker container"

build: build-frontend build-python

build-frontend:
	@echo "Building Next.js dashboard..."
	cd llm-memory-dashboard && npm install && npm run export
	@echo "Copying to package directory..."
	python3 build_frontend.py

build-python:
	@echo "Building Python package..."
	pip install build
	python3 -m build --wheel

clean:
	@echo "Cleaning build artifacts..."
	rm -rf dist/ build/ *.egg-info
	rm -rf llm-memory-dashboard/out llm-memory-dashboard/.next
	rm -rf src/llm_memory/server/static
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

install: build
	@echo "Installing llm-memory..."
	pip install dist/*.whl

install-dev:
	@echo "Installing llm-memory with dev dependencies..."
	pip install -e ".[all]"

test:
	@echo "Running tests..."
	pytest -v

lint:
	@echo "Running linter..."
	ruff check .

format:
	@echo "Formatting code..."
	ruff format .

docker-build:
	@echo "Building Docker image..."
	docker build -t llm-memory:latest .

docker-run:
	@echo "Running Docker container..."
	docker run -p 8000:8000 -v $(PWD)/.llm-memory:/data llm-memory:latest
