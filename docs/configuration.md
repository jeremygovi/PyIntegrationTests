# Configuration and Docker execution

Use `make build` once, then `make test`, `make check`, `make validate`, `make list`.
Only Docker and Make run on the host. Python, Helm, uv, Ruff, mypy, Pyright and its Node
runtime are installed inside images. Development dependencies are resolved in `uv.lock`;
`make lock` updates the lock in Docker. Normal builds export that lock and install it directly
into the container's disposable Python installation; they do not create a virtual environment.

Copy an example configuration to a gitignored `*.local.yaml` file. Pass its path with
`--config` (CLI) or `--pit-config` (pytest); otherwise a suite may name a config relative
to itself. `.env` is read only when explicitly requested. Relative artifact paths are
relative to the process working directory; chart/file inputs are relative to the suite.
Provider credential file paths refer to paths **inside the container**.

```yaml
apiVersion: pyintegrationtests/v1alpha1
providers:
  cluster:
    kind: kubernetes
    context: sandbox
    namespace: integration-tests
    kubeconfig: /credentials/kubeconfig
  charts:
    kind: helm
    kubernetesProvider: cluster
  aws:
    kind: aws
    region: eu-west-3
    accountId: '123456789012'
    profile: sandbox
    operations:
      s3.head_bucket: read
      s3.create_bucket: mutate
      s3.delete_bucket: mutate
      lambda.invoke: mutate
      sqs.receive_message: mutate
  cf:
    kind: cloudflare
    zoneId: explicit-sandbox-zone-id
    apiTokenEnv: CLOUDFLARE_API_TOKEN
  web:
    kind: http
    verify: true
    trustEnv: false
  dns:
    kind: dns
    nameservers: ['192.0.2.53']
timeouts: {caseSeconds: 900, observationSeconds: 15, cleanupSeconds: 300}
cleanup: {policy: strict}
artifacts: {directory: .pyintegrationtests/runs, maxBytes: 8388608}
plugins: []
```

Multiple named providers of the same kind are independent. Credentials and kubeconfig
are never changed globally to switch a provider. Kubernetes cluster-scoped operations
require `allowClusterScoped: true`. Helm always uses its linked Kubernetes provider's
context, namespace and kubeconfig.

AWS region and account are explicit target configuration, not inferred from an ambient
account. STS verifies the account before execution. SDK profile/role chains retain native
credential refresh. Standard environment credentials are read by a dedicated session;
the explicit env file can supply access key, secret key, session token, profile, and
credential/config file locations. AWS SDK endpoint and TLS verification are configurable.
Do not classify a mutation as a read: operation availability does not imply replay safety.
If the AWS extra is present, request models are also validated offline without a client.

Cloudflare uses the official SDK with retries disabled. `baseUrl` defaults to the official
API. Scenario paths cannot override the origin. Mutations are restricted to children of
the configured external zone; deleting that zone is prohibited. Account/zone identity is
recorded for cleanup. HTTP supports `baseUrl`, `proxy`, `verify` (boolean or CA path),
`trustEnv`, explicit redirects and per-call limits. DNS `nameservers` may be ordinary
resolver addresses or HTTPS resolver endpoints supported by dnspython's DoH extra.

## Priority and environment variables

For mapped options: defaults < config < explicit env file < process environment < CLI.
Business inputs and Helm values are separate from engine configuration.

| Variable | Configuration field |
| --- | --- |
| `PYINTEGRATIONTESTS_CASE_SECONDS` | `timeouts.caseSeconds` |
| `PYINTEGRATIONTESTS_OBSERVATION_SECONDS` | `timeouts.observationSeconds` |
| `PYINTEGRATIONTESTS_CLEANUP_SECONDS` | `timeouts.cleanupSeconds` |
| `PYINTEGRATIONTESTS_ARTIFACTS_DIRECTORY` | `artifacts.directory` |

`--case-seconds` / `--pit-case-seconds` has priority over case and suite timeout defaults.
Without it, the case timeout overrides the suite default, then the configured engine default.
SDK variables include `AWS_PROFILE`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
`AWS_SESSION_TOKEN`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, `KUBECONFIG`, and
`CLOUDFLARE_API_TOKEN`. See [.env.example](../.env.example). No secret manager is required.

## Explicit live execution

```sh
make run SUITE=examples/helm-workload CONFIG=config.local.yaml \
  CREDENTIALS_DIR=/absolute/path/to/dedicated/credentials

make run SUITE=examples/cloudflare-record CONFIG=cloudflare.local.yaml \
  ARGS='--env-file .env --junit-xml reports/live.xml --json-report reports/live.json'
```

The first command mounts the dedicated credential directory read-only at `/credentials`.
The second reads the ignored `.env` already inside the project mount. Neither command
discovers a sandbox automatically. `make run` explicitly enables networking and live tests;
normal test/validation containers have `network_mode: none`. Kubeconfig exec authentication
requires the referenced executable inside a derived image; cloud login tools are not
silently installed. For EKS, supply a derived image containing your approved authentication
helper or a compatible kubeconfig. The runner itself does not provision EKS or grant IAM.

`make image` builds the runtime image, which runs as an unprivileged user. Mount the suite
and writable artifacts directory at `/work`; adjust the container UID to your host's file
ownership when needed. `docker run --rm pyintegrationtests:local --help` shows the CLI.
No PyPI upload is performed by the supplied workflows.
