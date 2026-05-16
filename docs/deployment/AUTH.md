# Server Authentication

The FastAPI server protects memory, intent, repository, team, and relationship
routes by default. The root status endpoint and packaged dashboard shell can be
loaded without credentials, but API calls from clients need authentication
unless local development mode is explicitly enabled.

## Local Development

For single-user local testing, disable auth:

```bash
LLM_MEMORY_EMBEDDING_PROVIDER=noop \
LLM_MEMORY_SERVER_AUTH_ENABLED=false \
llm-memory serve
```

This makes API calls run as a local admin user. Do not use this setting for a
shared server.

## API Key Mode

For simple server-to-client setups, configure one or more API keys:

```bash
export LLM_MEMORY_SERVER_API_KEYS="dev-key,ci-key"
llm-memory serve
```

Send the key with requests:

```bash
curl http://127.0.0.1:8000/memories \
  -H "X-API-KEY: dev-key"
```

`LLM_MEMORY_API_KEY` is also accepted for backward-compatible single-key setups.

## JWT Mode

JWT mode requires a signing secret:

```bash
export LLM_MEMORY_JWT_SECRET="replace-with-a-long-random-secret"
export LLM_MEMORY_SERVER_JWT_EXPIRY_HOURS=24
llm-memory serve
```

Clients send:

```bash
curl http://127.0.0.1:8000/memories \
  -H "Authorization: Bearer <token>"
```

Tokens must include a `sub` claim. Optional claims include `username`,
`team_id`, and `is_admin`.

## Anonymous Mode

Anonymous mode is available for trusted internal demos:

```bash
export LLM_MEMORY_SERVER_ALLOW_ANONYMOUS=true
export LLM_MEMORY_SERVER_DEFAULT_TEAM=platform
llm-memory serve
```

Anonymous requests are attributed to `anonymous`. This is not recommended for
shared or internet-accessible deployments.

## CORS

Configure dashboard/client origins with a comma-separated list:

```bash
export LLM_MEMORY_SERVER_CORS_ORIGINS="https://memory.example.com,http://localhost:8000"
```

If `*` is used as an origin, credentialed CORS responses are disabled
automatically.

## Storage For Team Deployments

For a shared server, use Neo4j or a persistent SQLite volume:

```bash
export LLM_MEMORY_STORAGE_DATA_DIR=/data/llm-memory
```

For Neo4j:

```bash
export LLM_MEMORY_STORAGE_BACKEND=neo4j
export NEO4J_URI=bolt://neo4j:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=replace-me
```

## Security Notes

- Do not run with `LLM_MEMORY_SERVER_AUTH_ENABLED=false` outside local
  development.
- Use long random values for API keys and JWT secrets.
- Treat memory content as potentially sensitive project data.
- Avoid broad CORS origins on shared deployments.
- Back up the storage directory or Neo4j database before upgrades.
