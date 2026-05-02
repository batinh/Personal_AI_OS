.PHONY: test test-smoke test-fast test-cov test-integration check lint format security run deploy rollback

test:
	python -m pytest tests/ --cov=app --cov-report=xml:reports/coverage.xml --junitxml=reports/junit.xml --cov-fail-under=60

test-smoke:
	python -m pytest tests/test_smoke.py -v

test-fast:
	python -m pytest tests/ --tb=no

test-cov:
	python -m pytest tests/ --cov=app --cov-report=term-missing --cov-report=xml:reports/coverage.xml --junitxml=reports/junit.xml

test-integration:
	INTEGRATION_TEST=1 python -m pytest tests/ -m integration -v

lint:
	ruff check app/ tests/

format:
	black app/ tests/

security:
	bandit -r app/ -ll

check:
	./scripts/pre-deploy-check.sh

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

deploy:
	./scripts/deploy-t440.sh

rollback:
	./scripts/rollback-t440.sh
