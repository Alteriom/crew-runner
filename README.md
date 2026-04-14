# Crew Runner

**CrewAI execution engine for Alteriom Command Center**

Crew Runner is a standalone microservice that executes CrewAI workflows on behalf of the Alteriom Command Center backend. It provides a simple HTTP API for submitting crew execution requests and retrieving results.

---

## Features

- ✅ **FastAPI HTTP API** - Simple REST interface for crew execution
- ✅ **LiteLLM Integration** - Unified API for multiple LLM providers (OpenAI, Anthropic, etc.)
- ✅ **Worker Registration** - Auto-registers with backend on startup
- ✅ **Health Monitoring** - Built-in health checks and heartbeat system
- ✅ **Version Management** - Semantic versioning from VERSION file
- ✅ **Docker Support** - Production-ready containerized deployment
- ✅ **Zero Zombie Processes** - Pure Python/FastAPI (no subprocess spawning)

---

## Quick Start

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export LITELLM_API_KEY=sk-...
export BACKEND_URL=http://localhost:3007
export OLLAMA_BASE_URL=http://localhost:11434

# Run server
python src/main.py
```

Server runs on `http://localhost:8081`

### Docker

```bash
# Build image
docker build -t crew-runner:latest .

# Run container
docker run -d \
  --name crew-runner \
  -p 8081:8081 \
  -e LITELLM_API_KEY=sk-... \
  -e BACKEND_URL=http://backend:3007 \
  -e OLLAMA_BASE_URL=http://ollama:11434 \
  crew-runner:latest
```

### Docker Compose

```yaml
version: '3.8'
services:
  crew-runner:
    image: ghcr.io/alteriom/crew-runner:1.0.0
    ports:
      - "127.0.0.1:8081:8081"
    environment:
      - LITELLM_API_KEY=${LITELLM_API_KEY}
      - BACKEND_URL=http://backend:3007
      - OLLAMA_BASE_URL=http://ollama:11434
      - MAX_SESSIONS=2
      - WORKER_HEARTBEAT_INTERVAL=60
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8081/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

---

## API Endpoints

### GET `/health`
Health check endpoint

**Response**:
```json
{
  "status": "ok",
  "version": "1.0.0",
  "active_sessions": 0,
  "max_sessions": 2,
  "at_capacity": false
}
```

### GET `/version`
Version information

**Response**:
```json
{
  "version": "1.0.0",
  "service": "crew-runner"
}
```

### POST `/execute`
Execute a CrewAI workflow

**Request**:
```json
{
  "prompt": "Analyze this data...",
  "system_context": "You are a data analyst",
  "execution_id": "exec-123",
  "tenant_id": "tenant-456",
  "inputs": {},
  "timeout_seconds": 300
}
```

**Response**:
```json
{
  "result": "Analysis complete...",
  "execution_id": "exec-123",
  "status": "completed"
}
```

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LITELLM_API_KEY` | Yes | - | LiteLLM API key for LLM access |
| `BACKEND_URL` | Yes | - | Command Center backend URL |
| `OLLAMA_BASE_URL` | No | `http://localhost:11434` | Ollama server URL |
| `MAX_SESSIONS` | No | `2` | Maximum concurrent executions |
| `WORKER_HEARTBEAT_INTERVAL` | No | `60` | Heartbeat interval (seconds) |
| `DEBUG` | No | `false` | Enable debug logging |

---

## Integration with Command Center

Crew Runner integrates with the Alteriom Command Center backend:

1. **Worker Registration**: On startup, crew-runner registers itself with the backend
2. **Execution Requests**: Backend sends execution requests via POST `/execute`
3. **Heartbeat**: crew-runner sends periodic heartbeats to backend
4. **Results**: Execution results are returned in the response

### Backend Compatibility

| crew-runner | Command Center Backend |
|-------------|------------------------|
| 1.0.x       | >= 1.5.0              |

---

## Development

### Running Tests

```bash
# Set test environment
export CREW_RUNNER_URL=http://localhost:8081
export BACKEND_URL=http://localhost:3007

# Run test suite
./scripts/test-config.sh
```

### Project Structure

```
crew-runner/
├── src/
│   ├── main.py                    # FastAPI application
│   └── llm_output_normalizer.py   # LLM output processing
├── tests/                         # Unit tests
├── scripts/
│   └── test-config.sh            # Integration test script
├── .github/workflows/
│   └── ci.yml                    # GitHub Actions CI/CD
├── Dockerfile                     # Production container
├── docker-compose.yml            # Local dev environment
├── requirements.txt              # Python dependencies
├── VERSION                       # Semantic version
├── SECURITY.md                   # Security policy
└── README.md                     # This file
```

### Making Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Run tests locally
5. Commit your changes (`git commit -am 'Add new feature'`)
6. Push to the branch (`git push origin feature/my-feature`)
7. Create a Pull Request

---

## Deployment

### GitHub Container Registry

Images are automatically built and published to GHCR on every push to `main`:

```bash
# Pull latest image
docker pull ghcr.io/alteriom/crew-runner:latest

# Pull specific version
docker pull ghcr.io/alteriom/crew-runner:1.0.0
```

### Production Deployment

1. Pull the desired version from GHCR
2. Update `docker-compose.prod.yml` with the version tag
3. Deploy:
   ```bash
   docker compose -f docker-compose.prod.yml up -d crew-runner
   ```

### Version Pinning

Always pin versions in production:

```yaml
services:
  crew-runner:
    image: ghcr.io/alteriom/crew-runner:1.0.0  # ✅ Pinned
    # NOT: ghcr.io/alteriom/crew-runner:latest  # ❌ Unpredictable
```

---

## Security

### Current Status (v1.0.0)

⚠️ **No authentication implemented**

Crew-runner v1.0.0 should only be deployed:
- Behind a firewall
- On internal networks only
- With access restricted to trusted backend services

### Planned (v1.1.0)

- ✅ API key authentication
- ✅ Rate limiting
- ✅ Input sanitization
- ✅ Request signing (HMAC)

See [SECURITY.md](SECURITY.md) for full security policy.

---

## Monitoring

### Health Checks

```bash
# Check service health
curl http://localhost:8081/health

# Check version
curl http://localhost:8081/version
```

### Logs

```bash
# Docker logs
docker logs crew-runner --tail 100 --follow

# Check for errors
docker logs crew-runner 2>&1 | grep -i error
```

### Metrics

Crew-runner logs execution metrics:
- Execution ID
- Duration
- Status (success/failure)
- Error messages (if any)

---

## Troubleshooting

### Connection Refused

**Problem**: `Connection refused` on port 8081  
**Solution**: Check if crew-runner is running and ports are correctly mapped

```bash
docker ps | grep crew-runner
netstat -tuln | grep 8081
```

### Backend Registration Failed

**Problem**: crew-runner can't register with backend  
**Solution**: Verify `BACKEND_URL` is correct and backend is reachable

```bash
curl $BACKEND_URL/health
```

### Execution Timeouts

**Problem**: Crews timeout or hang  
**Solution**: Increase `timeout_seconds` in execution requests or check LiteLLM API key

---

## License

MIT License - see [LICENSE](LICENSE) for details

---

## Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

---

## Support

- **Issues**: [GitHub Issues](https://github.com/Alteriom/crew-runner/issues)
- **Documentation**: [GitHub Wiki](https://github.com/Alteriom/crew-runner/wiki)
- **Email**: support@alteriom.net

---

**Version**: 1.0.0  
**Last Updated**: April 14, 2026
