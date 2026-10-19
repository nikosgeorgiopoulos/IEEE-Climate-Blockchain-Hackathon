# ProofOfAir — common tasks. Run `make setup keys seed run` on a fresh clone.
PYTHON ?= python3
VENV   ?= .venv
PY     := $(VENV)/bin/python

.PHONY: setup keys seed run demo deploy test lint clean

setup:      ## create the virtualenv, install deps, create .env from the example
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt
	test -f .env || cp .env.example .env

keys:       ## gateway + simulated device keys
	$(PY) scripts/gen_keys.py

seed:       ## backfill the last 3 days (Sensor.Community archive + simulator)
	$(PY) scripts/seed_history.py

run:        ## start the API + dashboard on http://localhost:8000
	$(PY) -m uvicorn proofofair.api:app --reload --port 8000

demo:       ## seed + batch-now + print URLs
	$(PY) scripts/run_demo.py

deploy:     ## compile + deploy AnchorRegistry.sol to Polygon Amoy
	$(PY) scripts/deploy_contract.py

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check .

clean:      ## delete the local database, mock chain and OTS files (keeps keys)
	rm -f data/proofofair.db data/proofofair.db-* data/mock_chain.json anchors/*.bin anchors/*.ots anchors/*.bak
