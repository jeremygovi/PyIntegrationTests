# Helm workload example

This opt-in example installs a standalone Deployment and Service, waits for a fresh ready
Deployment, checks arbitrary labels/annotations/probes/status fields, uninstalls, and verifies
both Kubernetes descendants disappeared. Cleanup tracks the release plus both descendants.

Copy `config.example.yaml` to an ignored `config.local.yaml`, set an explicit sandbox context,
namespace and container path to a dedicated kubeconfig, then use the Docker command documented
in [configuration](../../docs/configuration.md). The namespace must already exist. The runner
does not provision it or the cluster. CI validates the DSL and renders/lints the chart offline;
it does not execute this smoke test.
