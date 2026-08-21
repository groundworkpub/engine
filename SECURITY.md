# Security Policy

## Reporting a vulnerability

Email **groundworkpub@gmail.com** with the subject line `[security] <short description>`.

Please include reproduction steps, affected workflow or agent, and any logs. We acknowledge reports within 72 hours and will credit reporters in the fix notes unless anonymity is requested.

## Scope

In scope:

- Code in this repository (`agents/`, `.github/workflows/`)
- Workflow privilege escalation, secret exposure, or injection via untrusted input (RSS payloads, LLM output, third-party API responses)

Out of scope:

- gworky.com application security (report via the contact page)
- Automated scanner noise without a working proof of concept

## Security posture

- All credentials are injected at runtime through GitHub Actions encrypted secrets; none are committed
- Third-party actions are pinned to full commit SHAs to mitigate repo-jacking and tag-rename supply chain attacks
- Every workflow declares least-privilege `permissions`
- Untrusted external data (RSS, scraped HTML, LLM output) is validated before persistence
- Outbound scraping uses rate caps, standard browser headers, and per-source exception handling; no credential-bearing scans run against third-party sites

## Safe harbor

We consider good-faith research consistent with responsible disclosure to be authorized access.
