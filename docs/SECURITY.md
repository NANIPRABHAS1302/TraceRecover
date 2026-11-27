# TraceRecover Connector Security Architecture

`connectors/permissions.py` enforces defense-in-depth across every external connector:

1. **Encrypted Secret Vault (`SecretVault`)**: API keys, bearer tokens, and connection strings saved through Connect Hub are encrypted on disk using `cryptography.fernet.Fernet` (`vault.enc.json` + `.vault.key` or `TRACERECOVER_VAULT_KEY`) or resolved dynamically from `env:<VAR_NAME>` references.
2. **Zero Client Exposure**: Plaintext secrets are never returned by `/api/connectors*`, never embedded in JavaScript bundles, and never written to `localStorage` or `sessionStorage`.
3. **Log & Trace Redaction (`redact_secrets`)**: Known secret patterns (`AIza...`, `sk-...`, `ghp_...`, `github_pat_...`, `tvly-...`, `Bearer ...`, `db://user:pass@`) and resolved vault/env values are masked before writing connector logs or surfacing error text.
4. **Least-Privilege Defaults (`PermissionEngine`)**: Connectors default to `read_only=True`, domain allowlists/blocklists, request timeouts, and per-minute sliding-window rate limits. Write/mutating actions require explicit approval.
5. **Untrusted Data Isolation**: External payloads are framed inside `[UNTRUSTED_EXTERNAL_DATA source="..."]` markers with prompt-injection directive filtering.
6. **Optional Admin Authentication**: All mutating `/api/connectors*` and browser action endpoints respect `TRACERECOVER_ADMIN_TOKEN` via Bearer token or `X-API-Key` header.
