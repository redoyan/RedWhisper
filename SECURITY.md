# Security Policy

## Reporting a vulnerability

Please do not disclose vulnerabilities, credentials, private transcripts, or
personal information in a public issue.

Use **Security → Report a vulnerability** in the GitHub repository to submit a
private security advisory. Include the affected version or commit, the impact,
and a minimal reproduction that contains no real API keys or dictated content.

## If a credential is exposed

Revoke or rotate it with the provider immediately. Removing a key from the
latest file is not enough because it may remain in Git history, pull-request
refs, forks, caches, or build logs. After rotation, repository maintainers
should remove the value from history and run a full-history secret scan before
making the repository public again.

Red Whisper stores saved provider keys in macOS Keychain. Keys, local settings,
recordings, transcripts, logs, and replacement dictionaries must never be
committed.

## Automated checks

Every push and pull request runs a Gitleaks scan. Contributors should also
inspect their staged diff before committing and use obvious fake values such as
`test-key` in tests and documentation. Maintainers should run an additional
full-history scan before changing repository visibility.
