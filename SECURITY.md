# Security and privacy

This dashboard reads local engine logs. It does not need inference credentials
and does not call the inference service. Its `/api/` paths are local telemetry
routes, not embedded third-party credentials.

- Bind to `localhost` by default; do not expose directly to an untrusted network.
- Use an authenticated proxy or SSH tunnel for remote access.
- Log-derived chart text is escaped before rendering.
- Local telemetry, environment files and private-key files are git-ignored.
- The state endpoint reports only the log filename, not a user's home path.
- Samples and docs use generic hostnames, not literal network addresses.
- Commit author metadata uses a generic project identity.

Engine logs may contain prompts, command arguments, paths or other sensitive
text. Runtime event records are **not** a sanitized public dataset. Keep the
`data/` directory private; never publish it or bypass ignore rules. Review every
staged diff before pushing, including any new configuration or screenshots.

GitHub secret scanning and push protection should remain enabled. Neither
ignore rules nor automated scanners guarantee detection of every secret.

A public GitHub repository necessarily displays its hosting account. Rewriting
Git history removes old commits from branch history but does not guarantee
removal from GitHub caches, existing clones or known commit URLs. Contact GitHub
Support if previously published personal metadata needs complete removal.

For a vulnerability report, use the hosting platform's private reporting tools
when available; do not attach real credentials or sensitive logs to public
issues.
