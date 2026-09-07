# Security policy

This repository is an alpha infrastructure test runner. Report suspected vulnerabilities
privately through GitHub's security advisory feature for this repository. Do not include
real tokens, kubeconfigs, resource payloads or account identifiers in a public issue.

Supported code is the current default branch. Live execution should use short-lived,
least-privilege credentials and dedicated sandbox resources. Preserve journals and reports
as private artifacts. The DSL and Python plugins are trusted input; do not execute suites
or install plugins from an untrusted source.
