# YAML contract: v1alpha1

Suites use `apiVersion: pyintegrationtests/v1alpha1` and the reserved suffix
`.integ.yaml` or `.integ.yml`. These documents are local files, not Kubernetes CRDs.
`make validate` and `make list` never obtain credentials or construct SDK clients.
The editor schema is [suite.schema.json](../schemas/suite.schema.json); concrete
action parameter schemas are in [actions.schema.json](../schemas/actions.schema.json).
Generate them with `make schema`; `make check` rejects stale generated files.

## Documents and steps

Suite fields: `id`, optional `name`, `config` (relative to the suite), `labels`, `inputs`,
`sensitiveInputs`, `defaults.timeout`, and a nonempty `tests` list. Each test has a
unique `id`, optional `name`, `labels`, `timeout`, and ordered `steps`. Test IDs and
step IDs must match `[a-z][a-z0-9_-]*`. `kind` is always explicit.

```yaml
apiVersion: pyintegrationtests/v1alpha1
id: basic
inputs: {desired: 2}
tests:
  - id: typed-comparison
    steps:
      - kind: action
        id: snapshot
        action: data.value
        with: {value: {replicas: 2, extraField: true}}
        captures:
          replicas: {path: $.replicas}
        assertions:
          - path: $.replicas
            op: equal
            value: {$ref: {source: inputs, path: $.desired}}
      - kind: assert
        id: another-field
        source: steps.snapshot.data
        assertions: [{path: $.extraField, op: equal, value: true}]
```

An action has `action`, optional named `provider`, `with`, `captures`, `assertions`,
`expectedError`, `sensitive`, and ownership declarations (`creates`, `uses`, `tracks`).
Every mutation needs `creates`, `tracks`, or `uses`; see [cleanup](cleanup.md). An assertion
step has exactly one of `source` and `observe`. An observation is an action invocation
classified as a read, with optional `notFound: 'null'` (quote the word).

`expectedError` requires `provider` and at least one exact `code` or `status`.
When both are supplied both must match. It never accepts an unrelated connection,
authentication or schema failure. HTTP status is normally returned in `data.status`;
set `raiseForStatus: true` to expose it as an operation error.

## References, literals and captures

`{$ref: {source: ..., path: ...}}` replaces the complete value and retains its type.
Permitted sources are `inputs`, `run`, non-sensitive `config`, `steps.ID.data`,
`steps.ID.meta`, and `steps.ID.captures.NAME`. `run` contains `id`, `suiteId`, `caseId`.
`path` defaults to `$` and must select exactly one value. Missing or multiple matches
are errors. Unknown/future sources and malformed paths are rejected before execution;
response-dependent cardinality and types are checked when the response exists.

Captures are deeply immutable snapshots. A capture specifies `path`, optional
`transforms`, and `sensitive`. Temporal assertions require a fresh `observe`; they
cannot repeatedly compare a snapshot. New observed fields require only YAML changes.

To pass an API object containing a literal `$ref` or `$format`, wrap it in
`{$literal: <object>}`. No further references are expanded inside that object.

```yaml
path:
  $format:
    template: '/zones/{zone}/dns_records/{id}'
    values:
      zone: {$ref: {source: config, path: $.providers.cf.zoneId}}
      id: {$ref: {source: steps.create.captures.id}}
```

Formatting accepts scalar named values only: no attribute access, indexing, conversion,
format specifications, Jinja, eval or Python imports. Cleanup invocations and their
verification may additionally use `resource.data`, the fresh ownership probe response.
This allows exact recovery when a creation succeeded but its response was lost.

`name.generate` returns `{name: ...}` with a sanitized prefix and 12 hexadecimal hash
characters derived from run/case/prefix. Set `maxLength` to the provider constraint
(53 for Helm; default 63). Different run IDs produce different names without CI variables.

## Selectors and comparisons

The dialect is a bounded subset of **jsonpath-ng.ext**, not kubectl JSONPath or RFC 9535.
Root/current nodes, children, fields, indices, slices, wildcards and simple comparison
filters are supported. Recursive descent, arithmetic, named operators and regex filters
are rejected. Expressions are at most 1024 characters. Quote bracket-containing paths
inside YAML flow mappings, for example `path: '$.items[0].id'`.

Examples: `$.metadata.annotations['example.com/check']`,
`$.spec.template.spec.containers[0].livenessProbe`,
`$.status.conditions[?(@.type == 'Ready')].status`.

| Operators | Semantics |
| --- | --- |
| `equal`, `notEqual` | Strict types; `true`, `1`, `1.0`, `"1"` differ |
| `exists`, `notExists` | Presence; present `null` exists |
| `isNull`, `isNotNull` | Present values only |
| `contains`, `notContains` | String substring, list element, or dictionary key |
| `isSubset`, `isNotSubset` | Actual is/is not a recursive subset of expected; list duplicates count |
| `lengthEqual` | Exact string/list/dictionary length |
| `isType` | `string`, `integer`, `number`, `boolean`, `array`, `object`, `null` |
| `isEmpty`, `isNotEmpty` | String/list/dictionary emptiness; never null/false/zero |
| `greater`, `greaterOrEqual`, `less`, `lessOrEqual` | Numeric operands only, excluding booleans |
| `matchRegex` | Python regex, max 512 pattern characters; bounded text |
| `allOf`, `anyOf`, `not` | Nested `checks`; not requires exactly one check |
| `custom` | Explicit registered `assertion` and `value`; [extension contract](plugins.md) |

A missing field is distinct from null and fails every value comparison, including
`notEqual`. `default` explicitly supplies a value for a missing scalar. Multiple matches
require `quantifier: any` or `all`; an empty selection fails unless `allowEmpty: true`.
List equality defaults to `order: ordered`; `set` ignores duplicates, `multiset` retains
their counts. Every branch of a composition is evaluated so invalid selections or
transformations cannot be hidden by a successful sibling.

Transforms are explicit ordered objects: `transforms: [{name: decodeBase64},
{name: decodeJson}]`. Available names: `decodeBase64`, `decodeJson`, `sort`, `lower`,
`stripTrailingDot`, `decimal`. Transformation failures are errors, not empty values.
Decimal responses normalize to exact strings; `decimal` enables exact numeric comparisons
without an intermediate float. Datetimes normalize to ISO 8601. Bytes normalize to
`{"$bytes": "base64..."}`. Select that field before `decodeBase64`.

## Temporal assertions

`mode: immediate` takes one observation. `eventually` repeats a read until every assertion
on that same response passes or the deadline expires. `consistently` samples repeatedly
for `timeout` seconds and fails on the first contradiction; this is not proof of invariance
between samples. Defaults: 60-second timeout, 1-second interval. Jitter, retry-after and
per-observation budgets are bounded by the case deadline. Cleanup has a separate budget.

`failWhen` is a list of predicates; any match aborts immediately, before success checks.
401/403 and schema/parameter errors fail immediately. Only adapter-classified transient
errors are retried during `eventually`. Mutations are never retried by the runner.
An explicit POSIX timer interrupts blocking calls on the sequential main thread; SDK
timeouts remain configured as a second boundary. Docker supplies this execution environment.

Deployment readiness checks observed generation and desired updated/ready/available replicas.
Job failure takes precedence over completion. ACK terminal conditions take precedence over
synced conditions. With ACK, supply `expectedGeneration` if the controller supports it;
otherwise assert a fresh external field rather than trusting an old synced condition.

## Action catalog

| Action | Parameters / behavior |
| --- | --- |
| `data.value` | Typed local `value` |
| `name.generate` | `prefix`, `maxLength` |
| `artifact.zip` | Text `files` mapping, optional `name`; deterministic private ZIP, path and SHA-256 |
| `artifact.read` | `path`, `encoding: text/base64/json`; bounded, sensitive |
| `artifact.hash` | `value` text/binary/file, `algorithm: sha256/sha512` |
| `jwt.sign` | `keyEnv`, `claims`, `headers`, `algorithm`; RS256 default, PyJWT crypto |
| `helm.command` | `operation`, `release`, `chart`, `version`, ordered `valuesFiles`, typed `values`, `wait`, `workspaceRoot` |
| `kubernetes.resource` | `apiVersion`, `kind`, `namespace`, exact `name`, `operation`, selectors, `body`, patch options, readiness |
| `kubernetes.logs` | Pod `name`, explicit `container`, `namespace`, `tailLines`, `sinceSeconds` |
| `kubernetes.exec` | Pod `name`, explicit `container` and `argv`; stdout/stderr/exitCode; mutation with ownership |
| `kubernetes.job` | `source`, new `name`, `fromCronJob`, `namespace`, extra `labels` |
| `aws.call` | `service`, public snake_case `operation`, native `parameters`, `pagination`, `maxPages`, `readStreams` |
| `aws.s3.empty` | Exact `bucket`, `deleteBucket`, `maxPages`; versions, markers, ordinary objects, multipart |
| `aws.iam.delete-role` | Exact `roleName`; detach policies, remove inline policies, detach profiles, delete role |
| `aws.kms.find` | Exact `tags` mapping and `maxPages`; recover matching keys after unknown create result |
| `aws.kms.schedule-deletion` | `keyId`, `pendingWindowInDays` (7–30); aliases, disable, schedule deletion |
| `cloudflare.request` | `method`, relative `path`, `query`, `body`, `pagination`, `maxPages` |
| `http.request` | `method`, `url`, `headers`, `query`, `body`, `jsonBody`, `followRedirects`, `raiseForStatus` |
| `dns.query` | `name`, `recordType`; structured ANSWER/NODATA/NXDOMAIN, TTLs, records and full textual response |

AWS accepts `{$file: path}` and `{$bytes: base64}` within SDK parameters for binary payloads.
All-pages results use `data.pages`, retaining every page and its metadata. A page limit
raises an incomplete-result error; it never reports a partial list as a complete total.
AWS all-pages requires an SDK paginator. Cloudflare all-pages requires page/total_pages
or cursor metadata; endpoints without supported metadata require explicit page mode.

Helm supports install/upgrade/uninstall/status/get-values/get-manifest/dependency-build.
Dependency builds require the committed lock when dependencies exist and operate in a
private copy preserving `file://` siblings inside `workspaceRoot`. No implicit dependency
update, atomic rollback, namespace creation or cluster provisioning occurs. Helm 3.19.0
is the tested Docker binary. A Helm 4 compatibility claim requires separate tests.
