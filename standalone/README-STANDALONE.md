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

On first start, use the one-use setup link in the server output to create your
administrator account. Existing installations keep their accounts. There is no
shared default password.

Data defaults to `.visp-memory/data` under the current project unless configuration
selects another storage directory. Keep this directory when replacing the executable.

## Configuration and updates

The [installation guide](https://github.com/djkeshawa/visp-memory/blob/develop/docs/deployment/PACKAGING.md)
explains other distributions. Before upgrading, stop all writers and follow the
[backup procedure](https://github.com/djkeshawa/visp-memory/blob/develop/docs/development/STORAGE.md#backup-and-schema-upgrades).
The standalone bundle excludes local transformer models and the ArcadeDB/JVM runtime.
