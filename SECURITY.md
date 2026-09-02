# Security Policy

## Reporting a Vulnerability

If you believe you've found a security vulnerability in this project, please report it
responsibly using GitHub's private security advisory feature rather than opening a
public issue:

**https://github.com/ShreyasUpadhyaya/decision-ledger/security/advisories/new**

This creates a private channel between you and the maintainer to discuss the issue,
work out a fix, and coordinate disclosure before any public details are shared.

Please include as much detail as you can:

- A description of the vulnerability and its potential impact
- Steps to reproduce, or a proof of concept
- The affected component(s) and, if known, the affected version/commit

## Scope

The following are considered in scope for security reports:

- The backend API (FastAPI application under `backend/app/`)
- The deterministic rule engine (`backend/app/core/`)
- Authentication and role-based access control (RBAC)
- The encrypted, versioned ruleset store

Issues in third-party dependencies should generally be reported upstream, but feel free
to flag them here too if they materially affect this project.

## Response Times

This is a personal/portfolio project maintained by a single person. Reports are taken
seriously and will be looked at as promptly as possible, but response and fix times are
best-effort — there is no SLA. Thank you for your patience and for helping keep this
project secure.
