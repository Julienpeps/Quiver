# Quiver MCP server

Quiver exposes a local Model Context Protocol server over stdio:

```sh
quiver mcp
```

For local clients that need a long-lived HTTP server, Streamable HTTP is also
available and binds to localhost by default:

```sh
quiver mcp --transport streamable-http --port 8000
```

MCP hosts should launch it as a child process and pass the same global options
used by Quiver when needed, for example:

```sh
quiver --root "$HOME/.quiver" mcp
```

The first implementation exposes typed workspace, service, image, diagnostic,
GUI, audit-log, and container-execution tools. Container execution is explicit:
workspaces must already be running, argv execution is the default, and shell
pipelines require `shell=true`. Interactive executions return an execution ID;
use `exec_read`, `exec_write`, `exec_resize`, `exec_signal`, and `exec_close` to
control them.

Destruction requires a short-lived token from `workspace_destroy_plan`.
Execution output is bounded and cursor-addressable. Sessions are process-bound
to the MCP server and are terminated when their workspace is stopped or
destroyed.
