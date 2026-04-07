.PHONY: test test-fast test-cov check run deploy

test:
	python -m pytest tests/

test-fast:
	python -m pytest tests/ --tb=no

test-cov:
	python -m pytest tests/ --cov=app --cov-report=term-missing

check:
	./scripts/pre-deploy-check.sh

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

deploy:
	./scripts/deploy-t440.sh
