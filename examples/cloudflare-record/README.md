# Cloudflare record example

This opt-in example creates one uniquely named A record in a pre-existing sandbox zone,
captures its ID, verifies the complete API response, updates content/TTL while retaining the
ID, deletes the record, and verifies disappearance. The run ID in `comment` is the ownership
proof. The parent zone is protected from deletion by the adapter.

Copy `config.example.yaml` to an ignored `config.local.yaml`, set the exact sandbox `zoneId`,
put `CLOUDFLARE_API_TOKEN` in an ignored `.env`, and use the Docker command documented in
[configuration](../../docs/configuration.md). CI runs this flow only against the official SDK
and a simulated transport; it never discovers or contacts a real account.
