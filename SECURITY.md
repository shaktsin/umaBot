# Security Policy

## Reporting a Vulnerability
Please report security issues privately by opening a private issue or contacting the maintainers directly. Include:
- A clear description of the issue
- Steps to reproduce
- Potential impact

## Supported Versions
This project is in early development. Only the latest release is supported.

## Security Practices
- Tool calls are validated with JSON Schema.
- Risk-tier enforcement (RED requires explicit user confirmation).
- Web and shell tools are disabled by default.
- Secrets are stored in macOS Keychain when available or a protected `.env` file on Linux.
