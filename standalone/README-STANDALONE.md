# Visp Memory Standalone

The standalone archive contains the Visp Memory CLI, API server, and embedded
dashboard. It does not contain the MCP server. Install the Python package with
the `mcp` extra, or use the container image, when an MCP transport is required.

## Start the server

- Linux/macOS: `./start-server.sh`
- Windows PowerShell: `./start-server.ps1`

The dashboard is available at <http://localhost:8000/dashboard> and the API
documentation at <http://localhost:8000/docs>.

Run `visp-memory --help` (`./visp-memory` on Linux/macOS and
`./visp-memory.exe` on Windows) for CLI commands.

Data is stored in `.visp-memory` under the current project unless configuration
selects another storage directory.

## Capability matrix

| Distribution | CLI | API | Dashboard | MCP |
| --- | --- | --- | --- | --- |
| Standalone archive | Yes | Yes | Yes | No |
| Python package with `[api,mcp]` | Yes | Yes | Release wheel | Yes |
| Container image | Yes | Yes | Yes | Yes |

See the project README and `docs/deployment/PACKAGING.md` for configuration and
backend details.
