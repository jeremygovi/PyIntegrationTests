.PHONY: help build test check format schema package validate list lock run cleanup image all
DOCKER_CONTEXT ?= $(shell docker context inspect desktop-linux >/dev/null 2>&1 && echo desktop-linux || echo default)
DOCKER = docker --context $(DOCKER_CONTEXT)
DC = $(DOCKER) compose
RUN = $(DC) run --rm dev
SUITE ?= examples
ARGS ?=
help:
	@echo "build test check format schema package validate list lock image all"
	@echo "run SUITE=path CONFIG=config.local.yaml [ARGS='--env-file .env']"
	@echo "cleanup JOURNAL=path CONFIG=config.local.yaml [ARGS='--env-file .env']"
all: build test check validate package
build:
	$(DC) build dev
test:
	$(RUN) python -m pytest --cov=pyintegrationtests --cov-report=term-missing --cov-report=xml --junitxml=reports/junit.xml
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
