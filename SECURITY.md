# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

This project is in active development. Only the latest release receives security updates.

## Reporting a Vulnerability

**⚠️ DO NOT open public GitHub issues for security vulnerabilities.**

Please report security issues privately via:
- Email: [Create a private security advisory if available]
- Direct message to maintainers

### What to Include
- Clear description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if known)
- Your contact information for follow-up

### Response Timeline
- **Acknowledgment**: Within 48 hours
- **Initial Assessment**: Within 1 week
- **Fix Development**: 2-4 weeks (severity-dependent)
- **Public Disclosure**: After fix release + user update window

## Security Architecture

### 🔒 Defense in Depth

UmaBot implements multiple security layers:

1. **Risk-Based Tool Approval**
   - 🟢 GREEN: Auto-approved (read-only)
   - 🟡 YELLOW: Auto-approved with logging (safe writes)
   - 🔴 RED: Owner confirmation required (destructive ops)

2. **Cryptographic Confirmation Tokens**
   - 128-bit entropy (16 hex characters)
   - Single-use, session-scoped
   - Hashed before logging (SHA256)
   - Example: `YES a1b2c3d4e5f67890`

3. **Secret Management**
   - macOS Keychain integration (automatic)
   - `~/.umabot/.env` fallback (0600 permissions)
   - Environment variable support
   - **Never stored in config.yaml**

4. **Input Validation**
   - JSON Schema validation for all tool args
   - Parameterized SQL queries (no SQLi)
   - `shlex.split()` for shell commands (no command injection)
   - Path traversal prevention in skills

5. **Skill Isolation**
   - Subprocess execution (isolated process)
   - Per-skill virtualenv
   - 20-second timeout (configurable)
   - Explicit tool allowlists

6. **Control Panel Separation**
   - Dedicated bot/connector for owner
   - Separate from public message channels
   - Confirmation commands owner-only

7. **Log Sanitization**
   - API keys masked to show only last 4 chars
   - Confirmation tokens hashed (SHA256) before logging
   - LLM response content NEVER logged (only length)
   - Tool arguments sanitized (only count, not values)
   - User messages stored in DB, not logged to files

## Security Best Practices

### For Users

**✅ DO:**
- Use environment variables for secrets in production
- Review skill tool allowlists before installation
- Keep UmaBot updated (`git pull && make upgrade`)
- Monitor logs for suspicious activity
- Rotate API keys every 90 days
- Use separate Telegram bot for control panel

**❌ DON'T:**
- Commit `config.yaml` or `.env` to git
- Share your control panel chat ID
- Enable shell tool unless necessary
- Grant broad tool allowlists to untrusted skills
- Run UmaBot as root

### Environment Variables

```bash
# LLM Provider
export UMABOT_LLM_API_KEY="sk-..."

# Connector Tokens (format: UMABOT_CONNECTOR_<NAME>_TOKEN)
export UMABOT_CONNECTOR_CONTROL_PANEL_BOT_TOKEN="123:ABC..."
export UMABOT_CONNECTOR_PUBLIC_TELEGRAM_TOKEN="456:DEF..."

# WebSocket Authentication
export UMABOT_WS_TOKEN="<generated-during-init>"
```

### File Permissions

```bash
# Secure config directory
chmod 700 ~/.umabot
chmod 600 ~/.umabot/config.yaml
chmod 600 ~/.umabot/.env
chmod 600 ~/.umabot/umabot.db
```

### For Skill Developers

**Minimal Tool Allowlist:**
```yaml
# SKILL.md frontmatter
allowed_tools:
  - web.get  # Only what you need
  - file.read
```

**Safe Subprocess Calls:**
```python
# ❌ NEVER use shell=True
os.system(f"cat {user_input}")  # VULNERABLE

# ✅ Use list arguments
subprocess.run(["cat", user_input], check=True, capture_output=True)
```

**Input Validation:**
```python
import json
import sys

try:
    data = json.loads(sys.stdin.read())
    assert all(k in data for k in ["required_field"])
except (json.JSONDecodeError, AssertionError) as e:
    print(f"Invalid input: {e}", file=sys.stderr)
    sys.exit(1)
```

## Security Audit History

### 2026-02-28 Internal Audit

**Status:** ✅ All findings addressed

| Severity | Issue | Status |
|----------|-------|--------|
| CRITICAL | API keys logged in plaintext | ✅ Fixed (masked) |
| CRITICAL | Connector tokens in config | ✅ Fixed (env vars) |
| CRITICAL | LLM response content logged | ✅ Fixed (removed) |
| CRITICAL | Tool arguments logged | ✅ Fixed (sanitized) |
| HIGH | Confirmation tokens logged | ✅ Fixed (hashed) |
| HIGH | .env directory permissions | ✅ Fixed (0700) |
| HIGH | Telegram api_hash logged | ✅ Fixed (masked) |
| MEDIUM | Low token entropy (48-bit) | ✅ Fixed (128-bit) |
| LOW | Code duplication | ℹ️ Acceptable |
| LOW | Missing docstrings | ℹ️ Future work |

## Compliance

### OWASP Top 10 (2021)

| Risk | UmaBot Mitigation |
|------|-------------------|
| A01 Broken Access Control | ✅ Control panel isolation, risk tiers |
| A02 Cryptographic Failures | ✅ Keychain/env secrets, no plaintext |
| A03 Injection | ✅ Parameterized SQL, shlex, schema validation |
| A04 Insecure Design | ✅ Risk-based approval, skill isolation |
| A05 Security Misconfiguration | ✅ Secure defaults (shell disabled) |
| A06 Vulnerable Components | ✅ Minimal deps, regular updates |
| A07 Auth Failures | ✅ Token confirmation, control isolation |
| A08 Data Integrity | ✅ Skill validation, allowlists |
| A09 Logging Failures | ✅ Comprehensive logs, secret masking |
| A10 SSRF | ✅ URL validation in web tools |

### CWE Coverage

- **CWE-78** (Command Injection): Mitigated via `shlex.split()`
- **CWE-89** (SQL Injection): Mitigated via parameterized queries
- **CWE-22** (Path Traversal): Mitigated via skill path validation
- **CWE-200** (Info Disclosure): Mitigated via secret masking
- **CWE-798** (Hardcoded Credentials): Mitigated via keychain/env

## Known Limitations

1. **Secrets in Memory**
   - API keys loaded into process memory
   - Mitigation: Run on trusted host, limit process access

2. **Confirmation Tokens in Messages**
   - Tokens visible in Telegram/Discord UI
   - Mitigation: 128-bit entropy, single-use, session-scoped

3. **Database Encryption**
   - SQLite database not encrypted at rest
   - Mitigation: File permissions (0600), no sensitive data stored

4. **WebSocket Local Only**
   - Gateway binds to 127.0.0.1 (localhost)
   - Mitigation: Do not expose to external network

## Responsible Disclosure

We appreciate security researchers who responsibly disclose vulnerabilities. We commit to:
- Acknowledging reports within 48 hours
- Providing regular updates on fix progress
- Crediting researchers in release notes (with permission)
- Not taking legal action against good-faith researchers

## Additional Resources

- [OWASP Cheat Sheet Series](https://cheatsheetseries.owasp.org/)
- [CWE Top 25](https://cwe.mitre.org/top25/)
- [Python Security Guide](https://python.readthedocs.io/en/stable/library/security.html)
- [NIST Cybersecurity Framework](https://www.nist.gov/cyberframework)
