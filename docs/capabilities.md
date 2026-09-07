# Scope and capability matrix

PyIntegrationTests is a generic Python library, pytest plugin and thin CLI for declarative
integration tests across Helm, Kubernetes, AWS, Cloudflare, HTTP and DNS. Assertions operate
on complete adapter responses through one selector/comparator engine. The core contains no
PECO, landing-zone, chart, account, ARN, namespace, domain, tag or organization convention.

The V1 implements engine capabilities plus two examples. It does not migrate or claim live
parity with the 49 historical scenarios. “Contract” below means the primitive has offline
tests with SDK stubs/transports/fake subprocesses. “Composed” means the YAML sequence can be
built from tested primitives but the historical scenario has not been run. Live status is
`not run`: no account, zone or cluster was supplied or contacted.

Evidence families:

- `A`: generic selectors, strict types, missing/null, collections, transforms, composition.
- `T`: fresh eventual/consistent observations, deadlines, transient and terminal failures.
- `O`: intent journal, ownership, exact recovery, dependencies, idempotent cleanup, redaction.
- `H`: real Helm argv/timeouts/locks/copy plus dynamic Kubernetes get/list/mutate/readiness,
  jobs, logs and multi-container exec contracts.
- `W`: modeled generic boto3 calls, pagination, streams/binary and S3/IAM/KMS lifecycle.
- `C`: official Cloudflare SDK transport, full envelopes, pagination and zone confinement.
- `N`: HTTP/TLS/proxy/redirects, DNS states/TTL, ZIP/hash/base64/JSON and JWT crypto.

| ID | Historical case | V1 composition and evidence | Status |
| --- | --- | --- | --- |
| IT-001 | cloudflare/zone-via-gitlab | Helm phases + arbitrary CR + fresh API zone observation (`H+C+T+O`) | Composed; live not run |
| IT-002 | cloudflare/record | Full A/CNAME record data + list/TTL/proxy + DNS (`C+N+A`) | Contract; example A only; live not run |
| IT-003 | cloudflare/record-adoption | Exact API fixture identity, count/ID capture, CR deletion, disappearance (`C+H+O+T`) | Composed; live not run |
| IT-004 | cloudflare/ruleset | Nested rule JSON, regex values, redirect config and NS lookup (`C+N+A`) | Composed; live not run |
| IT-005 | cloudflare/full-stack | Multiple owned resources and arbitrary sync-wave annotations (`H+C+O+A`) | Composed; live not run |
| IT-006 | dynamodb/basic | Generic describe/list-tags and complete SDK fields (`W+A`) | Contract primitives; live not run |
| IT-007 | dynamodb/provisioned | Exact numbers and explicit missing defaults (`W+A`) | Contract primitives; live not run |
| IT-008 | dynamodb/gsi-lsi | JSONPath filtered lists, status and nested projections (`W+A`) | Contract primitives; live not run |
| IT-009 | dynamodb/streams | Boolean, view type and nonempty ARN (`W+A`) | Contract primitives; live not run |
| IT-010 | dynamodb/ttl | Generic TTL description and `anyOf` (`W+A`) | Contract primitives; live not run |
| IT-011 | dynamodb/pitr | Backup description and tags (`W+A`) | Contract primitives; live not run |
| IT-012 | dynamodb/sse-kms | Encryption fields plus externally protected key (`W+A+O`) | Composed; live not run |
| IT-013 | dynamodb/sse-aes256 | Explicit absence/non-KMS negative semantics (`W+A`) | Contract primitives; live not run |
| IT-014 | istio-api-gateway-route/basic | Arbitrary namespaced CRs, nested routes and policies (`H+A+T`) | Contract primitives; live not run |
| IT-015 | istio-lambda-gateway/basic | IAM/Lambda fixtures, PyJWT RS256, ACK, Route53 and HTTPS (`W+H+N+T+O`) | Composed; live not run |
| IT-016 | istio-lambda-gateway/multi-lambdas | Multiple identities/routes and authenticated GET/POST (`W+H+N+O`) | Composed; live not run |
| IT-017 | lambda/basic | Generic invoke bounded stream and correlated log reads (`W+T+A`) | Contract primitives; live not run |
| IT-018 | lambda/datadog | Arbitrary layer/env fields, sensitive output, invoke/logs (`W+A+O`) | Contract primitives; live not run |
| IT-019 | lambda/arm | Architecture/layer lists plus invoke/log observations (`W+A+T`) | Contract primitives; live not run |
| IT-020 | lambda/vpc | List fields and invocation/log observations (`W+A+T`) | Contract primitives; live not run |
| IT-021 | lambda/async-invoke | SQS fixture and generic event-invoke configuration (`W+O+A`) | Composed; delivery intentionally excluded |
| IT-022 | lambda/dlq | SQS/policy fixture, DLQ field and log observation (`W+O+T`) | Composed; live not run |
| IT-023 | lambda/rollout | ZIP/hash, code then config upgrades and fresh reads (`N+W+H+T`) | Contract primitives; live not run |
| IT-024 | lambda/esm-sqs | One-shot send mutation plus fresh log reads (`W+T+O`) | Contract primitives; live not run |
| IT-025 | lambda/esm-kinesis | Binary put-record, mapping state and logs (`W+T+O`) | Contract primitives; live not run |
| IT-026 | lambda/esm-dynamodb | Native typed item, stream mapping and logs (`W+T+O`) | Contract primitives; live not run |
| IT-027 | s3/basic | Encryption and tag API calls (`W+A`) | Contract primitives; live not run |
| IT-028 | s3/encryption-kms | KMS tag recovery, alias/disable/scheduled deletion, object head (`W+O`) | Contract lifecycle; live not run |
| IT-029 | s3/versioning | Repeated exact-key writes and paginated versions (`W+O+A`) | Contract lifecycle; live not run |
| IT-030 | s3/lifecycle | Nested rules, expiration and prefix fields (`W+A`) | Contract primitives; no multi-day wait |
| IT-031 | s3/cors | Explicit unordered set/multiset and scalar comparisons (`W+A`) | Contract primitives; live not run |
| IT-032 | s3/public-access-block | Exact expected provider code/status; unrelated failures rejected (`W+A`) | Contract; live not run |
| IT-033 | s3/policy | STS identity, decoded JSON and filtered statements (`W+A`) | Contract primitives; live not run |
| IT-034 | s3/adoption | Exact fixture, object/date captures and ownership (`W+O+A`) | Composed; live not run |
| IT-035 | s3/force-delete | Version/marker/multipart pagination, Helm upgrade, Job clone/readiness (`W+H+O`) | Contract lifecycle; live not run |
| IT-036 | s3-front-deployer/basic | Deterministic ZIP/S3 binary plus Job and object presence (`N+W+H+O`) | Contract primitives; live not run |
| IT-037 | workload/deployment-basic | Deployment/Service fields and generation-aware readiness (`H+A+T`) | Contract and Helm example; live not run |
| IT-038 | workload/job | Job Complete with terminal Failed precedence (`H+T`) | Contract; live not run |
| IT-039 | workload/cronjob | Sanitized Job creation from jobTemplate (`H+O`) | Contract; live not run |
| IT-040 | workload/deployment-autoscaling | Arbitrary ScaledObject/HPA/metric fields (`H+A+T`) | Contract primitives; load scaling not claimed |
| IT-041 | workload/deployment-istio | Arbitrary VS/Gateway/DR/Sidecar lists and metadata (`H+A`) | Contract primitives; live not run |
| IT-042 | workload/service-account-irsa | ACK IAM plus SA annotation and distinct RBAC groups (`W+H+A+O`) | Composed; live not run |
| IT-043 | workload/external-secrets | SSM fixture, CR condition and sensitive base64 Secret (`W+H+N+O`) | Contract primitives; live not run |
| IT-044 | workload/external-secrets-secretsmanager | JSON property extraction and Secret convergence (`W+H+N+T+O`) | Contract primitives; live not run |
| IT-045 | workload/certificate | Issuer fixture, Certificate condition and sensitive TLS Secret (`H+T+O`) | Contract primitives; live not run |
| IT-046 | workload/pdb | maxUnavailable and label fields (`H+A`) | Contract primitives; live not run |
| IT-047 | workload/multi-containers-volumes | Explicit container exec, stdout/stderr/exit code, expected refusal (`H+A`) | Contract; live not run |
| IT-048 | workload/configmaps | Managed/external/file data, environment and pod exec (`H+N+O`) | Contract primitives; live not run |
| IT-049 | workload/configmap-reload | Immutable before/after captures, fresh revision/checksum, reinstall identity (`H+T+O+A`) | Contract primitives; live not run |

## Verified V1 claims and limits

Offline tests instantiate the real boto3, Cloudflare and Kubernetes libraries with Stubber,
MockTransport or fake dynamic resources. They cover multiple pages, unknown fields, streams,
404/403/429, request validation, cleanup ordering, response loss, stale controller state,
two cleanup passes, secret masking, JUnit/JSON, pytest collection and Dockerized Helm render.
The two example workflows run end-to-end against simulated provider state.

The repository contains no 49-suite migration, no historical shell calls, no CRD/operator,
no cluster provisioning, no arbitrary host shell/Python execution, no DAG/parallel scheduler,
no xdist guarantee, no finalizer removal, no daemon janitor and no live snapshot. Helm 3.19
is tested in Docker. Cloudflare and AWS behavior beyond the SDK/transport contracts still
requires sandbox validation. A generic mutation without enough information for exact cleanup
is rejected rather than guessed.

The `pyintegrationtests` distribution name was not present on PyPI when checked on
2026-09-07. This is only a point-in-time observation; recheck immediately before publishing.
No publication is configured, and this repository currently grants no redistribution license.
