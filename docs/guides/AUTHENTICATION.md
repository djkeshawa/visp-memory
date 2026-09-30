# Accounts and tokens

For same-user maintenance and project-scoped clients, see the [shared server guide](SHARED_SERVER.md).

Authentication is enabled by default. The dashboard sends unauthenticated users
to sign-in; API data requires credentials. Public images contain no default password.
The one exception is [open local-owner mode](#open-local-owner-mode), which a
loopback `serve` enters when no credentials are configured.

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

Administrators manage further accounts on the **Users** page. Passwords are stored
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
On authenticated deployments, dreaming endpoints require administrator access
and the `admin` token scope. Workflow reports need `intent:write` and access to
the target intent/project.

## Open local-owner mode

`visp-memory serve` enters open local-owner mode when authentication is enabled
(the default), it binds a loopback host (`127.0.0.1`, `localhost` or `::1`), and
no credentials are configured: no JWT secret, no API keys (`VISP_MEMORY_SERVER_API_KEYS`, `VISP_MEMORY_API_KEY` or
`storage.api_key`), and the anonymous flag is off. It prints a warning, enables
anonymous access and sets `VISP_MEMORY_SERVER_LOCAL_OWNER_MODE`. Binding a
non-loopback host with no credentials is refused. The peer address is checked on
every request, so setting the mode by hand on a public bind grants nothing.

In this mode every request from the loopback interface is the local owner. The
owner passes every repository check without a token: any local process that can
reach the port can read or write any project by naming its `repo_id`. Repository
scoping in [shared server](SHARED_SERVER.md) setups is a client convention, not
access control. The server also rejects non-loopback `Host` headers in this mode
to defend against DNS rebinding. This is a single-user, same-machine assumption;
configure accounts, tokens or API keys if it does not hold.

For a server that is not in open mode, clients send credentials from
`VISP_MEMORY_API_KEY`, `VISP_MEMORY_JWT_TOKEN` or `storage.api_key`.

## Owner maintenance on a local server

Only in open local-owner mode, `visp-memory serve` writes a random maintenance
token to `~/.visp-memory/run/owner-<port>.token` with owner-only permissions on
POSIX systems, and a discovery record `~/.visp-memory/run/server-<port>.json`.
A server with credentials configured writes neither file, so `connect` finds no
record there and falls back to `http://127.0.0.1:8000`; pass `--server-url` for
any other address. On Windows, the files live under the current user's profile.
Local clients such as `RemoteStorage` can read the token file as the same OS user
and send it in the `X-Visp-Owner-Token` header. The server removes both files on
clean shutdown.

The token unlocks the maintenance routes and nothing else:

- memory purge and repository archive, restore and purge (with their previews)
- retention preview and execution, and the consistency check
- embedding-index status and reindex
- dreaming
- graph import
- the audit log

It also authorizes workflow-status reports
([workflow reports](WORKFLOW_REPORTS.md)). It does not make the caller an
administrator and unlocks no account, team or provider settings.

Loopback alone does not prove ownership for these maintenance routes, because other
OS users on the machine can connect to `127.0.0.1` too; the token file is what
proves same-user access. (For ordinary reads and writes, loopback is enough in
this mode, as described above.)

The dashboard runs in a browser and cannot read this protected file or send its
token, so dashboard maintenance still requires an administrator account.

The same token is required to report an intent's workflow status
(`POST /intents/{id}/workflow-status`) as the local owner; an anonymous loopback
caller without it is refused.

In local-owner mode the server also refuses requests whose `Host` is not
`localhost`, `127.0.0.1` or `[::1]` (HTTP 421), and refuses a cross-origin
`POST`/`PUT`/`PATCH`/`DELETE` whose `Origin` is not a loopback page that is this
server's own dashboard or a configured CORS origin (HTTP 403). This stops a
web page that rebinds its hostname to `127.0.0.1` from reading or changing your
memories. Clients that send no `Origin` (the CLI, `RemoteStorage`, MCP) are not
affected, and neither are servers that are not in local-owner mode.

## Server deployment

Persist the complete storage directory, including accounts and sessions. Set
`VISP_MEMORY_STORAGE_DATA_DIR` when choosing a custom location. Follow the
[backup procedure](STORAGE.md#backup-and-schema-upgrades) before upgrades.

For access beyond localhost, use HTTPS, enable secure session cookies with
`VISP_MEMORY_SERVER_SESSION_COOKIE_SECURE=true`, and configure explicit client
origins through `VISP_MEMORY_SERVER_CORS_ORIGINS`. A wildcard origin disables
credentialed CORS responses. Team features remain [frozen](../reference/FEATURE_STATUS.md).

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
