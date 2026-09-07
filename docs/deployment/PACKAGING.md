# Installation

| Distribution | Requirements | Includes |
|---|---|---|
| Published Docker image | Docker; Linux amd64 runtime | CLI, API, dashboard, MCP |
| Python package | Python 3.10+ | CLI; add `api` and `mcp` extras as needed; release wheels bundle the dashboard |
| Standalone archive | Matching OS/architecture | CLI, API, dashboard; no MCP |

Download versions and checksums from [GitHub Releases](https://github.com/djkeshawa/visp-memory/releases).

## Published Docker image

```bash
docker run -d --name visp-memory \
  -p 127.0.0.1:8000:8000 \
  -v visp-memory-data:/data \
  -e VISP_MEMORY_EMBEDDING_PROVIDER=noop \
  ghcr.io/djkeshawa/visp-memory:0.7.0
```

Open [the dashboard](http://127.0.0.1:8000/dashboard). Read `docker logs visp-memory`
for the first administrator setup link, then follow [account setup](AUTH.md).
This example uses keyword search. [Storage and embeddings](../development/STORAGE.md)
explains how to enable semantic search.

The named volume persists data across container replacement. The image runs as
UID 1000; a bind-mounted `/data` directory must be writable by that user. The
`/readyz` endpoint checks storage and dashboard assets, not embedding connectivity.

### Stop and upgrade

`docker stop visp-memory` stops the server without deleting its data. Before an
upgrade, stop all writers and [back up the store](../development/STORAGE.md#backup-and-schema-upgrades).
Pull the chosen version, then recreate the container with the **same volume and
configuration**. Keep the previous image and backup until the new version is verified.
Do not remove the data volume during a routine restart or upgrade.

## Docker Compose from source

From a checkout, optionally copy `.env.example` to `.env` if no local `.env`
exists. A cloud key or bootstrap password is not required for first-run setup.

```bash
docker compose --profile lite up -d --build --wait
docker compose --profile lite logs --tail=100
```

The `lite` profile uses SQLite and a persistent named volume. It binds to
`127.0.0.1:8000`; change `VISP_MEMORY_PORT` for another local port. Preserve any
existing `docker-compose.override.yml`, especially custom volume mappings.
Use the same profile and file arguments for `ps`, `logs`, `stop`, and later `up` commands.

### Local Ollama service

```bash
docker compose -f docker-compose.yml -f docker-compose.ollama.yml \
  --profile lite up -d --build --wait --wait-timeout 600
```

The overlay starts Ollama, downloads `nomic-embed-text` by default, and waits for
the pull before starting the app. Set `VISP_MEMORY_OLLAMA_EMBEDDING_MODEL` to choose
another model. Models persist in `ollama-data`; no Ollama host port is published.
Initial downloads require network access. Inspect `logs ollama-pull` if they fail.

Explicit `-f` arguments disable automatic override loading. If you use a custom
volume override, insert `-f docker-compose.override.yml` between the base file
and Ollama overlay. Omitting it can open a different, empty data volume.

A provider alone does not create a vector index. For SQLite semantic search,
build with the `chroma` extra in `VISP_MEMORY_EXTRAS` as well as the provider and
`api,mcp` extras. The published image excludes Chroma and local transformer models.
Changing a model also requires compatible vectors for existing memories.

The optional `arcadedb` profile is [frozen](../FEATURE_STATUS.md).

### Neo4j beta

The separate Compose file keeps Neo4j optional and leaves the SQLite deployment intact.
Available starting with 0.7.0. Build from this checkout or set
`VISP_MEMORY_IMAGE=ghcr.io/djkeshawa/visp-memory:0.7.0` and omit `--build`.
Set `NEO4J_PASSWORD` to a unique database password in your local environment, then run:

```bash
docker compose -f docker-compose.neo4j.yml --profile neo4j up -d --build --wait
```

The application uses the same login/bootstrap flow as the SQLite service and binds to
localhost. Neo4j is reachable only on the private container network. Database failures
do not fall back to SQLite. To evaluate it alongside an app already using port 8000,
set `VISP_MEMORY_PORT=8001` for this command.

Two named volumes persist the graph and application accounts/journals. Keep both for
a complete backup. For graph backup or legacy migration, stop only the application
service, leave Neo4j running, and run the [administration commands](../development/STORAGE.md#backup-restore-and-legacy-migration)
inside a one-off application container. For example:

```bash
docker compose -f docker-compose.neo4j.yml stop visp-memory-neo4j
docker compose -f docker-compose.neo4j.yml run --rm --no-deps visp-memory-neo4j \
  python -m visp_memory.core.neo4j_admin backup /data/graph-backup.json
docker compose -f docker-compose.neo4j.yml --profile neo4j up -d --wait
```

Copy backups off the data volume as part of your normal backup process.

## Python package

```bash
pip install "visp-memory[mcp,capture]"
# Add the API and dashboard:
pip install "visp-memory[api,mcp]"
visp-memory serve
```

The dashboard is at `/dashboard`, and generated API documentation is at `/docs`.
Use [MCP and hooks](../development/MCP.md) to connect an assistant. Uninstalling
with `pip uninstall visp-memory` leaves project configuration and stored data intact.

## Standalone downloads

Extract the archive for your OS and architecture, then run `start-server.sh`
(Linux/macOS) or `start-server.ps1` (Windows). Follow the included
[standalone instructions](../../standalone/README-STANDALONE.md). These bundles
exclude MCP, local transformer models, and the ArcadeDB/JVM runtime.

## Build and troubleshoot

Source builds need Node/npm as well as Python:

```bash
pip install -e ".[api,mcp,capture,dev]"
python3 build_frontend.py
python3 -m build
```

Build the frontend first so the wheel includes dashboard assets. For a standalone
build, use `./build_standalone.sh` on the target platform. Maintainers should follow
[Releasing](RELEASING.md) for checks and publication.

| Symptom | Next step |
|---|---|
| Dashboard returns 404 | Use a release wheel or rebuild frontend assets before packaging |
| Existing installation looks empty | Check the data directory, named volume, and Compose overrides |
| Only keyword results appear | Check both the embedding provider and Chroma availability |
| Login fails after an upgrade | Confirm the original data volume and account database are mounted |
| API inaccessible from another machine | The default binding is localhost; configure authentication and HTTPS before exposing it |
