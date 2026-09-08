.PHONY: help build test check format schema package validate list collect example test-one lock run cleanup image all
DOCKER_CONTEXT ?= $(shell docker context inspect desktop-linux >/dev/null 2>&1 && echo desktop-linux || echo default)
DOCKER = docker --context $(DOCKER_CONTEXT)
DC = $(DOCKER) compose
RUN = $(DC) run --rm dev
SUITE ?= examples
ARGS ?=
help:
	@echo "Premiers pas : build example validate list"
	@echo "Qualite       : test check format schema package all"
	@echo "Cible precise : test-one TEST=tests/unit/test_foundation.py [ARGS='-k nom']"
	@echo "Collecte      : collect [SUITE=examples]"
	@echo "Execution live: run SUITE=path CONFIG=config.local.yaml [ARGS='...']"
	@echo "Nettoyage     : cleanup JOURNAL=path CONFIG=config.local.yaml [ARGS='...']"
	@echo "Images        : image lock"
all: build test check validate package
build:
	$(DC) build dev
test:
	$(RUN) python -m pytest --cov=pyintegrationtests --cov-report=term-missing --cov-report=xml --junitxml=reports/junit.xml
test-one:
	@test -n "$(TEST)" || (echo "TEST is required, for example TEST=tests/unit/test_foundation.py"; exit 2)
	$(RUN) python -m pytest $(TEST) $(ARGS)
check:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	$(RUN) mypy --strict src
	$(RUN) pyright
	$(RUN) python scripts/generate_schemas.py --check
format:
	$(RUN) ruff format .
	$(RUN) ruff check --fix .
schema:
	$(RUN) python scripts/generate_schemas.py
package:
	$(RUN) python -m build --no-isolation
	$(RUN) sh -c 'twine check dist/*'
validate:
	$(RUN) pyintegrationtests validate $(SUITE) $(ARGS)
list:
	$(RUN) pyintegrationtests list $(SUITE) $(ARGS)
collect:
	$(RUN) python -m pytest --collect-only -q $(SUITE) $(ARGS)
example:
	$(RUN) python -m pytest -q examples/getting-started/basic.integ.yaml
lock:
	$(DOCKER) build --target tooling -t pyintegrationtests-tooling .
	$(DOCKER) run --rm -v "$(CURDIR):/app" pyintegrationtests-tooling uv lock
image:
	$(DOCKER) build --target runtime -t pyintegrationtests:local .
run:
	@test -n "$(CONFIG)" || (echo "CONFIG is required for live execution"; exit 2)
	$(DC) -f compose.yaml -f compose.live.yaml run --rm dev pyintegrationtests run $(SUITE) --config "$(CONFIG)" --live $(ARGS)
cleanup:
	@test -n "$(CONFIG)" -a -n "$(JOURNAL)" || (echo "CONFIG and JOURNAL are required"; exit 2)
	$(DC) -f compose.yaml -f compose.live.yaml run --rm dev pyintegrationtests cleanup "$(JOURNAL)" --config "$(CONFIG)" $(ARGS)
