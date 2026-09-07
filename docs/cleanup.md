# Ownership, cleanup and recovery

Each case gets a random 128-bit run ID. A mutation must declare one of:

- `creates`: one exact target that must be absent before the call;
- `tracks`: additional exact descendants expected to be created by the same call;
- `uses`: a resource previously created and still proven to belong to this run.

Every resource has an exact read-only `probe`, `absent` predicates, and positive `owned`
predicates. At least one equality ownership predicate must contain the current run ID after
references resolve. An external namespace, zone, account, VPC, key, SecretStore or release
therefore cannot become owned merely because a name is similar. Kubernetes creation stamps
`pyintegrationtests.io/run`; updates require that label and UID/resource-version guards.
Cloudflare mutations may target children of the configured zone, never the zone itself.

Before the first possible side effect the runner verifies absence and atomically records an
`intent`. A successful call marks it `created`. If the response is lost, cleanup repeats the
exact probe and requires the same ownership proof. It never searches by prefix. A scenario
may define an exact tag query, such as `aws.kms.find`, as its probe for APIs without a stable
name. Ambiguous results fail ownership checks.

The journal is versioned JSON, written atomically with `0700` directories and `0600` files
on POSIX. It stores provider identity, public resource identity, compensation and state. It
does not store credentials or sensitive restore payloads. A configuration hash prevents a
journal from being replayed against a different account/context/zone. On resume, provider
identity is rechecked using fresh credentials.

Compensations run in dependency order and continue across independent failures. `after`
means “clean this dependency first.” This supports S3 emptying before chart removal or a
Helm release before verifying descendant workloads. A verification-only tracked resource
omits `cleanup`, supplies `after`, `cleaned`, and optionally a different `verify` observation.
It detects a leak without directly deleting it and thereby preserves a failed product
finalizer as a failed test. Cycles are rejected before execution.

`cleanup` is idempotent. A manually removed target becomes `done`; completed entries are
not replayed. KMS `PendingDeletion` is an accepted terminal cleanup state, not immediate
deletion. IAM helpers detach policies before deleting a role. S3 cleanup paginates object
versions, delete markers, current objects, and multipart uploads. Page limits fail visibly.
No helper removes Kubernetes finalizers.

The pytest item owns the lifecycle: setup creates the private journal, call executes ordered
steps, and teardown always attempts cleanup. The original failure and teardown failure remain
separate pytest phases, producing a nonzero status. JSON/JUnit reports and bounded diagnostic
artifacts are generated before teardown. Sensitive inputs, action results and known sensitive
keys are redacted; arbitrary plugin output sections are suppressed.

Resume an interrupted run with the exact journal and original configuration:

```sh
make cleanup \
  JOURNAL=.pyintegrationtests/runs/RUN/SUITE/CASE/journal.json \
  CONFIG=config.local.yaml \
  ARGS='--env-file .env'
```

SIGINT/SIGTERM triggers bounded teardown when Python receives the signal. The runner sends
TERM then KILL to a timed-out Helm process group. SIGKILL, node loss, kernel failure and a
lost journal cannot provide immediate cleanup. Preserve `.pyintegrationtests/runs` as a
private CI artifact and invoke the exact journal later. Unknown ownership requires manual
investigation; the runner will not broaden deletion criteria.
