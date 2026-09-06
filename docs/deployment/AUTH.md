# Server Authentication

The FastAPI server protects memory, intent, repository, team, and relationship
routes by default. The packaged dashboard shell can be loaded without
credentials, but its data and all detailed API routes require authentication.

## Dashboard Accounts

There is no public signup. Bootstrap the first administrator once:

```bash
export VISP_MEMORY_BOOTSTRAP_ADMIN_USERNAME=admin
export VISP_MEMORY_BOOTSTRAP_ADMIN_PASSWORD="replace-with-a-strong-password"
visp-memory serve
```

Alternatively, create an administrator interactively:

```bash
visp-memory admin create --username admin
```

Open `/dashboard/auth` and sign in with the account. The server stores only an
Argon2id password hash. Browser sessions use opaque, hashed server-side IDs in
HttpOnly cookies, expire after 12 idle hours or 7 days, and require CSRF and
origin checks for changes. Administrators can create and disable more accounts
from Users & Teams in the sidebar.

## Personal Access Tokens

Create API and MCP credentials from Dashboard > Integrations. Tokens begin
with `llmm_`, are displayed once, and are stored only as hashes. Select the
minimum required project access, scopes, and expiry, then send the token as a
Bearer credential:

```bash
curl http://127.0.0.1:8000/memories \
  -H "Authorization: Bearer llmm_your_token"
```

Tokens can be revoked immediately from the same page. The dashboard never
stores account passwords or tokens in local storage.

## Local Development

For single-user local testing, disable auth:

```bash
VISP_MEMORY_EMBEDDING_PROVIDER=noop \
VISP_MEMORY_SERVER_AUTH_ENABLED=false \
visp-memory serve
```

This makes API calls run as a local admin user. Do not use this setting for a
shared server.

## Legacy API Key Mode

For simple server-to-client setups, configure one or more API keys:

```bash
export VISP_MEMORY_SERVER_API_KEYS="dev-key,ci-key"
visp-memory serve
```

Send the key with requests:

```bash
curl http://127.0.0.1:8000/memories \
  -H "X-API-KEY: dev-key"
```

`VISP_MEMORY_API_KEY` is also accepted for backward-compatible single-key setups.

## Legacy JWT Mode

JWT mode requires a signing secret:

```bash
export VISP_MEMORY_JWT_SECRET="replace-with-a-long-random-secret"
export VISP_MEMORY_SERVER_JWT_EXPIRY_HOURS=24
visp-memory serve
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
export VISP_MEMORY_SERVER_ALLOW_ANONYMOUS=true
export VISP_MEMORY_SERVER_DEFAULT_TEAM=platform
visp-memory serve
```

Anonymous requests are attributed to `anonymous`. This is not recommended for
shared or internet-accessible deployments.

## CORS

Configure dashboard/client origins with a comma-separated list:

```bash
export VISP_MEMORY_SERVER_CORS_ORIGINS="https://memory.example.com,http://localhost:8000"
```

If `*` is used as an origin, credentialed CORS responses are disabled
automatically.

## Storage For Team Deployments

For a shared server, use Neo4j or a persistent SQLite volume:

```bash
export VISP_MEMORY_STORAGE_DATA_DIR=/data/visp-memory
```

For Neo4j:

```bash
export VISP_MEMORY_STORAGE_BACKEND=neo4j
export NEO4J_URI=bolt://neo4j:7687
export NEO4J_USER=neo4j
export NEO4J_PASSWORD=replace-me
```

## Security Notes

- Do not run with `VISP_MEMORY_SERVER_AUTH_ENABLED=false` outside local
  development.
- Prefer scoped personal access tokens over compatibility API keys and JWTs.
- Use long random values for bootstrap passwords, API keys, and JWT secrets.
- Treat memory content as potentially sensitive project data.
- Avoid broad CORS origins on shared deployments.
- Back up the storage directory or Neo4j database before upgrades.

## First-time browser setup

When authentication is enabled and no accounts exist, server startup logs contain a
one-use `/dashboard/auth#setup=...` link. Open it on that server to create the first
administrator with a username and a password of at least 12 characters. The code is
kept in the URL fragment, removed from the browser address bar after loading, and is
never returned by the public authentication status endpoint. Only someone with local
operator access to the startup logs should receive the link.

The code stops working after the first account is created, including an account created
through the existing bootstrap/CLI route. Existing installations retain their accounts
and login flow. Public images do not contain a default administrator password.

After creating the account, the dashboard opens a search setup guide. It distinguishes
keyword fallback from an available semantic index and explains provider configuration.
The guide is also available from Settings. Provider changes are applied through server
environment settings and a restart; the browser does not store provider credentials.
