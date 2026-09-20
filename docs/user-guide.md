# Quiver user guide

Quiver creates a disposable Docker container per assessment while keeping the assessment workspace on the host. The container is disposable; files in the assessment workspace persist across `stop` and the next `start`.

> **Authorization:** use Quiver only for systems and networks you are authorized to assess.

## 1. Prerequisites

```bash
uv sync --all-groups
uv run quiver doctor
```

You need a Docker daemon running Linux containers. The image-build command additionally requires Docker Buildx:

```bash
docker buildx version
```

`quiver doctor` reports this as **Buildx**. Install/enable the Buildx plugin for the Docker CLI you use, then rerun `doctor`.

### Why `quiver image pull base` currently fails

Built-in profile names resolve to local-style tags:

| Profile | Default tag |
| --- | --- |
| `base` | `quiver-base:stable` |
| `web` | `quiver-web:stable` |
| `internal` | `quiver-internal:stable` |
| `full` | `quiver-full:stable` |

This repository has a release workflow but does **not** currently publish those tags to a public registry. Therefore `quiver image pull base` and a first `quiver start NAME` correctly fail until you either build the images locally or publish/pull a fully-qualified registry image.

## 2. Build images locally

Quiver deliberately refuses to build its base image without verified provenance. The base image downloads the BlackArch strap script; ARM builds also consume an Arch Linux ARM rootfs. You must supply a trusted, pinned artifact URL and SHA-256 for each input.

Do **not** treat a checksum calculated from the same untrusted download as verification. Obtain the SHA-256 from a signed upstream release/checksum or another trusted channel, compare it before building, and record the URL and hash with your assessment build records. If an upstream only exposes mutable `latest` URLs, first copy the verified artifact to an immutable internal artifact store and use that URL.

### 2.1 Choose the target platform

```bash
docker version --format '{{.Server.Arch}}'
```

- `amd64`/`x86_64` → `linux/amd64`
- `arm64`/`aarch64` → `linux/arm64`

`quiver image build` defaults to the Docker daemon's native platform. On an Apple-silicon/OrbStack host this is normally `linux/arm64`.

### 2.2 Set verified provenance inputs

Set these in the shell that runs the build. The environment variable names include `QUIVER_` even though the CLI's error message displays the Docker build-argument names without that prefix.

```bash
# Required for every base build. Replace with an immutable, verified artifact.
export QUIVER_BLACKARCH_STRAP_URL='https://artifacts.example.net/blackarch/strap-<verified-version>.sh'
export QUIVER_BLACKARCH_STRAP_SHA256='<64-hex-character-sha256>'

# Required only for linux/arm64. Download the verified archive to the required path.
export QUIVER_ARCHLINUXARM_ROOTFS_SHA256='<64-hex-character-sha256>'
export ARCHLINUXARM_ROOTFS_URL='https://artifacts.example.net/archlinuxarm/ArchLinuxARM-aarch64-<verified-version>.tar.gz'

mkdir -p images/base/rootfs
curl --fail --location --retry 3 "$ARCHLINUXARM_ROOTFS_URL" \
  --output images/base/rootfs/archlinuxarm-aarch64.tar.gz
printf '%s  %s\n' "$QUIVER_ARCHLINUXARM_ROOTFS_SHA256" \
  images/base/rootfs/archlinuxarm-aarch64.tar.gz | shasum -a 256 -c -
```

For an amd64 build, omit the ARM rootfs download and `QUIVER_ARCHLINUXARM_ROOTFS_SHA256`.

### 2.3 Build with Quiver (Buildx available)

From the repository root:

```bash
# Apple Silicon / ARM Docker daemon
uv run quiver image build base --platform linux/arm64
uv run quiver image build internal --platform linux/arm64

# Intel / AMD Docker daemon
uv run quiver image build base --platform linux/amd64
uv run quiver image build internal --platform linux/amd64
```

Build the base first. Then select the profile you need:

```bash
uv run quiver image build web
uv run quiver image build internal
uv run quiver image build full
```

The profile command uses the local `quiver-base:stable` as its parent and prints the resolved required/optional package report. `full` is the largest image.

### 2.4 Direct Docker fallback when Buildx is unavailable

If `docker buildx version` fails, use your native architecture and direct Docker builds instead. This is appropriate for local native builds; it is not a replacement for a multi-platform Buildx pipeline.

**ARM64:**

```bash
# Prerequisites: export the three QUIVER_* values and download/verify the rootfs
# as described above.
docker build \
  --file images/base/Dockerfile.arm64 \
  --build-arg "BLACKARCH_STRAP_URL=$QUIVER_BLACKARCH_STRAP_URL" \
  --build-arg "BLACKARCH_STRAP_SHA256=$QUIVER_BLACKARCH_STRAP_SHA256" \
  --build-arg "ARCHLINUXARM_ROOTFS_SHA256=$QUIVER_ARCHLINUXARM_ROOTFS_SHA256" \
  --tag quiver-base:stable .
```

**AMD64:**

```bash
# Prerequisites: export QUIVER_BLACKARCH_STRAP_URL and
# QUIVER_BLACKARCH_STRAP_SHA256.
docker build \
  --file images/base/Dockerfile.amd64 \
  --build-arg "BLACKARCH_STRAP_URL=$QUIVER_BLACKARCH_STRAP_URL" \
  --build-arg "BLACKARCH_STRAP_SHA256=$QUIVER_BLACKARCH_STRAP_SHA256" \
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

Use `PROFILE=web` or `PROFILE=full` for those profiles. Confirm the local image exists before starting an assessment:

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

# Start an assessment using a local profile explicitly.
uv run quiver start internal-demo --image quiver-internal:stable --detach

# Enter a recorded root shell (omit --detach).
uv run quiver start internal-demo
```

If `test` already exists from a failed initial start, build `quiver-base:stable` and run `uv run quiver start test --detach`. To switch that existing assessment to another image, use `--reconfigure`:

```bash
uv run quiver start test --image quiver-internal:stable --reconfigure --detach
```

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

The GUI is noVNC bound to loopback. Quiver reports the URL and any generated per-assessment password; do not expose the port publicly without understanding the security consequences.

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
| Provenance-variable error | Export the matching `QUIVER_*` variables. On ARM64 also download and verify `images/base/rootfs/archlinuxarm-aarch64.tar.gz`. |
| `docker: unknown command: docker buildx` | Install/enable Docker Buildx, or use the direct native `docker build` fallback above. |
| `docker compose` unavailable | Install Docker Compose v2 before using Compose services such as BloodHound CE. |
| Existing assessment rejects `--image` | Use `--reconfigure`, edit the config, or destroy/recreate the assessment. |
| VPN start fails | Run `quiver doctor`; confirm Linux containers, TUN capability, a valid full-tunnel profile, and VPN endpoint reachability. |
| Image/profile build fails during package installation | Review the required/optional package report. Required packages fail the build; unavailable optional packages are logged and skipped. |

## 7. Publish images for a team (optional)

The `release-images` GitHub Actions workflow builds both architectures and publishes `base`, `web`, `internal`, and `full` profiles. Trigger it manually with:

- a repository such as `ghcr.io/<owner>/quiver`;
- a version and date tag;
- immutable, verified BlackArch strap URL/SHA-256;
- immutable, verified ARM rootfs URL/SHA-256.

It publishes architecture-specific tags and multi-platform `<version>`, `<date>`, and `stable` manifests. Team members can then use a fully-qualified reference, for example:

```bash
uv run quiver start team-demo --image ghcr.io/<owner>/quiver-internal:v0.1.0 --detach
```
