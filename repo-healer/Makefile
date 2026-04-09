.PHONY: install dev lint type-check test test-cov run docker-build docker-up seed-test-repo

install:
	pip install -r requirements.txt

dev:
	pip install -r requirements-dev.txt

lint:
	ruff check app/ tests/
	ruff format --check app/ tests/

type-check:
	mypy app/

test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ --cov=app --cov-report=term-missing --cov-fail-under=80

run:
	uvicorn app.main:app --reload --port 8000

seed-test-repo:
	bash scripts/seed_test_repo.sh

docker-build:
	docker build -t repo-healer .

docker-up:
	docker-compose up
