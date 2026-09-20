# Quiver — Implementation Specification

**Status:** v1 implementation specification  
**Date:** 20 September 2026  
**Project name:** `Quiver`  
**Target:** personal penetration-testing container environment replacing an Exegol-style workflow

## 1. Executive summary

Quiver is a Python CLI that creates disposable, assessment-scoped Docker environments while keeping all assessment state on the host. An assessment has one primary pentest container, one persistent workspace, one validated YAML configuration, one dedicated Docker network by default, optional full-tunnel OpenVPN/WireGuard connectivity, a browser-accessible local GUI, terminal/session auditing, and optional auxiliary service stacks such as BloodHound CE.

The primary container is intentionally ephemeral. `quiver stop` stops and removes it; `quiver start` recreates it from the configured image and remounts the same workspace. This makes the image the source of truth for tooling and the workspace the source of truth for assessment state.

The recommended v1 architecture is:

```text
Host (Linux / macOS / Windows)
|
|-- quiver Python CLI
|    |-- config + validation
|    |-- Docker CLI adapter
|    |-- lifecycle manager
|    |-- service manager
|    |-- browser launcher
|    `-- diagnostics / doctor
|
`-- ~/.quiver/workspaces/<assessment>/
     |-- .quiver.yaml
     |-- .logs/
     |-- .vpn/
     |-- .services/              # implementation-owned service state
     `-- <assessment files>

Docker daemon
|
|-- quiver-<assessment> network
|    |-- quiver-<assessment>      # disposable primary container
|    `-- optional service stacks # e.g. BloodHound CE
|
`-- registry images
     |-- quiver-base
     |-- quiver-web
     |-- quiver-internal
     `-- future profiles
```

Key implementation choices are:

- Docker is the only runtime supported in v1. The CLI uses the installed Docker CLI as its compatibility boundary rather than reimplementing Docker context, credential-helper, Buildx, and Compose behavior.
- BlackArch is the tool ecosystem, but image construction is owned by Quiver. amd64 and arm64 use architecture-specific roots that converge into common provisioning layers.
- The default network is a dedicated per-assessment user-defined bridge. Host mode is supported as an explicit option, with platform limitations and a hard safety check against starting the in-container VPN on native Linux host networking.
- VPN connectivity is an in-container supervised service. A kill switch is applied before tunnel establishment and remains closed if the VPN process dies.
- The GUI uses XFCE + TigerVNC + noVNC/Websockify. This is preferred over direct X11, RDP, Guacamole, and KasmVNC for v1 because it is browser-based, supports clipboard and dynamic resize, and has straightforward x86_64/aarch64 availability in the Arch ecosystems.
- In-container long-running services are managed by Supervisor. Multi-container applications are managed by generated Docker Compose projects and attached to the assessment network.
- Interactive terminal sessions are audited with command metadata plus terminal output/timing. `script(1)` is the default recorder; asciinema is an on-demand recording backend.

## 2. Goals and non-goals

### 2.1 Goals

Quiver v1 MUST provide all of the following:

1. A single Python CLI for assessment lifecycle management: start/create, stop, destroy, edit, status, shell, GUI, services, logs, and images.
2. A one-assessment-to-one-primary-container model.
3. Disposable primary containers with persistent host-mounted workspaces.
4. Per-assessment persisted configuration.
5. Docker support on Linux, macOS, and Windows using Linux containers.
6. amd64 and arm64 image publication where the selected profile's packages are available.
7. Root as the default user inside the primary container.
8. Configurable Docker privileged mode, capabilities, devices, network mode, sysctls, environment, and published ports.
9. Full-tunnel OpenVPN and WireGuard as supervised services, with fail-closed behavior on tunnel failure.
10. Browser-local graphical desktop with clipboard and dynamic resize.
11. Terminal command auditing, terminal output capture, timestamps, searchable logs, and faithful replay.
12. An optional asciinema recording mode for highly interactive terminal tools.
13. A service system supporting both in-container processes and multi-container Compose stacks.
14. A base image plus profile images, built locally or by CI and distributable through GHCR or Docker Hub.
15. Shell completion for Bash, Zsh, Fish, and PowerShell.

### 2.2 Non-goals

The following are explicitly out of scope for v1:

- Kubernetes, Podman, LXC/LXD, or another container runtime.
- Multi-user/team synchronization, shared assessment state, or remote collaboration.
- Wireless, Bluetooth, USB, or hardware-radio passthrough.
- Docker socket exposure inside the pentest container.
- Hardened container escape resistance against a hostile root user inside the pentest container.
- Tamper-evident or cryptographically signed audit logs.
- Secret redaction from terminal recordings.
- Encrypted workspaces.
- A graphical Quiver management UI or TUI.
- Transparent parity for layer-2 host networking across Linux, macOS, and Windows. Docker Desktop virtualizes Linux networking; some low-level behaviors cannot be made identical to native Linux.

## 3. Research conclusions and technology decisions

### 3.1 Docker runtime and host portability

Docker Desktop runs Linux containers inside a Linux VM on macOS and Windows. Consequently, network behavior is not identical to a native Linux Docker Engine. User-defined bridge networking remains the most predictable cross-platform default. Docker host networking is supported, but on Docker Desktop it is effectively layer-4 oriented and does not reproduce native Linux layer-2 namespace behavior. Port publishing is also ignored when host networking is active.

**Decision:** use a dedicated user-defined bridge per assessment by default. Support `host`, `none`, and named/custom networks as explicit advanced configuration. All host-published management ports MUST bind to `127.0.0.1` by default.

### 3.2 Docker integration method

The Docker CLI already resolves Docker contexts, TLS endpoints, local credential helpers, registries, Buildx, and Compose. Current Docker tooling also provides cross-platform context behavior and multi-platform builds.

**Decision:** make the installed `docker` executable a hard runtime dependency. Implement an internal `DockerBackend` abstraction around subprocess execution. Use JSON-formatted Docker outputs wherever possible. Do not require the Python Docker SDK in v1.

This minimizes platform-specific daemon-socket handling and preserves whatever Docker context the user has selected. A global `--context` option MUST be passed through to every Docker invocation without changing the user's persistent Docker context.

### 3.3 Python CLI framework

**Decision:** use Typer for the command surface and completion UX, with Pydantic v2 models for validated configuration. Use PyYAML or `pydantic-settings[yaml]` for YAML serialization/loading. Keep business logic independent of Typer so it is unit-testable.

### 3.4 BlackArch and multi-architecture images

BlackArch maintains official Docker build material and its repository provides a large penetration-testing package set. BlackArch also documents compatibility with Arch Linux ARM. However, the official Arch Linux Docker image remains amd64-only, so a single `FROM archlinux` Dockerfile cannot produce a true arm64 BlackArch image.

**Decision:** Quiver owns two architecture roots and converges after the bootstrap phase:

- **amd64:** official Arch Linux OCI base -> enable BlackArch repository -> common Quiver provisioning.
- **arm64:** Arch Linux ARM AArch64 root filesystem -> enable BlackArch repository -> common Quiver provisioning.

Do not depend on the BlackArch Docker rootfs artifacts as a permanent base contract because their official Docker repository describes short artifact retention. Quiver's architecture-specific Dockerfiles bootstrap their own upstream roots; local and CI builds MUST NOT require caller-supplied bootstrap URLs or checksums.

Profile package lists MUST permit architecture conditions. A profile build MUST fail with a readable package report if a mandatory package is unavailable for that architecture.

### 3.5 GUI transport

The evaluated options were direct X11/Wayland forwarding, TigerVNC + noVNC, KasmVNC, xrdp/RDP, and Apache Guacamole.

**Decision:** XFCE + TigerVNC + noVNC/Websockify.

Reasons:

- noVNC is browser-native and supports clipboard, scaling, and remote desktop resize.
- TigerVNC supports client clipboard updates and `SetDesktopSize`; both x86_64 Arch and AArch64 Arch Linux ARM currently package TigerVNC.
- It avoids a host X server dependency, which would be awkward on macOS and Windows.
- It avoids the additional gateway/application layer of Guacamole for a single localhost-only desktop.
- KasmVNC has an excellent browser UX and current arm64 release assets, but Arch packaging is less direct than TigerVNC and would add avoidable packaging/build complexity to the BlackArch image.
- RDP/xrdp adds a second desktop/session stack without a material advantage for localhost browser access.

The GUI is a local management plane, not a remotely exposed service. Docker port publication MUST bind it to loopback unless the user explicitly overrides this.

### 3.6 Service supervision

Docker documents Supervisor as a valid approach for multiple processes in a container. Quiver needs independent control of VPN and GUI processes, restart behavior, logs, and health.

**Decision:** run the container with Docker `--init` and use `supervisord` in foreground mode as the container's main process. Supervisor programs are generated from the instance configuration at startup.

Internal services in v1:

- `vpn` — OpenVPN or WireGuard client and kill switch.
- `gui` — TigerVNC + XFCE + Websockify/noVNC launcher.

External services are managed by Quiver on the host using Docker Compose. BloodHound CE is the first built-in external service definition; its official deployment consists of a PostgreSQL application database, Neo4j graph database, and BloodHound application/UI.

### 3.7 Terminal auditing

`script(1)` records a raw terminal output stream plus timing information suitable for `scriptreplay`. asciinema provides lightweight timed terminal recordings and is particularly useful for interactive terminal applications.

**Decision:** every interactive Quiver shell is started through a recording wrapper. The default recorder is util-linux `script`. Command metadata is recorded separately by shell hooks. Asciinema is selectable per session.

This produces three useful artifacts:

- command metadata (`commands.jsonl`) with wall-clock timestamps, working directory, command text, exit status, and duration;
- a terminal replay artifact (`typescript` + timing file, or `.cast`);
- a normalized text rendering (`output.txt`) for grep/search.

The design is auditing-oriented, not tamper-resistant. Root can bypass it intentionally, which is acceptable for the stated threat model.

## 4. Functional architecture

### 4.1 Host-side components

The Python package SHOULD be split into these modules:

```text
quiver/
  cli.py
  models/
    config.py
    runtime.py
    services.py
  core/
    lifecycle.py
    workspaces.py
    images.py
    ports.py
    browser.py
    doctor.py
  docker/
    backend.py
    commands.py
    inspect.py
    compose.py
  vpn/
    parser.py
    model.py
  audit/
    sessions.py
    render.py
  services/
    registry.py
    internal.py
    compose.py
  resources/
    services/
    schemas/
```

Responsibilities:

- **CLI layer:** parse options, display errors/status, invoke domain services.
- **Config layer:** load, validate, migrate, and write per-assessment YAML.
- **Lifecycle manager:** turn persisted configuration into Docker resources.
- **Docker backend:** deterministic command execution, JSON parsing, context propagation, and error translation.
- **Workspace manager:** directory initialization, path canonicalization, ownership repair, destroy semantics.
- **Service registry:** built-in service descriptors and service lifecycle.
- **Doctor:** host/runtime capability checks.

### 4.2 Container-side components

The image MUST contain:

- `/usr/local/bin/quiver-entrypoint`
- `/usr/local/bin/quiver-record-shell`
- `/usr/local/bin/quiver-vpn`
- `/usr/local/bin/quiver-gui`
- `/usr/local/lib/quiver/` helper scripts
- `/etc/supervisor/` base configuration
- `/etc/quiver/` image metadata and default service definitions
- `/opt/novnc/` pinned noVNC assets
- `/workspace` mount point

The entrypoint MUST:

1. validate that `/workspace` is mounted;
2. create `.logs` and `.vpn` if needed;
3. load generated runtime configuration from a read-only bind mount or environment-selected file;
4. generate Supervisor program fragments;
5. install/prepare shell audit hooks;
6. start Supervisor in foreground mode.

It MUST NOT mutate the persistent assessment configuration itself.

## 5. Repository layout

Recommended repository layout:

```text
.
|-- pyproject.toml
|-- README.md
|-- docs/
|   `-- implementation-spec.md
|-- src/quiver/
|-- tests/
|   |-- unit/
|   |-- integration/
|   `-- fixtures/
|-- images/
|   |-- base/
|   |   |-- Dockerfile.amd64
|   |   `-- Dockerfile.arm64
|   |-- common/
|   |   |-- entrypoint/
|   |   |-- supervisor/
|   |   |-- shell/
|   |   `-- gui/
|   `-- profiles/
|       |-- web/
|       |-- internal/
|       `-- full/
|-- packages/
|   |-- base.yaml
|   |-- web.yaml
|   `-- internal.yaml
|-- services/
|   |-- bloodhound-ce/
|   |   |-- service.yaml
|   |   `-- compose.yaml
|   `-- schema.json
`-- .github/workflows/
    |-- test-cli.yml
    |-- build-images.yml
    `-- release.yml
```

Package/profile manifests SHOULD be data files rather than giant Dockerfile command lists. This allows architecture conditions, easier diffing, and image metadata generation.

## 6. Host data model

Default root:

```text
~/.quiver/
`-- workspaces/
    `-- acme-2026/
        |-- .quiver.yaml
        |-- .logs/
        |   |-- sessions/
        |   |-- exec/
        |   `-- services/
        |-- .vpn/
        |   |-- client.ovpn
        |   `-- auth.txt
        |-- .services/           # implementation-owned, hidden
        |   `-- bloodhound-ce/
        `-- <normal assessment files>
```

Rules:

- The workspace root MUST be an absolute path after resolution.
- The primary container MUST mount it read/write at `/workspace`.
- The default working directory MUST be `/workspace`.
- `.quiver.yaml` is the source of truth for the assessment configuration.
- `.logs/` is never mounted read-only and persists after container removal.
- `.vpn/` stores the VPN configuration and optional credential material.
- `.services/` is reserved for bind-mounted persistent data belonging to external service stacks. It is implementation-owned rather than a user-facing standardized working directory.
- The CLI MUST refuse assessment names containing path traversal, slashes, control characters, or characters invalid in Docker object names.

### 6.1 Linux ownership behavior

The pentest shell runs as root. On native Linux, files created in a bind mount may therefore be owned by UID 0 on the host.

Quiver SHOULD record the invoking host UID/GID in runtime metadata and support:

```text
quiver workspace fix-perms <assessment>
```

On graceful `stop`, the CLI SHOULD attempt to normalize workspace ownership to the original host UID/GID from inside the container before removal. This operation is best-effort and primarily relevant to native Linux; Docker Desktop file-sharing layers may map ownership differently.

## 7. Configuration specification

### 7.1 General rules

- Format: YAML.
- Schema version MUST be explicit.
- Unknown top-level fields SHOULD be rejected by default.
- Every load MUST pass Pydantic validation.
- `quiver edit` MUST validate the edited file before replacing the previous valid configuration.
- A failed edit MUST preserve the previous file and display field-level validation errors.
- Future schema migrations MUST be explicit and one-way, with a backup written before migration.

### 7.2 Example configuration

```yaml
schema_version: 1
name: acme-2026

image:
  profile: internal
  reference: ghcr.io/example/quiver-internal:stable
  pull_policy: missing
  platform: auto

docker:
  context: default
  privileged: false
  network:
    mode: bridge
    name: null
  capabilities:
    add: []
    drop: []
  devices: []
  sysctls: {}
  environment: {}
  ports:
    - host_ip: 127.0.0.1
      host_port: 8081
      container_port: 8081
      protocol: tcp

workspace:
  path: ~/.quiver/workspaces/acme-2026
  container_path: /workspace
  fix_ownership_on_stop: true

vpn:
  enabled: true
  type: openvpn
  config: .vpn/client.ovpn
  credentials_file: .vpn/auth.txt
  full_tunnel: true
  fail_closed: true
  dns_through_vpn: false
  local_management_bypass: true
  bypass_cidrs: []
  healthcheck:
    timeout_seconds: 30
    require_interface: true

gui:
  enabled: true
  backend: novnc
  desktop: xfce
  container_port: 6080
  host_ip: 127.0.0.1
  host_port: null
  authentication: true
  clipboard: true
  dynamic_resize: true
  initial_geometry: 1600x1000

logging:
  enabled: true
  shell_recorder: script
  record_commands: true
  render_searchable_text: true
  asciinema_idle_limit: 2

services:
  autostart: []
  enabled:
    - bloodhound-ce
  config: {}
```

### 7.3 Image fields

`image.profile` is a logical profile name known to Quiver. `image.reference` is the concrete registry reference. Either may be omitted if the other resolves unambiguously from global defaults.

`pull_policy` values:

- `missing` — default; pull only if absent.
- `always` — pull before each new container.
- `never` — fail if missing locally.

`platform` values: `auto`, `linux/amd64`, `linux/arm64`.

### 7.4 Docker fields

`privileged` MUST map directly to Docker privileged mode.

Quiver MUST automatically add feature-required permissions when `privileged` is false. For VPN this normally includes `NET_ADMIN` and TUN access. User-declared capability additions are additive.

Supported network modes:

- `bridge` — Quiver-created assessment bridge, recommended/default.
- `host` — explicit advanced mode.
- `none` — fully disconnected container; incompatible with enabled VPN.
- `custom` — attach to a user-specified existing Docker network.

### 7.5 Port fields

`host_port: null` means Docker chooses an available host port. Quiver MUST discover and display the assigned port using `docker inspect`.

All automatically created management ports MUST default to `127.0.0.1`, not `0.0.0.0`.

## 8. CLI contract

### 8.1 Global options

```text
quiver [--context NAME] [--root PATH] [--verbose] [--no-color] COMMAND ...
```

- `--context` applies a Docker context to every Docker command for this invocation only.
- `--root` overrides `~/.quiver`.
- `--verbose` exposes Docker commands and detailed diagnostics without leaking stored passwords where avoidable.

### 8.2 `start`

```text
quiver start NAME [creation options] [--detach]
```

Semantics:

- If NAME does not exist, create the workspace/config from defaults plus supplied creation options, then start it.
- If NAME exists, load the persisted config and recreate the primary container from it.
- If the container is already healthy/running, do not create a duplicate; enter a shell unless `--detach` is specified.
- By default, `start` enters a recorded shell after readiness checks. `--detach` only starts the environment.
- Creation options on an existing assessment MUST NOT silently rewrite persisted configuration. The CLI should reject mutating creation flags and direct the user to `edit`, unless `--reconfigure` is explicitly supplied.

Representative creation options:

```text
--image PROFILE_OR_REF
--platform auto|linux/amd64|linux/arm64
--vpn PATH
--vpn-type auto|openvpn|wireguard
--vpn-credentials PATH
--privileged / --no-privileged
--network bridge|host|none|custom
--network-name NAME
-p, --publish [HOST_IP:]HOST_PORT:CONTAINER_PORT[/PROTO]
--service NAME
--gui / --no-gui
```

Start sequence is defined in section 9.

### 8.3 `stop`

```text
quiver stop NAME
```

`stop` MUST:

1. stop external autostarted service stacks unless configured otherwise;
2. run best-effort workspace ownership normalization;
3. stop the primary container;
4. remove the primary container;
5. remove ephemeral assessment network resources when no managed service needs them;
6. preserve workspace, configuration, VPN files, logs, and service data.

This command intentionally leaves no stopped primary container behind.

### 8.4 `destroy`

```text
quiver destroy NAME [--yes]
```

`destroy` MUST stop/remove all managed Docker resources for the assessment and delete its workspace. It MUST require interactive confirmation unless `--yes` is supplied. It MUST display the exact workspace path that will be deleted before confirmation.

### 8.5 `edit`

```text
quiver edit NAME
quiver edit NAME --set docker.privileged=true
quiver edit NAME --vpn new-client.ovpn
quiver edit NAME --publish 8443:8443
```

Without structured flags, open `.quiver.yaml` using `$VISUAL`, then `$EDITOR`, then a platform default. Write to a temporary file, validate, and atomically replace the active configuration only on success.

If a runtime-affecting setting changes while the container is running, report that the change requires recreation. Optional `--restart` MAY apply stop/start immediately.

### 8.6 Shell and exec

```text
quiver shell NAME [--asciinema]
quiver exec NAME -- COMMAND [ARGS...]
```

`shell` MUST be interactive and recorded unless logging is globally disabled. It uses the configured shell/dotfiles.

`exec` is intended for non-interactive commands. Quiver MUST capture stdout/stderr and write a timestamped log entry under `.logs/exec/` in addition to forwarding output to the terminal.

### 8.7 GUI

```text
quiver gui NAME [--no-open]
```

Behavior:

1. ensure the container is running;
2. start/health-check the internal `gui` service through Supervisor;
3. discover the host-published port;
4. print the localhost URL;
5. open the system default browser unless `--no-open` is set.

The command MUST never invent a public bind. If the config explicitly binds GUI to a non-loopback address, print a visible warning in status output.

### 8.8 Service commands

```text
quiver service list NAME
quiver service up NAME SERVICE
quiver service down NAME SERVICE
quiver service restart NAME SERVICE
quiver service status NAME [SERVICE]
quiver service logs NAME SERVICE [-f]
```

The user-facing command is uniform across internal and Compose services.

### 8.9 Status/list/logging/image commands

```text
quiver list
quiver status [NAME]
quiver logs NAME [--session SESSION_ID]
quiver replay NAME SESSION_ID
quiver image list
quiver image pull PROFILE_OR_REF
quiver image build PROFILE [--platform ...]
quiver doctor
```

`status NAME` SHOULD show image, runtime/container state, privileged flag, network mode, VPN state, GUI URL, enabled service states, workspace, and recent session ID.

### 8.10 Completion

Expose Typer/Click completion for Bash, Zsh, Fish, and PowerShell. Assessment names, service names, and profile names SHOULD be dynamically completed.

## 9. Lifecycle and state machine

Persisted assessment states are intentionally minimal:

```text
ABSENT -> CONFIGURED -> RUNNING
            ^             |
            |             v
            +---------- STOPPED
```

`STOPPED` and `CONFIGURED` are equivalent from Docker's perspective: configuration/workspace exist but no primary container exists.

Runtime resources are discovered through Docker labels rather than a separate runtime database.

### 9.1 Resource labels

Every managed Docker object SHOULD carry labels where supported:

```text
io.quiver.managed=true
io.quiver.assessment=acme-2026
io.quiver.component=primary|service
io.quiver.service=<service-id>
io.quiver.schema=1
```

### 9.2 Start algorithm

1. Resolve workspace and configuration.
2. Acquire an assessment-scoped host file lock to prevent two concurrent Quiver mutations.
3. Run lightweight preflight checks.
4. Resolve image reference and target platform.
5. Pull image according to policy.
6. If a stale managed primary container exists, remove it.
7. Ensure the assessment Docker network exists for bridge mode.
8. Materialize a runtime-only config file from validated persisted config. Runtime config MAY contain generated ports, resolved IDs, or secrets needed by services and MUST be mounted read-only.
9. Create the primary container with labels, workspace mount, configured ports, capabilities/devices, and `--init`.
10. Start the primary container.
11. If VPN is enabled, wait for VPN service health. If health fails, leave networking fail-closed, collect diagnostics, stop/remove the primary container, and return an error.
12. Start configured internal autostart services.
13. Start configured external autostart services and attach them to the assessment network.
14. Report readiness.
15. Enter recorded shell unless detached.

### 9.3 Stop algorithm

See section 8.3. Stop MUST be idempotent.

### 9.4 Crash/stale recovery

`start` MUST be able to recover from:

- an exited primary container;
- a primary container with the expected name but missing Quiver labels;
- a leftover network;
- partially started Compose services;
- a previous VPN failure.

Unlabeled resources with colliding names MUST never be deleted automatically. Instead, fail with an actionable conflict message.

## 10. Docker runtime contract

### 10.1 Container defaults

- user: `root`
- working directory: `/workspace`
- hostname: derived from assessment slug
- init: enabled (`--init`)
- restart policy: `no`; Quiver owns lifecycle
- primary filesystem: disposable writable layer
- workspace: bind mount, read/write
- Docker socket: never mounted
- GUI: packaged but on-demand
- VPN: autostart when enabled

### 10.2 Capabilities

When `docker.privileged: true`, use privileged mode and do not try to emulate it with capability lists.

When false:

- preserve Docker defaults;
- add `NET_ADMIN` when VPN is enabled;
- provide `/dev/net/tun` when needed and available;
- allow user-configured extra capabilities;
- do not automatically add broad capabilities such as `SYS_ADMIN`.

Pentest profiles may document tools that require privileged mode or extra capabilities. The CLI SHOULD surface a concise hint when a known operation fails because of missing capability, but it should not silently restart privileged.

### 10.3 Network modes

#### Bridge

Quiver creates `quiver-<assessment>` as a user-defined bridge. This gives service DNS and isolation from unrelated containers.

#### Host

Supported as an explicit option. Constraints:

- Docker port publishing is ignored.
- On native Linux the container shares the host network namespace.
- On Docker Desktop host networking has different semantics and does not provide full Linux layer-2 equivalence.
- **VPN + host mode on native Linux MUST be rejected by default** because an in-container VPN would manipulate the host network namespace and could redirect the host itself. An expert-only escape hatch, if implemented, must be named explicitly and require a confirmation flag.

#### None

No network. Incompatible with VPN, GUI host access, and external service access unless the user changes configuration.

#### Custom

Attach to a named existing network. Quiver does not delete user-owned custom networks.

## 11. VPN subsystem

### 11.1 Supported input

- OpenVPN `.ovpn` / `.conf`
- WireGuard `wg-quick` style `.conf`
- `vpn.type: auto` detects from syntax/extension and fails if ambiguous.

On first creation with `--vpn PATH`, Quiver SHOULD copy the file into `.vpn/` so subsequent starts do not depend on the original external path.

### 11.2 Required behavior

When VPN is enabled:

- the VPN MUST start automatically before the assessment is considered ready;
- all normal outbound traffic MUST use the tunnel;
- a tunnel process failure MUST NOT restore clear-net outbound access;
- DNS is not required to traverse the tunnel; this is an intentional configuration choice;
- local/management communication needed for Quiver GUI and service containers MUST remain reachable;
- status MUST report VPN type, interface, process health, and basic tunnel state.

### 11.3 OpenVPN runtime

Run OpenVPN in foreground mode under Supervisor.

The container normally requires:

- `NET_ADMIN`;
- TUN device access;
- root user.

Quiver MUST prefer `tun` rather than `tap` for cross-platform Docker Desktop compatibility. Layer-2 TAP VPNs are not a v1 compatibility target.

The launcher SHOULD enforce a default-route redirect even if the supplied profile does not explicitly request one, unless an advanced config override disables Quiver route enforcement.

Credentials MAY be supplied via `.vpn/auth.txt` or generated at runtime from configuration. Never put username/password directly on the OpenVPN process command line.

### 11.4 WireGuard runtime

Prefer kernel WireGuard when the Docker VM/host kernel supports it. If kernel interface creation fails for a known unsupported reason, the image MAY fall back to `wireguard-go`, which uses a TUN device and otherwise follows the same `wg` configuration interface.

`AllowedIPs = 0.0.0.0/0` and, when IPv6 is supported by the assessment network, `::/0` are the expected full-tunnel semantics. Quiver SHOULD validate and, if configured to enforce full tunnel, reject or override a profile that does not cover the default route.

### 11.5 Kill-switch algorithm

The kill switch MUST be active before VPN establishment. Prefer nftables; provide an iptables fallback only if the image/runtime requires it.

Algorithm:

1. Determine the base outbound interface and gateway.
2. Resolve the VPN endpoint hostname before locking down traffic. Cache endpoint IPs for the current start.
3. Determine Docker's embedded/local resolver and current resolver addresses.
4. Determine assessment-management CIDRs: at minimum the assessment Docker bridge and loopback.
5. Add explicitly configured bypass CIDRs.
6. Install a dedicated firewall table/chain with default outbound reject/drop semantics.
7. Permit:
   - loopback;
   - established/related traffic;
   - traffic over the VPN interface;
   - VPN endpoint IP/port/protocol over the base interface;
   - DNS over the base interface when `dns_through_vpn: false`;
   - assessment-management and explicitly configured bypass CIDRs over the base interface.
8. Start the VPN client.
9. Verify interface/route/handshake health.
10. Keep the restrictive policy if the VPN process crashes.

An intentional service shutdown may remove the kill switch only if the user explicitly disables VPN or invokes a command equivalent to `vpn down --unlock-network`. A crash/restart path MUST remain fail-closed.

### 11.6 VPN health

Minimum health checks:

- process is RUNNING in Supervisor;
- expected interface exists;
- full-tunnel routing/policy is installed;
- WireGuard: recent handshake when applicable;
- OpenVPN: management/state output or established interface/state marker.

Do not depend on a public “what is my IP” web service for core health; assessments may intentionally have no internet egress.

### 11.7 DNS behavior

Because `dns_through_vpn` is false by default in this specification, Quiver MUST preserve working DNS through Docker's resolver or the original host-provided resolvers and add those destinations to the kill-switch allowlist. Status SHOULD mark this as `DNS: host/outside tunnel` so the behavior is explicit.

### 11.8 Docker Desktop preflight

`quiver doctor` MUST test TUN availability through the active Docker daemon rather than assuming host filesystem semantics. A disposable probe container SHOULD verify that a Linux container can access/create the necessary TUN functionality with the intended capability/device configuration.

## 12. GUI subsystem

### 12.1 Components

- XFCE desktop environment
- TigerVNC/Xvnc server
- noVNC web client
- Websockify bridge
- browser opened by host CLI

### 12.2 Runtime flow

1. `gui` Supervisor program starts Xvnc on an internal VNC port, e.g. 5901.
2. Start XFCE against the VNC-backed X display.
3. Start Websockify/noVNC on container port 6080.
4. Docker publishes 6080 to `127.0.0.1:<assigned>` in bridge mode.
5. `quiver gui` discovers the host port and opens `http://127.0.0.1:<assigned>/vnc.html` with suitable default query parameters.

### 12.3 Clipboard

TigerVNC MUST be configured to accept/send clipboard updates. noVNC clipboard support MUST be enabled. Chromium-family browsers are the primary validation target; Firefox is secondary.

Clipboard testing MUST cover host -> desktop and desktop -> host text transfer.

### 12.4 Resize

Enable TigerVNC `AcceptSetDesktopSize` and noVNC remote resize. Browser window size changes SHOULD resize the virtual desktop rather than merely scaling where supported.

### 12.5 Authentication

Even though the published endpoint is loopback-only, GUI authentication is enabled by default. Generate a per-assessment random credential on first GUI enablement and persist it in the assessment configuration or an implementation-owned credential file with user-only permissions.

The authentication mechanism MAY be standard TigerVNC authentication. Do not expose VNC's internal TCP port to the host; only publish noVNC/Websockify.

### 12.6 TLS

TLS is optional for localhost-only v1. `localhost`/loopback is the trust boundary. If future non-loopback exposure is requested, TLS becomes mandatory and is a separate design decision.

### 12.7 Audio and file transfer

Audio is not required. File transfer is not required because `/workspace` is already shared with the host. Do not add protocol-specific file-transfer complexity in v1.

## 13. Service subsystem

### 13.1 Unified service model

A service descriptor has:

```yaml
id: bloodhound-ce
display_name: BloodHound CE
kind: compose          # internal | compose
autostart_default: false
requires_network: true
supported_platforms:
  - linux/amd64
  - linux/arm64
healthcheck:
  type: http
  target: http://127.0.0.1:${BLOODHOUND_PORT}/
```

Quiver exposes the same user commands regardless of service kind.

### 13.2 Internal services

Internal services map to Supervisor program names. `service up/down/restart/status/logs` invokes `supervisorctl` through `docker exec` and translates states to Quiver states.

### 13.3 Compose services

Compose service definitions are shipped as package resources. Quiver renders an assessment-specific environment file and, if needed, a generated Compose override that attaches relevant containers to the assessment network and binds exposed ports to loopback.

Use a deterministic project name:

```text
quiver-<assessment>-<service>
```

Compose invocations MUST include the same Docker context as the primary lifecycle operation.

### 13.4 BloodHound CE built-in service

The built-in definition SHOULD be derived from the current official BloodHound CE Docker Compose example and contain:

- PostgreSQL application database;
- Neo4j graph database;
- BloodHound application/UI.

Defaults:

- BloodHound UI published to `127.0.0.1` only;
- Neo4j browser/database ports not published unless explicitly requested;
- database credentials generated per assessment rather than using upstream example defaults;
- persistent database state stored under `.services/bloodhound-ce/` through bind mounts;
- the BloodHound application attached to the assessment Docker network so the primary container can reach it by service alias;
- service-specific logs available through `quiver service logs` and optionally mirrored under `.logs/services/`.

### 13.5 Service networking and VPN interaction

The primary container's kill switch MUST permit traffic to the dedicated assessment bridge so local service stacks remain reachable even when all external traffic uses the VPN.

External service containers do not automatically inherit the primary container's VPN. They should be treated as local tooling/services. A future service descriptor may choose another network model, but v1 does not route arbitrary sidecars through the pentest container.

## 14. Audit/logging subsystem

### 14.1 Session identity

Each interactive shell gets a session ID:

```text
20260920T142701Z-7f3a2c
```

Directory:

```text
.logs/sessions/<session-id>/
  metadata.json
  commands.jsonl
  output.ansi
  timing.log
  output.txt
  session.cast          # only for asciinema sessions
```

### 14.2 Session metadata

`metadata.json` SHOULD include:

```json
{
  "session_id": "...",
  "assessment": "acme-2026",
  "started_at": "2026-09-20T12:27:01Z",
  "ended_at": "...",
  "container_image": "ghcr.io/...@sha256:...",
  "container_id": "...",
  "shell": "/bin/zsh",
  "recorder": "script",
  "host": "...",
  "docker_context": "default"
}
```

Do not record the full environment by default.

### 14.3 Command log

Preferred default shell: Zsh.

Use `preexec`/`precmd` hooks to write start/end records. Example logical event:

```json
{"event":"command","ts":"2026-09-20T12:30:41.131Z","cwd":"/workspace","command":"nmap -sV 10.10.10.5","exit":0,"duration_ms":8123}
```

For Bash fallback, use a carefully scoped DEBUG/PROMPT_COMMAND hook and suppress recursive logging from the logger itself.

The logging implementation MUST correctly JSON-escape multi-line commands and shell metacharacters.

### 14.4 Terminal recorder

Default:

- util-linux `script` logs output and timing;
- input keystrokes are not separately logged by default;
- after the session exits, convert raw terminal output to a plain-text searchable form by stripping ANSI/control sequences while preserving line breaks as well as reasonably possible.

Replay:

```text
quiver replay NAME SESSION_ID
```

invokes `scriptreplay` using the recorded output/timing files.

### 14.5 Asciinema mode

`quiver shell NAME --asciinema` records an asciicast locally under the same session directory. Never auto-upload recordings.

When asciinema is used, Quiver MUST still maintain `commands.jsonl` and generate `output.txt` from output events after the recording ends.

### 14.6 GUI terminals

The default terminal emulator inside XFCE MUST launch `quiver-record-shell` rather than launching Zsh directly. This ensures normal GUI terminal sessions use the same audit path as `quiver shell`.

A root user can intentionally bypass this by launching a different shell/process; preventing that is not a requirement.

### 14.7 Non-interactive execution

`quiver exec` MUST create:

```text
.logs/exec/<timestamp>-<short-id>.json
.logs/exec/<timestamp>-<short-id>.stdout
.logs/exec/<timestamp>-<short-id>.stderr
```

The JSON record includes command, start/end timestamps, exit code, and working directory.

## 15. Images and profiles

### 15.1 Image layering

Recommended hierarchy:

```text
quiver-root-<arch>
   -> quiver-base
       -> quiver-web
       -> quiver-internal
       -> quiver-full
```

`quiver-base` contains:

- BlackArch repository configuration;
- common shell/dotfiles;
- Python/runtime helpers;
- Supervisor;
- OpenVPN/WireGuard tooling;
- XFCE/TigerVNC/noVNC support;
- terminal auditing tools;
- common pentest utilities.

Profiles only add tooling and profile-specific configuration. They MUST inherit the same Quiver runtime contract.

### 15.2 Package manifests

Example:

```yaml
profile: internal
inherits: base
packages:
  - nmap
  - impacket
  - netexec
  - responder
  - bloodhound-python
  - tcpdump
arch:
  amd64:
    packages: []
  arm64:
    optional:
      - some-package-not-always-published
```

A build report MUST show skipped optional packages and fail on missing mandatory packages.

### 15.3 Dotfiles

Dotfiles are part of the image, not the workspace, unless the user explicitly mounts/overrides them in a later customization feature. This keeps assessment workspaces clean and image behavior reproducible.

The default shell configuration MUST include audit hooks but SHOULD place user-facing customizations in a separate sourced file so logging logic is not tangled with appearance aliases/themes.

### 15.4 Versioning

Publish at least:

- immutable release tag, e.g. `v0.1.0`;
- date/build tag, e.g. `2026.09.20`;
- channel tag, e.g. `stable`;
- OCI digest recorded in session metadata.

Avoid relying solely on `latest` for assessment reproducibility.

## 16. CI/CD image pipeline

### 16.1 Build strategy

Use GitHub Actions with native x64 and arm64 Linux runners where available. Native arm64 avoids the heavy cost and occasional incompatibility of building a large Arch/BlackArch image under QEMU.

Build each architecture separately, then publish a multi-platform manifest for each profile.

### 16.2 Workflow outline

1. validate package manifests and Dockerfiles;
2. build amd64 on `ubuntu-24.04` or equivalent;
3. build arm64 on `ubuntu-24.04-arm` or equivalent;
4. run profile smoke tests natively on each architecture;
5. push architecture-specific image digests;
6. create/push a manifest list for the profile tag;
7. generate build provenance/SBOM if convenient;
8. record failed/missing BlackArch packages in the job summary.

Docker Buildx and the Docker build-push action support multi-platform image publication. If native runners are unavailable for a deployment, QEMU/Buildx is a fallback rather than the preferred path.

### 16.3 Local builds

`quiver image build PROFILE` SHOULD run the same build definition locally through Buildx. Default to the Docker daemon's native platform unless `--platform` is supplied.

## 17. Cross-platform behavior

### 17.1 Linux

Linux is the reference implementation and has the strongest networking behavior.

Expected full support:

- bridge networking;
- privileged containers;
- TUN/OpenVPN/WireGuard;
- raw sockets/sniffing inside the container namespace;
- native host networking;
- bind-mounted workspace.

### 17.2 macOS Docker Desktop

Expected support:

- bridge networking and published localhost ports;
- privileged Linux containers inside Docker's VM;
- VPN inside the Linux container when TUN preflight succeeds;
- browser GUI;
- arm64 images on Apple Silicon and amd64 images on Intel or emulation.

Limitations:

- no true native-Linux layer-2 host-network equivalence;
- host filesystem bind performance differs from Linux;
- host networking depends on Docker Desktop capabilities/settings and remains an advanced mode.

### 17.3 Windows Docker Desktop

Expected support using Linux containers:

- bridge networking and published localhost ports;
- VPN inside the Docker Linux VM/container when TUN preflight succeeds;
- browser GUI;
- workspace bind mounts;
- PowerShell completion.

Windows/WSL networking is virtualized. A VPN established in a Linux container affects that container; it does not imply that Windows or a surrounding WSL distribution inherits the route. This is desirable for Quiver isolation.

### 17.4 Remote Docker contexts

Remote Docker contexts conflict with a local host workspace bind because bind paths are evaluated on the daemon host.

**Decision:** v1 supports local Docker daemons only for assessment containers. `doctor` SHOULD detect a remote context endpoint and fail with a clear explanation rather than creating a container with a broken/nonexistent workspace mount.

## 18. `quiver doctor`

`doctor` is mandatory because VPN and Docker Desktop capabilities vary by platform.

Checks:

1. Python supported version.
2. `docker` executable exists.
3. Docker daemon reachable.
4. Docker daemon OS is Linux.
5. Docker context is local/supported.
6. daemon architecture detected.
7. Buildx available for local image builds.
8. Compose v2 available for external services.
9. user can create/start/remove a small probe container.
10. TUN/VPN capability probe with intended permissions.
11. host networking availability/status if selected.
12. workspace root writable.
13. browser launch availability is informational only.

Output SHOULD be a concise table with `OK`, `WARN`, or `FAIL` and a remediation string.

## 19. Error handling and UX

### 19.1 Error classes

Create typed domain errors:

- `ConfigError`
- `DockerUnavailableError`
- `DockerConflictError`
- `ImageUnavailableError`
- `PlatformUnsupportedError`
- `VpnError`
- `ServiceError`
- `WorkspaceError`
- `ValidationError`

CLI exits SHOULD be stable enough for scripting.

Suggested exit codes:

```text
0  success
2  usage/config validation
3  Docker unavailable/runtime failure
4  image/platform failure
5  VPN failure
6  service failure
7  workspace/filesystem failure
```

### 19.2 Command execution

All subprocess calls MUST:

- use argument arrays, not shell interpolation;
- capture stderr for diagnostics;
- redact known credential values from verbose logs;
- support cancellation/CTRL-C cleanly;
- preserve the child exit code where sensible.

### 19.3 Concurrency

Use a lock file per assessment under the workspace. Mutating commands (`start`, `stop`, `destroy`, `edit --restart`, service mutations) MUST serialize. Read-only commands may proceed when safe.

## 20. Security and exposure defaults

Although the container itself is intentionally permissive for pentesting, Quiver should have conservative host exposure defaults:

- root inside container: yes;
- privileged: off unless configured;
- Docker socket: never mounted;
- GUI published to loopback only;
- BloodHound UI published to loopback only;
- arbitrary user port mappings: allowed as configured;
- VPN fail-closed: on;
- external DNS outside VPN: on by stated requirement;
- host/local bypass: only management subnet plus explicitly configured CIDRs;
- secrets may persist in workspace/config because stronger secret storage was not requested, but file permissions SHOULD be restrictive where the host supports them.

## 21. Testing strategy

### 21.1 Unit tests

Must cover:

- YAML schema validation and migrations;
- config creation/merge/edit behavior;
- Docker command construction and quoting;
- name/slug validation;
- port parsing;
- OpenVPN/WireGuard config detection;
- VPN endpoint extraction;
- kill-switch ruleset generation;
- service descriptor rendering;
- profile/architecture package resolution;
- audit JSON encoding and ANSI stripping.

### 21.2 Docker integration tests

Run on Linux amd64 and arm64:

- start -> shell/exec -> stop -> start preserves workspace;
- container overlay changes disappear after stop/start;
- privileged and unprivileged modes match config;
- port publication works and defaults to loopback;
- custom capability injection works;
- assessment bridge creation/cleanup is idempotent;
- stale resource recovery works;
- unlabeled name collisions are never deleted.

### 21.3 VPN tests

Create controlled test VPN endpoints in CI or a dedicated integration environment.

Verify:

1. pre-tunnel kill switch blocks unrelated clear-net egress;
2. VPN endpoint remains reachable;
3. tunnel establishes;
4. target traffic traverses tunnel;
5. management subnet remains reachable;
6. DNS follows configured outside-tunnel behavior;
7. killing the VPN process leaves clear-net blocked;
8. Supervisor restart can re-establish the tunnel;
9. intentional disable can restore ordinary networking when explicitly requested.

Run separately for OpenVPN and WireGuard.

### 21.4 GUI tests

Automate where practical with a browser driver:

- noVNC page loads from loopback-published port;
- authentication required;
- XFCE becomes available;
- resize request changes X desktop geometry;
- clipboard works both directions for text;
- GUI service can stop/restart without recreating the primary container.

### 21.5 Audit tests

- interactive command appears once in `commands.jsonl`;
- timestamps are UTC ISO 8601;
- exit code and duration recorded;
- terminal output captures TTY-aware applications;
- `scriptreplay` works;
- asciinema session plays locally;
- searchable output is generated;
- multiple simultaneous terminals get independent session IDs/files.

### 21.6 Cross-platform release smoke tests

Before a release, manually or through suitable runners validate:

- Linux amd64;
- Linux arm64;
- macOS Apple Silicon Docker Desktop;
- Windows 11 Docker Desktop/WSL2 backend.

The acceptance target is functional equivalence of the Quiver workflow, not identical low-level network behavior.

## 22. Acceptance criteria for v1

v1 is complete only when all of the following are true:

1. `quiver start demo` creates config/workspace, starts a disposable container, and enters a recorded root shell.
2. A file created in `/workspace` survives `stop` + `start`; a package/file created elsewhere in the container does not.
3. `quiver edit demo` can change VPN config, credentials reference, privileged mode, network mode, and port mappings and validates before save.
4. `quiver stop demo` leaves no primary container but preserves the workspace.
5. `quiver destroy demo` removes all Quiver-managed assessment resources and workspace after confirmation.
6. OpenVPN and WireGuard full-tunnel modes pass fail-closed integration tests.
7. VPN failure cannot silently fall back to ordinary outbound networking.
8. `quiver gui demo` opens a localhost browser desktop with clipboard and resize support.
9. Standard terminal sessions produce replayable output plus searchable logs and per-command timestamp metadata.
10. `--asciinema` produces a local replayable cast without uploading.
11. `quiver service up demo bloodhound-ce` starts a usable BloodHound CE stack and `down` stops it without losing configured persistent service data.
12. `quiver service status/logs` works for both internal and Compose services.
13. amd64 and arm64 profile images are built in CI and published as a single multi-platform profile tag where package availability permits.
14. `quiver doctor` identifies unsupported TUN/host-network/runtime conditions before an assessment start fails mysteriously.
15. Shell completion works on Bash, Zsh, Fish, and PowerShell.
16. The core workflow is validated on Linux, macOS Docker Desktop, and Windows Docker Desktop.

## 23. Implementation order

Everything below is part of v1; the order is only to reduce integration risk.

### Phase A — CLI/config/lifecycle foundation

- Python package, Typer command tree, Pydantic config.
- workspace creation and locking.
- Docker backend/context handling.
- start/stop/destroy/edit/status/list.
- labels and assessment network.
- image/profile resolution.

### Phase B — base image and auditing

- amd64 BlackArch/Arch base.
- common entrypoint and Supervisor.
- dotfiles/root shell.
- `script` recording + command JSONL + replay.
- exec logging.

### Phase C — VPN

- TUN doctor probe.
- OpenVPN launcher.
- WireGuard launcher + optional userspace fallback.
- nftables kill switch.
- health/status and fail-closed tests.

### Phase D — GUI

- XFCE + TigerVNC.
- vendored/pinned noVNC + Websockify.
- loopback port discovery.
- auth, clipboard, resize, browser launcher.
- GUI terminal recording integration.

### Phase E — service framework

- unified service registry.
- Supervisor adapter.
- Compose adapter.
- BloodHound CE built-in descriptor and persistence.

### Phase F — arm64 and release automation

- Arch Linux ARM root pipeline.
- architecture-aware package manifests.
- native arm64 CI.
- multi-platform manifests.
- Docker Hub/GHCR publishing.
- release smoke-test checklist.

## 24. Specific implementation notes

### 24.1 Prefer generated runtime config over environment-variable sprawl

Persisted `.quiver.yaml` should be transformed into a runtime JSON/YAML file and mounted read-only at `/run/quiver/config.yaml`. This gives the container helpers a structured input while avoiding hundreds of environment variables. Only simple boot selectors should be environment variables.

### 24.2 Do not mutate VPN profiles unnecessarily

Keep the user's source VPN profile recognizable. Enforcement rules (default route, kill switch, credentials path) should be applied by launcher arguments/runtime firewall logic where possible. If a normalized generated config is necessary, write it to `/run/quiver/`, not back into `.vpn/`.

### 24.3 Keep management traffic narrowly scoped

Do not solve “VPN plus local access” by broadly exempting all RFC1918 ranges. Pentest VPN target ranges frequently use RFC1918 space. The automatic bypass should cover only the assessment bridge/management plane. Additional LAN CIDRs must be explicit or discovered and confirmed through a dedicated setting.

### 24.4 Image metadata

Every image SHOULD contain `/etc/quiver/image.json`:

```json
{
  "schema": 1,
  "profile": "internal",
  "version": "2026.09.20",
  "git_revision": "...",
  "architecture": "amd64",
  "blackarch_snapshot": "..."
}
```

`quiver status` and session logs should read this rather than inferring profile from image name alone.

### 24.5 Avoid hidden Docker state

The CLI should be able to reconstruct runtime state from `.quiver.yaml` plus Docker labels. Do not introduce a SQLite runtime database unless a concrete future requirement demands it.

### 24.6 Local service data

Service database directories under `.services/` may become large. `destroy` MUST include their size in the confirmation summary where practical. `stop` MUST never delete them.

## 25. References used for the design

1. BlackArch official Docker images — https://github.com/BlackArch/blackarch-docker
2. BlackArch downloads / Arch Linux ARM compatibility — https://blackarch.org/downloads.html
3. Arch Linux official Docker image architecture information — https://hub.docker.com/_/archlinux
4. Docker host network driver — https://docs.docker.com/engine/network/drivers/host/
5. Docker Desktop networking — https://docs.docker.com/desktop/features/networking/
6. Docker multi-platform builds — https://docs.docker.com/build/building/multi-platform/
7. Docker multi-platform GitHub Actions — https://docs.docker.com/build/ci/github-actions/multi-platform/
8. GitHub-hosted runner architecture matrix — https://docs.github.com/en/actions/reference/runners/github-hosted-runners
9. noVNC project/features — https://github.com/novnc/noVNC
10. TigerVNC Xvnc documentation — https://tigervnc.org/doc/Xvnc.html
11. KasmVNC server/client documentation — https://www.kasmweb.com/kasmvnc/docs/latest/
12. Apache Guacamole manual — https://guacamole.apache.org/doc/gug/
13. WireGuard `wg-quick` manual and kill-switch example — https://man7.org/linux/man-pages/man8/wg-quick.8.html
14. WireGuard userspace implementation — https://www.wireguard.com/xplatform/
15. OpenVPN Docker/TUN capability guidance — https://openvpn.net/as-docs/v3/docker.html
16. Docker container capability guidance — https://docs.docker.com/engine/containers/run/
17. util-linux `script(1)` — https://github.com/util-linux/util-linux/blob/master/term-utils/script.1.adoc
18. asciinema CLI/recording format — https://docs.asciinema.org/manual/cli/ and https://docs.asciinema.org/manual/asciicast/v3/
19. Docker multi-service container guidance — https://docs.docker.com/engine/containers/multi-service_container/
20. BloodHound CE official Docker Compose example — https://github.com/SpecterOps/BloodHound/tree/main/examples/docker-compose
21. Docker contexts — https://docs.docker.com/engine/manage-resources/contexts/
22. Typer completion — https://typer.tiangolo.com/tutorial/options-autocompletion/
23. Click shell completion — https://click.palletsprojects.com/en/stable/shell-completion/
