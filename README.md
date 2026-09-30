<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.png">
    <img src="docs/assets/logo-light.png" alt="Quiver logo" width="300">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Julienpeps/Quiver/actions/workflows/test-cli.yml"><img src="https://github.com/Julienpeps/Quiver/actions/workflows/test-cli.yml/badge.svg" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
</p>

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
# Create + start an assessment and enter a recorded host-UID/GID shell
quiver start acme-2026 --image ghcr.io/example/quiver-internal:stable

# Start without entering a shell
quiver start acme-2026 --image ghcr.io/example/quiver-internal:stable --detach

# With a VPN profile (copied into the workspace on first creation)
quiver start acme-2026 --image ... --vpn client.ovpn --vpn-credentials auth.txt --vpn-type auto

# Add comma-separated Arch packages to this assessment's disposable container.
quiver start acme-2026 --packages nmap,jq --detach

# Inspect
quiver list
quiver status acme-2026

# Shell / exec (recorded)
# Shells run as your host UID/GID and use Fish by default.
# Use sudo without a password when a command requires container root.
quiver shell acme-2026
quiver shell acme-2026 --asciinema
quiver exec acme-2026 -- nmap -sV 10.10.10.5

# Auditing
quiver logs acme-2026
quiver logs acme-2026 --session 20260920T142701Z-7f3a2c
quiver replay acme-2026 20260920T142701Z-7f3a2c

# GUI (loopback-only noVNC; opens the host default browser)
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
quiver edit acme-2026 --packages ripgrep,fd --restart
quiver edit acme-2026 --vpn new-client.ovpn --restart

# Host dotfiles: place Fish, tmux, Starship, Neovim, etc. configuration here.
# This directory is mounted read-only as ~/.config in interactive shells.
mkdir -p ~/.quiver/dotfiles

# Lifecycle
quiver stop acme-2026                      # removes the container, keeps the workspace
quiver destroy acme-2026                   # removes managed resources + workspace
quiver workspace fix-perms acme-2026       # best-effort host UID/GID normalization

# Images
quiver image list
quiver image pull internal
quiver image build internal --platform linux/arm64
quiver image build external --platform linux/arm64  # external recon and OSINT tooling
quiver image build cloud --platform linux/arm64     # AWS, Azure, GCP, Kubernetes tooling
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

- [User guide](docs/user-guide.md): verified local builds, image selection, assessment operations, VPN, GUI, services, audit, troubleshooting, and publishing.
- [Implementation specification](docs/implementation-spec.md): full v1 design and acceptance criteria.
- [Research notes](docs/research/): AUR/ARM64, desktop/VNC, and package-validation assessments that inform image decisions.

## License

MIT — see [LICENSE](LICENSE).
