# Quiver user guide

Quiver creates a disposable Docker container per assessment while keeping the assessment workspace on the host. The container is disposable; files in the assessment workspace persist across `stop` and the next `start`.

> **Authorization:** use Quiver only for systems and networks you are authorized to assess.

## 1. Prerequisites

```bash
uv sync --all-groups
uv run quiver doctor
```

You need a Docker daemon running Linux containers. Image builds require Docker Buildx, and Compose-backed services require Docker Compose v2. Quiver supports either Docker CLI plugin form or standalone binaries:

```bash
# Either form is accepted for each feature:
docker buildx version || docker-buildx version
docker compose version || docker-compose version
uv run quiver doctor
```

When `docker buildx` or `docker compose` is unavailable but `docker-buildx` or `docker-compose` is on `PATH`, Quiver detects and uses the standalone binary automatically. It passes the selected Quiver Docker context through `DOCKER_CONTEXT` for that fallback.

### Why `quiver image pull base` currently fails

Built-in profile names resolve to local-style tags:

| Profile | Default tag |
| --- | --- |
| `base` | `quiver-base:stable` |
| `web` | `quiver-web:stable` |
| `internal` | `quiver-internal:stable` |
| `external` | `quiver-external:stable` |
| `cloud` | `quiver-cloud:stable` |
| `full` | `quiver-full:stable` |

This repository has a release workflow but does **not** currently publish those tags to a public registry. Therefore `quiver image pull base` and a first `quiver start NAME` correctly fail until you either build the images locally or publish/pull a fully-qualified registry image.

## 2. Build images locally

Base builds are self-contained. The Dockerfiles download the BlackArch strap script and, on ARM64, bootstrap from the Arch Linux ARM rootfs. They require network access during the build but no Quiver environment variables, build arguments, or host-side rootfs download.

### 2.1 Choose the target platform

```bash
docker version --format '{{.Server.Arch}}'
```

- `amd64`/`x86_64` → `linux/amd64`
- `arm64`/`aarch64` → `linux/arm64`

`quiver image build` defaults to the Docker daemon's native platform. On an Apple-silicon/OrbStack host this is normally `linux/arm64`.

### 2.2 Build with Quiver (Buildx available)

From the repository root:

```bash
# Apple Silicon / ARM Docker daemon
uv run quiver image build base --platform linux/arm64
uv run quiver image build internal --platform linux/arm64
uv run quiver image build external --platform linux/arm64
uv run quiver image build cloud --platform linux/arm64

# Intel / AMD Docker daemon
uv run quiver image build base --platform linux/amd64
uv run quiver image build internal --platform linux/amd64
```

Build the base first. Then select the profile you need:

```bash
uv run quiver image build web
uv run quiver image build internal
uv run quiver image build external
uv run quiver image build cloud
uv run quiver image build full
```

The profile command uses the local `quiver-base:stable` as its parent and prints the resolved required/optional package report. `full` is the largest image.

### 2.3 Direct Docker fallback when Buildx is unavailable

If `uv run quiver doctor` still reports **Buildx** unavailable (neither `docker buildx` nor `docker-buildx` works), use your native architecture and direct Docker builds instead. This is appropriate for local native builds; it is not a replacement for a multi-platform Buildx pipeline.

**ARM64:**

```bash
docker build \
  --file images/base/Dockerfile.arm64 \
  --tag quiver-base:stable .
```

**AMD64:**

```bash
docker build \
  --file images/base/Dockerfile.amd64 \
  --tag quiver-base:stable .
```

Build a profile manually by resolving its package manifest for the platform and passing those values to Docker. Example for ARM64 `internal`:

```bash
PLATFORM=linux/arm64
PROFILE=internal
REQUIRED_PACKAGES="$(uv run python -c \
  "from quiver.core.images import load_package_report; print(' '.join(load_package_report('$PROFILE', '$PLATFORM').required))")"
OPTIONAL_PACKAGES="$(uv run python -c \
  "from quiver.core.images import load_package_report; print(' '.join(load_package_report('$PROFILE', '$PLATFORM').optional))")"

docker build \
  --file "images/profiles/$PROFILE/Dockerfile" \
  --build-arg "REQUIRED_PACKAGES=$REQUIRED_PACKAGES" \
  --build-arg "OPTIONAL_PACKAGES=$OPTIONAL_PACKAGES" \
  --tag "quiver-$PROFILE:stable" .
```

Use `PROFILE=web`, `PROFILE=internal`, `PROFILE=external`, `PROFILE=cloud`, or `PROFILE=full` for those profiles. Confirm the local image exists before starting an assessment:

```bash
docker image inspect quiver-base:stable
# or
docker image inspect quiver-internal:stable
```

## 3. Start and manage an assessment

After the local image exists, the command that previously failed will work without pulling:

```bash
# Creates ~/.quiver/workspaces/test/ and starts the local base image.
uv run quiver start test --detach

# Add comma-separated Arch packages to the disposable assessment container.
uv run quiver start test --packages nmap,jq --detach

# Start an assessment using a local profile explicitly.
uv run quiver start internal-demo --image quiver-internal:stable --detach

# Enter a recorded Fish shell running as your host UID/GID (omit --detach).
uv run quiver start internal-demo
```

If `test` already exists from a failed initial start, build `quiver-base:stable` and run `uv run quiver start test --detach`. To switch that existing assessment to another image, use `--reconfigure`:

```bash
uv run quiver start test --image quiver-internal:stable --reconfigure --detach
```

Custom packages are persisted in the assessment configuration and installed with `pacman -Sy --needed` whenever Quiver creates its disposable primary container. Add packages to an existing assessment with `quiver edit NAME --packages pkg1,pkg2 --restart`; the option appends unique names rather than removing existing ones. Package availability remains architecture- and repository-dependent.

Useful lifecycle commands:

```bash
uv run quiver list
uv run quiver status test
uv run quiver stop test                 # removes the container, keeps workspace data
uv run quiver destroy test --yes        # removes Quiver-managed resources and workspace
uv run quiver workspace fix-perms test  # normalize workspace ownership where supported
```

Use a full registry reference when you publish images yourself:

```bash
uv run quiver start client-a \
  --image registry.example.net/security/quiver-internal:v0.1.0 \
  --detach
```

Quiver treats an image argument containing `:` or `/` as a registry/image reference. Plain names such as `internal` are built-in profile names.

## 4. Workspace and configuration

By default workspaces are under `~/.quiver/workspaces/<assessment>/`. Use an alternate state root for testing or isolation:

```bash
uv run quiver --root "$PWD/.quiver-state" start demo --detach
```

Edit a saved configuration safely:

```bash
uv run quiver edit demo                         # opens $VISUAL, then $EDITOR
uv run quiver edit demo --set docker.privileged=true
uv run quiver edit demo --publish 127.0.0.1:8443:8443
uv run quiver edit demo --set gui.enabled=false
```

Creation options do not modify an existing assessment unless `--reconfigure` is supplied. Use `quiver edit` for deliberate configuration changes.

### Host ownership and dotfiles

Interactive `quiver shell` and `quiver exec` commands run as the invoking host UID/GID, so files they create in the bind-mounted workspace remain editable on the host. Quiver creates a matching in-container user and grants it passwordless `sudo`, so use `sudo <command>` whenever container root is required. Runtime services stay root-owned while the container runs; `quiver stop` performs a final ownership repair. Use `quiver workspace fix-perms NAME` if a previous image left root-owned workspace files.

Quiver directly binds `~/.quiver/dotfiles` into every interactive shell as read-only `~/.config` (or `<state-root>/dotfiles` when `--root` is supplied). The directory is created on the first assessment start. Put your configuration there:

```bash
mkdir -p ~/.quiver/dotfiles/{fish,nvim,tmux}
$EDITOR ~/.quiver/dotfiles/fish/config.fish
$EDITOR ~/.quiver/dotfiles/starship.toml
```

The image provides Fish as the default shell plus tmux, Starship, zoxide, eza, and Neovim. The bind mount is intentionally read-only: edit dotfiles on the host; new shells see the changes immediately.

## 5. VPN, GUI, services, and audit

### VPN

```bash
uv run quiver start vpn-demo \
  --image quiver-internal:stable \
  --vpn ./client.ovpn \
  --vpn-credentials ./auth.txt \
  --vpn-type auto \
  --detach
```

Quiver copies VPN material into the assessment workspace on first creation. VPN requires Linux-container TUN support and applies a fail-closed kill switch. Check `uv run quiver doctor` before relying on it.

### GUI

```bash
uv run quiver gui internal-demo
uv run quiver gui internal-demo --no-open
```

The GUI is unauthenticated noVNC bound to loopback. `quiver gui NAME` reports the URL and opens it with the host default browser unless `--no-open` is supplied. The base image includes Firefox for in-container web assessment. Do not expose the port publicly.

### Services

```bash
uv run quiver service list internal-demo
uv run quiver service up internal-demo bloodhound-ce
uv run quiver service status internal-demo
uv run quiver service logs internal-demo bloodhound-ce --follow
uv run quiver service down internal-demo bloodhound-ce
```

The BloodHound Compose service needs Docker Compose v2. Its UI port is selected once, persisted in the assessment service environment file, and bound to `127.0.0.1`.

### Recorded terminal work

```bash
uv run quiver shell internal-demo
uv run quiver shell internal-demo --asciinema
uv run quiver exec internal-demo -- nmap -sV 10.10.10.5
uv run quiver logs internal-demo
uv run quiver replay internal-demo <session-id>
```

Audit metadata and recordings live under the assessment workspace's `.logs/` directory.

## 6. Troubleshooting

| Symptom | Resolution |
| --- | --- |
| `pull access denied for quiver-base` | Build `quiver-base:stable` locally, or specify a fully-qualified image reference that you can pull. No public Quiver registry is configured by this repository. |
| Base image build fails while downloading bootstrap files | Confirm the build has network access to BlackArch and Arch Linux ARM mirrors, then retry. |
| Buildx unavailable in `quiver doctor` | Ensure either `docker buildx` or `docker-buildx` is on `PATH`, or use the direct native `docker build` fallback above. |
| Compose unavailable in `quiver doctor` | Ensure either `docker compose` or `docker-compose` is on `PATH` before using Compose services such as BloodHound CE. |
| Existing assessment rejects `--image` | Use `--reconfigure`, edit the config, or destroy/recreate the assessment. |
| VPN start fails | Run `quiver doctor`; confirm Linux containers, TUN capability, a valid full-tunnel profile, and VPN endpoint reachability. |
| Image/profile build fails during package installation | Review the required/optional package report. Required packages fail the build; unavailable optional packages are logged and skipped. |

## 7. Publish images for a team (optional)

The `release-images` GitHub Actions workflow builds both architectures and publishes `base`, `web`, `internal`, `external`, `cloud`, and `full` profiles. Trigger it manually with:

- a repository such as `ghcr.io/<owner>/quiver`;
- a version and date tag;
- network access for the base-image bootstrap downloads.

It publishes architecture-specific tags and multi-platform `<version>`, `<date>`, and `stable` manifests. Team members can then use a fully-qualified reference, for example:

```bash
uv run quiver start team-demo --image ghcr.io/<owner>/quiver-internal:v0.1.0 --detach
```
