# Contributing

The project deliberately requires no host Python, uv, pip, virtual environment, Helm,
Node or cloud SDK. Install Docker and GNU Make-compatible `make`, then run:

```sh
make build
make test
make check
make validate
make package
```

`make test` executes unit, contract, plugin and example tests with container networking
disabled, enforces branch coverage at 80%, and writes `coverage.xml` plus
`reports/junit.xml`. `make check` runs Ruff, mypy strict, Pyright and schema drift checks.
`make format` applies Ruff formatting and safe fixes in Docker. `make schema` updates
generated JSON Schema after an intentional contract change. Commit `uv.lock` changes.

Set `PYTHON_VERSION=3.13` or `3.14` on `make build`/`make test` to match CI. Live tests are
never part of the default suite. Use a dedicated, explicitly configured sandbox and the
commands in [configuration.md](docs/configuration.md). Never use production credentials,
auto-discovered accounts or shared resources as cleanup targets.

Keep the engine provider-neutral. Adding an assertion for an existing response field should
change YAML only. Add an adapter for a new protocol/service; add a custom assertion only
when generic selectors and comparators cannot express a pure comparison. Preserve unknown
response fields. Every mutation needs an exact ownership proof and compensation or a
verification-only descendant contract.

Pull requests should state the concrete behavior change, public DSL/API impact, and Docker
commands run. Changes to cleanup, redaction, deadlines or provider effects need discriminating
failure-path tests. No redistribution license is currently granted; contribution acceptance
does not change that status.
