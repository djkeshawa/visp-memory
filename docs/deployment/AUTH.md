# Accounts and tokens

Authentication is enabled by default. The dashboard sends unauthenticated users
to sign-in; API data requires credentials. Public images contain no default password.

## First-time browser setup

Start the server, then read its startup logs. If no accounts exist, they contain a
one-use `/dashboard/auth#setup=...` link. Open that path on your server to create
the first administrator, using a password of at least 12 characters.

For Docker, read the link with `docker logs visp-memory` (or your container name).
The setup code is removed from the browser address bar after loading and stops
working after the first account is created. Keep access to these logs private.
Existing installations continue using their existing accounts.

The account setup flow opens a search setup guide, also available from Settings.
Provider configuration is applied through server settings and a restart.

## Dashboard accounts

For an interactive terminal setup, use the same storage configuration as the server:

```bash
visp-memory admin create --username admin
```

For unattended setup, provide `VISP_MEMORY_BOOTSTRAP_ADMIN_USERNAME` and
`VISP_MEMORY_BOOTSTRAP_ADMIN_PASSWORD` through your deployment's secret settings.
The initial account is created only when the store has no accounts.

Administrators manage further accounts in **Users & Teams**. Passwords are stored
as Argon2id hashes. Browser sessions use HttpOnly cookies and hashed server-side
IDs, expire after 12 idle hours or 7 days, and require CSRF and origin checks for
changes. Passwords and access tokens are not stored in browser local storage.

## Personal access tokens

Create a token in **Integrations**, selecting the necessary projects, scopes,
and expiry. Copy it when shown; the server stores only its hash. Tokens can be
revoked from the same page.

```bash
curl http://127.0.0.1:8000/memories \
  -H "Authorization: Bearer $VISP_MEMORY_TOKEN"
```

Set `VISP_MEMORY_TOKEN` in your client environment to the issued token. The API
example uses it as a shell variable, not as a server configuration setting.
Dreaming endpoints require administrator access and the `admin` token scope;
workflow reports need `intent:write` and access to the target intent/project.

## Server deployment

Persist the complete storage directory, including accounts and sessions. Set
`VISP_MEMORY_STORAGE_DATA_DIR` when choosing a custom location. Follow the
[backup procedure](../development/STORAGE.md#backup-and-schema-upgrades) before upgrades.

For access beyond localhost, use HTTPS, enable secure session cookies with
`VISP_MEMORY_SERVER_SESSION_COOKIE_SECURE=true`, and configure explicit client
origins through `VISP_MEMORY_SERVER_CORS_ORIGINS`. A wildcard origin disables
credentialed CORS responses. Team features remain [frozen](../FEATURE_STATUS.md).

## Compatibility settings

New integrations should use scoped tokens. Existing deployments can still use:

| Mode | Server configuration | Client credential |
|---|---|---|
| API keys | `VISP_MEMORY_SERVER_API_KEYS` (comma-separated); legacy `VISP_MEMORY_API_KEY` | `X-API-KEY` header |
| JWT | `VISP_MEMORY_JWT_SECRET`; optional `VISP_MEMORY_SERVER_JWT_EXPIRY_HOURS` | Bearer JWT with `sub`; optional `username`, `team_id`, `is_admin` |
| Anonymous demo | `VISP_MEMORY_SERVER_ALLOW_ANONYMOUS=true` | Requests attributed to `anonymous` |
| Auth disabled | `VISP_MEMORY_SERVER_AUTH_ENABLED=false` | Requests run as local admin |

Anonymous and auth-disabled modes are for isolated local testing. They do not
provide the access protection expected of a shared server.
