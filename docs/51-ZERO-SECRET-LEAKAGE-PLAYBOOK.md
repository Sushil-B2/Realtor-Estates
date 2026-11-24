# Secret-handling playbook

This playbook reduces exposure risk; no scanner, instruction file, or workflow can guarantee that secrets never leak. Treat `AGENTS.md` as the project agent contract and keep it under review. Agent instruction files are advisory, not a security boundary: enforce critical controls with platform permissions, CI, and protected branches as well.

## Before any task

- Limit access to the specific project and resources needed. Never change sibling Vercel projects or team-wide settings as part of this project.
- Do not display, copy into chat, put in tool arguments, commit, or log secret values, database connection strings, tokens, private keys, webhook signing secrets, or local environment files. Never use shell tracing when handling them. Avoid printing full errors, HTTP headers, or provider responses, which may contain credentials.
- Use a secret manager and short-lived, least-privilege credentials. Inject values directly into a process or write them into Vercel through its dashboard as sensitive, write-only variables. Do not use a chat connector to transmit secret values.
- Treat local environment files as sensitive: exclude them from Git, bundles, backups shared with agents, and logs; restrict file permissions. Existence checks should return only a boolean. Avoid exposing even hashes or fingerprints unless there is a justified operational need.

## Developing and reviewing

- Use synthetic, nonfunctional placeholders for documentation and tests. Never put production or working demo credentials in client code or test fixtures. Review existing fixtures and public assets separately; a scanner passing does not prove they are safe.
- Keep secrets server-side; never expose them through browser bundles, service workers, API responses, source maps, analytics, or error reporting. Check role-level access and cache isolation for tenant data.
- Run `python3 scripts/check_secrets.py` before commits and packaging. Add an independently maintained secret scanner in CI, a pre-commit hook, and protected-branch checks. A local hook can be bypassed; CI must independently enforce the policy. Include repository history and release artifacts in scheduled scans, with reporting that redacts matches. Review scanner rules and exclusions periodically.
- Before running a command or reading a file, consider whether its output might include secrets. Run sensitive processing locally inside a narrowly scoped broker, with shell tracing disabled and sanitized error handling. Keep values out of command-line arguments, process listings and temporary files where possible. Do not assume a script is safe merely because it claims to redact output; audit it and its dependencies.

## Deployment

1. Confirm the target is `realtor-estates-pwa` only. Do not touch sibling projects or team-level settings.
2. Run the secret, JavaScript, accessibility and test gates in `AGENTS.md`. Review results without exposing matches. A failed or incomplete gate blocks promotion.
3. Review the bundle allowlist and independently scan the artifact. Use the local `scripts/safe_bundle_upload.mjs` broker only after reviewing its behavior and prerequisites. Treat its output and provider errors as potentially sensitive; stop if unexpected material appears.
4. Update only non-secret deployment metadata through the connector. Configure secret values directly in the provider's sensitive-variable UI; never retrieve decrypted values into an agent or chat. Use a preview deployment and validate authentication, authorization, tenant isolation, migrations, and latency before production promotion.
5. Verify actual deployed routes and response times with a representative authenticated test account, including cold and warm samples. Avoid collecting tokens or PII in benchmark logs. Keep rollback instructions and the previous verified deployment available.

## Suspected exposure

1. Stop the workflow. Assume exposed credentials are compromised; don't repeat or quote them in an incident ticket.
2. Revoke or rotate affected credentials at the issuer immediately, invalidate sessions where appropriate, and replace values directly in the secret manager and deployment provider. Check whether other systems reused the same credential.
3. Notify the incident owner, inspect relevant access and audit logs safely, and assess affected resources and data. Preserve evidence according to policy without spreading the secret.
4. Remove exposed material from logs, artifacts and repository history where feasible; deletion alone does not revoke access. Review downstream caches and copies, and record the remediation without storing the secret.

## Maintaining the contract

Keep `AGENTS.md` and `CLAUDE.md` in the repository and ensure agent tooling loads them. Review them when tools or deployment architecture change. Automated permission boundaries, secret-scanning checks and audit reviews remain necessary even when every agent is instructed to comply.
