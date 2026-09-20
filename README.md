# Quiver

Quiver is a Python CLI for disposable, assessment-scoped Docker penetration-testing environments with persistent host workspaces. Each assessment gets one ephemeral primary container (BlackArch-based), one persistent workspace under `~/.quiver/workspaces/<assessment>/`, a dedicated Docker network, optional full-tunnel OpenVPN/WireGuard with a fail-closed kill switch, and a loopback-only browser GUI (XFCE + TigerVNC + noVNC).

`quiver stop` removes the container; `quiver start` recreates it from the configured image and remounts the same workspace. The image is the source of truth for tooling, the workspace is the source of truth for assessment state.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Docker CLI with a local daemon running Linux containers (Linux, macOS, or Windows Docker Desktop)

Check the host with:

```bash
uv run quiver doctor
```

## Development setup

```bash
uv sync --all-groups
uv run quiver --help
uv run pytest            # unit + integration (integration skips without Docker)
uv run ruff check .
```

## Core workflow

```bash
# Create + start an assessment and enter a recorded root shell
quiver start acme-2026 --image ghcr.io/example/quiver-internal:stable

# Start without entering a shell
quiver start acme-2026 --image ghcr.io/example/quiver-internal:stable --detach

# With a VPN profile (copied into the workspace on first creation)
quiver start acme-2026 --image ... --vpn client.ovpn --vpn-credentials auth.txt --vpn-type auto

# Inspect
quiver list
quiver status acme-2026

# Shell / exec (recorded)
quiver shell acme-2026
quiver shell acme-2026 --asciinema
quiver exec acme-2026 -- nmap -sV 10.10.10.5

# Auditing
quiver logs acme-2026
quiver logs acme-2026 --session 20260920T142701Z-7f3a2c
quiver replay acme-2026 20260920T142701Z-7f3a2c

# GUI (loopback noVNC, per-assessment password)
quiver gui acme-2026

# Services (internal Supervisor services and Compose stacks)
quiver service list acme-2026
quiver service up acme-2026 bloodhound-ce
quiver service status acme-2026
quiver service logs acme-2026 bloodhound-ce -f

# Configuration
quiver edit acme-2026                       # opens $VISUAL/$EDITOR, validates on save
quiver edit acme-2026 --set docker.privileged=true
quiver edit acme-2026 --publish 8443:8443
quiver edit acme-2026 --vpn new-client.ovpn --restart

# Lifecycle
quiver stop acme-2026                      # removes the container, keeps the workspace
quiver destroy acme-2026                   # removes managed resources + workspace
quiver workspace fix-perms acme-2026       # best-effort host UID/GID normalization

# Images
quiver image list
quiver image pull internal
quiver image build internal --platform linux/arm64
```

Global options: `--context NAME` (per-invocation Docker context), `--root PATH` (override `~/.quiver`), `--verbose` (log Docker commands with credential redaction), `--no-color`.

Shell completion (Bash/Zsh/Fish/PowerShell): `quiver --install-completion`.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | success |
| 2 | usage or configuration validation error |
| 3 | Docker unavailable or runtime failure |
| 4 | image or platform failure |
| 5 | VPN failure |
| 6 | service failure |
| 7 | workspace/filesystem failure |

## Repository layout

```text
src/quiver/        CLI, config models, lifecycle, Docker backend, VPN, audit, services
images/            amd64/arm64 base Dockerfiles and shared container scripts
packages/          architecture-aware package manifests for image profiles
services/          built-in external service definitions (e.g. BloodHound CE)
tests/             unit tests + Docker integration tests
docs/              implementation specification
.github/workflows/ unit/integration tests, multi-arch image build, release publishing
```

The full v1 implementation specification is at [docs/implementation-spec.md](docs/implementation-spec.md).
