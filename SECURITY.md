# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

---

## Reporting a Vulnerability

If you discover a security vulnerability in crew-runner, please report it by emailing **security@alteriom.net** or opening a private security advisory on GitHub.

**Do NOT open public issues for security vulnerabilities.**

### What to Include
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if available)

### Response Time
- Initial response: Within 48 hours
- Patch timeline: Within 7 days for critical issues
- Public disclosure: After patch is released

---

## Security Best Practices

### Authentication
**Current Status (v1.0.0)**: ⚠️ **No authentication implemented**

Crew-runner v1.0.0 does NOT implement authentication. It should only be deployed:
- Behind a firewall
- On internal networks only
- With access restricted to trusted backend services

**Planned** (v1.1.0): API key authentication for all execution endpoints.

### Deployment Security

#### ✅ **DO**
- Deploy behind reverse proxy (Caddy, nginx)
- Use internal Docker networks
- Pin image versions (not `:latest`)
- Regularly update dependencies
- Monitor logs for suspicious activity
- Use secrets management for credentials

#### ❌ **DO NOT**
- Expose port 8081 publicly
- Use `:latest` tag in production
- Commit API keys or credentials
- Run as root (container uses non-root `appuser`)
- Disable health checks

### Credential Management

**Environment Variables**:
```bash
# Required
OLLAMA_BASE_URL=http://ollama:11434
LITELLM_API_KEY=sk-...
BACKEND_URL=http://backend:3007

# Optional
MAX_SESSIONS=2
WORKER_HEARTBEAT_INTERVAL=60
```

**Secrets Storage**:
- Use Docker secrets or environment files (`.env`)
- Never commit `.env` files to git
- Rotate API keys monthly
- Use separate credentials per environment (dev/staging/prod)

### Container Security

**Non-Root User**:
```dockerfile
# Crew-runner runs as non-root user 'appuser'
USER appuser
```

**Multi-Stage Build**:
- Build dependencies isolated from runtime
- Minimal runtime image (python:3.12-slim)
- No build tools in final image

**Health Checks**:
```yaml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### Network Security

**Internal Network Only**:
```yaml
# docker-compose.prod.yml
services:
  crew-runner:
    networks:
      - internal  # Not exposed to public network
```

**Port Binding**:
```yaml
# Bind to localhost only (not 0.0.0.0)
ports:
  - "127.0.0.1:8081:8081"
```

### Known Security Considerations

#### ⚠️ **No Zombie Process Vulnerability**
- Crew-runner uses pure Python/FastAPI (no subprocess spawning)
- CrewAI execution is in-process
- No SIGCHLD handler needed
- Verified: Zero zombie processes under load

#### ⚠️ **No Rate Limiting** (v1.0.0)
- Planned for v1.1.0
- Current mitigation: Deploy behind reverse proxy with rate limiting

#### ⚠️ **No Input Validation** (Beyond Pydantic)
- Pydantic validates request structure
- No additional prompt sanitization
- Planned: Input filtering for v1.1.0

### Audit Logging

**Execution Logging**:
```python
logger.info(
    "execute: starting execution=%s tenant=%s agents=%d tasks=%d",
    execution_id, tenant_id, len(agent_configs), len(task_configs)
)
```

**Recommended Log Retention**:
- Production: 30 days minimum
- Audit compliance: 90 days

### Dependency Security

**Automated Updates**:
- Dependabot enabled (GitHub)
- Security advisories monitored
- Critical updates: Deployed within 24 hours

**Dependency Pinning**:
```txt
# requirements.txt
fastapi>=0.115,<1.0  # Allow minor updates, not major
pydantic>=2.0,<3.0   # Pin major version
```

### Incident Response

**In Case of Security Incident**:
1. **Immediate**: Stop affected containers
2. **Assess**: Review logs, identify scope
3. **Patch**: Deploy security fix
4. **Notify**: Inform affected parties
5. **Document**: Post-mortem report

**Emergency Contacts**:
- Email: security@alteriom.net
- GitHub: [@Alteriom/security-team](https://github.com/orgs/Alteriom/teams/security-team)

---

## Security Roadmap

### v1.1.0 (Planned)
- ✅ API key authentication
- ✅ Rate limiting (per-client)
- ✅ Input sanitization
- ✅ Request signing (HMAC)

### v1.2.0 (Planned)
- ✅ JWT token support
- ✅ Role-based access control (RBAC)
- ✅ Audit trail export
- ✅ Security headers (CORS, CSP)

### v2.0.0 (Future)
- ✅ OAuth2 integration
- ✅ mTLS support
- ✅ Encryption at rest
- ✅ Security scan integration (Trivy)

---

## Compliance

### OWASP Top 10 Coverage

| Risk | Status | Mitigation |
|------|--------|------------|
| A01:2021 - Broken Access Control | ⚠️ Partial | Behind firewall (v1.0), API auth planned (v1.1) |
| A02:2021 - Cryptographic Failures | ✅ Low Risk | No sensitive data stored |
| A03:2021 - Injection | ✅ Protected | Pydantic validation, no direct SQL |
| A04:2021 - Insecure Design | ✅ Good | Isolated execution, non-root user |
| A05:2021 - Security Misconfiguration | ⚠️ Partial | Secure defaults, but no auth |
| A06:2021 - Vulnerable Components | ✅ Good | Dependabot enabled |
| A07:2021 - Authentication Failures | ⚠️ Not Implemented | Planned for v1.1 |
| A08:2021 - Software/Data Integrity | ✅ Good | Signed images (GHCR) |
| A09:2021 - Logging Failures | ✅ Good | Structured logging |
| A10:2021 - SSRF | ⚠️ Low Risk | Internal network only |

---

## License

This security policy is part of the crew-runner project and is subject to the same license terms.

**Last Updated**: April 14, 2026
