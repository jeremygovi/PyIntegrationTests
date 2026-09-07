# PyIntegrationTests

Declarative infrastructure integration testing, using pytest and ordinary YAML.
Python 3.12–3.14. **Alpha: validate against your sandbox before production use.**

Development, checks and packaging run exclusively in Docker. No local Python installation,
virtual environment, cloud credentials or Kubernetes cluster is required for offline tests.

```sh
make build
make test
make check
make validate
make list
make package
```

The CLI provides `validate`, `list`, `run`, `cleanup` and `--version`. The pytest plugin
collects only `*.integ.yaml` / `*.integ.yml`. Live execution requires `--pit-live` and
an explicit provider configuration. It never provisions a cluster.

Documentation: [DSL](docs/dsl.md), [configuration](docs/configuration.md),
[cleanup](docs/cleanup.md), [extensions](docs/plugins.md),
[capability matrix](docs/capabilities.md), [development](CONTRIBUTING.md).

Two opt-in examples: [Helm workload](examples/helm-workload/README.md) and
[Cloudflare record](examples/cloudflare-record/README.md).

No redistribution license has been granted for this repository.
