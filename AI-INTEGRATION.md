# AI Integration — GitHub Repo Library

There are two supported ways to use this self-hosted GitHub catalog with an AI assistant:

1. **REST API** for an AI application or workflow that can send authenticated HTTP requests.
2. **Optional local MCP server** for an MCP-compatible desktop or IDE agent that can run Python subprocesses through stdio.

The library does **not** install, execute, or validate third-party GitHub repositories automatically.

## 1. REST API

The web application exposes read-only JSON endpoints under `/api/ai/`, secured with `AI_API_KEY`. Other web and management endpoints **are not** protected by this key.

Generate an API key:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(36))'
```

Set the result in `.env` and start Docker Compose:

```env
AI_API_KEY=replace_with_your_random_key
```

```bash
docker compose up -d
```

Example request on the server:

```bash
curl -fsS -H "X-API-Key: YOUR_API_KEY" \
  'http://127.0.0.1:8765/api/ai/search?q=web&category=Web%20Design'
```

For PowerShell:

```powershell
$headers = @{ "X-API-Key" = "YOUR_API_KEY" }
Invoke-RestMethod -Uri "http://localhost:8765/api/ai/categories" -Headers $headers
```

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/ai/capabilities` | List supported AI endpoints |
| GET | `/api/ai/search?q=frontend&category=Web%20Design&list_id=1` | Search local repositories |
| GET | `/api/ai/categories` | List built-in and custom categories |
| GET | `/api/ai/lists` | List personal collections |
| GET | `/api/ai/repos/12345` | Repository details, GitHub URL, safe clone command |
| GET | `/api/ai/repos/12345/readme` | Read a saved repository's README from GitHub |
| GET | `/api/ai/repos/12345/file?path=src%2Fmain.py` | Read a small text file from GitHub (up to 200 KB) |

**Do not expose the API key over unencrypted public HTTP.** Use a protected LAN, VPN, or authenticated HTTPS reverse proxy. The application does not implement user accounts or separate API permissions for the management routes.

## 2. Optional local MCP server

The `mcp_server.py` module uses an optional Python package. It is **not** installed in the default Docker image.

Install on the computer where your MCP-capable client will launch it:

```bash
python3 -m pip install -r requirements-mcp.txt
```

An example configuration for clients that use the `mcpServers` configuration shape:

```json
{
  "mcpServers": {
    "github-repo-library": {
      "command": "python3",
      "args": ["/absolute/path/to/github-repo-library/mcp_server.py"],
      "env": {
        "DATA_DIR": "/absolute/path/to/github-repo-library/data"
      }
    }
  }
}
```

Adjust paths and configuration for your MCP client. On Windows, replace `python3` with the appropriate Python executable. The MCP server reads the locally available SQLite database directly; the example is **not** a remote MCP service. If your AI client runs on a different machine, it cannot directly access the Ubuntu machine's database without an appropriately secured shared or remote connection. For remote access, the protected REST API is usually simpler.

MCP tools include:

| Tool | Description |
| --- | --- |
| `search_repositories` | Search saved repositories using keywords, categories, or lists |
| `list_repository_categories` | List categories for filtering |
| `list_collections` | List personal collections |
| `get_repository` | Return repository details and the Git clone command |
| `read_repository_readme` | Read the README of a saved repository |
| `read_repository_file` | Read a selected small text source file |
| `save_github_link` | Import a public GitHub URL, optionally adding it to a collection |
| `create_collection` | Create a personal list |
| `put_repository_in_collection` | Add a repository to a personal list |

Example agent request:

> Search my GitHub Repo Library for a useful open-source web design tool, show the repository description, read its README, and provide the clone command. Do not run any commands without my approval.

### MCP safety

- Only connect MCP to an AI client you trust. These tools can change personal lists and import public repositories.
- Remote GitHub README files and source files are untrusted input and may contain misleading instructions.
- The MCP integration returns metadata and text; it does not automatically execute or install retrieved GitHub code.

## Important limitations

- GitHub imports, README requests, and source-file requests require access to GitHub and are subject to its API limits.
- The simple URL importer targets public repositories; private repositories need a sufficiently scoped token and careful handling of private data.
- Instagram links are not directly parsed. Paste the GitHub URL referenced by the post.
- Built-in categories use English labels such as `Robotics`, `Web Design`, and `AI & Machine Learning`. User-created category names are not automatically translated.
