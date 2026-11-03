# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately through GitHub's [private vulnerability reporting][gh-pvr]:
the repository's **Security** tab → **Report a vulnerability**.

Include the affected version, a description of the issue, and steps to
reproduce. You can expect an initial acknowledgement within a few days.

[gh-pvr]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Supported versions

pw-core follows semantic versioning. Security fixes land in the latest release
of the current major line (1.x); there is no long-term-support branch for an
older line. Pin `~=1.0` so a patch release reaches you, and upgrade promptly
when a fix ships.

## Scope

pw-core is a framework library, not a deployed service. The most
security-relevant surfaces are:

- **`pw_core.clients`** — the LLM transport layer (HTTP requests, subprocess
  invocation, API-key handling).
- **`pw_core.web`** — the FastAPI app factory and its error-page and
  rate-limit middleware, and the mountable admin routers, which refuse to
  mount without an `auth_dep` unless `allow_unauthenticated=True` is passed.
- **`pw_core.fetch`** — outbound HTTP: the SSRF guard on every URL and
  redirect hop (`pw_core.utils.url_safety`), response-size caps, and
  `pw_core.fetch.images`, which writes downloaded files to disk.
- **`pw_core.db`** — the database/connection layer.

API keys and other secrets are read from the environment and must never be
committed. pw-core does not log credential values.
