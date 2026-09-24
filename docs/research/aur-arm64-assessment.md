# AUR / paru assessment for ARM64

**Decision: do not make `paru` (or the AUR) a normal Quiver ARM64 package source.** Keep the official Arch Linux ARM packages as the product path. AUR can be a deliberately opt-in, separately built *experimental* source for a short allow-list, but it should not be run implicitly in image builds.

## Method and scope

On 2026-09-22 I de-duplicated the `arch.amd64.packages` entries in `packages/drafts/{internal,osint,web}.yaml` (165 names), then queried exact names through the [AUR RPC v5 multi-info endpoint](https://aur.archlinux.org/rpc/v5/info?arg[]=ffuf). The documented API returns exact package metadata; 64 names matched and 101 did not. For each match, I read the current first-party AUR `PKGBUILD` and used its `arch` declaration plus source/build/dependency inspection—not a search result—as the ARM judgement. The AUR describes itself as user-contributed build recipes, not a binary repository ([ArchWiki](https://wiki.archlinux.org/title/Arch_User_Repository)); `arch=any` therefore means the recipe claims architecture independence, **not** a tested ALARM binary.

This is a point-in-time screen, not a successful aarch64 build test. It is intentionally limited to names that the drafts currently put behind the amd64 gate. `docs/research/tool-package-validation.md` remains authoritative for the important prior result: BlackArch is x86_64-only and must not be used to fill an ARM manifest.

## Results

### Plausible aarch64 candidates

These exact AUR recipes declare `aarch64` or `any` and obtain source/build locally (rather than an obviously x86_64-only release). They are reasonable *candidates for a clean aarch64 build trial*, not approval to add to a manifest.

| Screen | draft candidates | Why |
|---|---|---|
| Explicit `aarch64` | [alterx](https://aur.archlinux.org/packages/alterx), [bloodhound](https://aur.archlinux.org/packages/bloodhound), [ffuf](https://aur.archlinux.org/packages/ffuf), [ligolo-ng](https://aur.archlinux.org/packages/ligolo-ng), [netdiscover](https://aur.archlinux.org/packages/netdiscover), [ngrok](https://aur.archlinux.org/packages/ngrok), [rusthound-ce](https://aur.archlinux.org/packages/rusthound-ce), [subfinder](https://aur.archlinux.org/packages/subfinder), [uncover](https://aur.archlinux.org/packages/uncover) | Recipe explicitly permits aarch64. Most compile Go/Rust/C sources; `ngrok` instead selects an upstream aarch64 binary, so it is runnable but less independently reproducible. |
| `any`, source/script recipe | [arjun](https://aur.archlinux.org/packages/arjun), [bqm](https://aur.archlinux.org/packages/bqm), [dirsearch](https://aur.archlinux.org/packages/dirsearch), [enum4linux-ng](https://aur.archlinux.org/packages/enum4linux-ng), [evil-winrm-py](https://aur.archlinux.org/packages/evil-winrm-py), [feroxbuster](https://aur.archlinux.org/packages/feroxbuster), [gau](https://aur.archlinux.org/packages/gau), [haiti](https://aur.archlinux.org/packages/haiti), [holehe](https://aur.archlinux.org/packages/holehe), [joomscan](https://aur.archlinux.org/packages/joomscan), [patator](https://aur.archlinux.org/packages/patator), [polenum](https://aur.archlinux.org/packages/polenum), [recon-ng](https://aur.archlinux.org/packages/recon-ng), [responder](https://aur.archlinux.org/packages/responder), [sherlock](https://aur.archlinux.org/packages/sherlock), [sliver](https://aur.archlinux.org/packages/sliver), [smbmap](https://aur.archlinux.org/packages/smbmap), [waybackurls](https://aur.archlinux.org/packages/waybackurls), [weevely](https://aur.archlinux.org/packages/weevely), [xsstrike](https://aur.archlinux.org/packages/xsstrike) | Python/Ruby/Perl scripts or a native build from checksummed source. Native programs marked `any` should still produce an aarch64 package in the isolated build. |

### Conditional rather than useful first targets

These recipes are architecture-permissive but pull an AUR dependency chain that must itself be resolved and built for aarch64: [maigret](https://aur.archlinux.org/packages/maigret) (`socid-extractor`), [spiderfoot](https://aur.archlinux.org/packages/spiderfoot) (multiple `python-*` AUR deps), and [wfuzz](https://aur.archlinux.org/packages/wfuzz) (`python-zombie-imp`). Treat them as whole dependency-closure investigations, not one-package installs.

Four apparent matches are **not ARM gaps**: [nuclei](https://aur.archlinux.org/packages/nuclei), [redis](https://aur.archlinux.org/packages/redis), [seclists](https://aur.archlinux.org/packages/seclists), and [wafw00f](https://aur.archlinux.org/packages/wafw00f) are already identified by `tool-package-validation.md` as official cross-architecture packages. Prefer those official packages.

### Do not use on ARM without maintaining a fork

The current AUR recipes explicitly restrict the package to x86_64/i686: `amass`, `amber`, `assetfinder`, `bloodyad`, `bruteforce-luks`, `crunch`, `dirb`, `dnsx`, `gf`, `gopherus`, `gowitness`, `httprobe`, `httpx`, `massdns`, `naabu`, `netexec`, `nfsshell`, `photon`, `pkcrack`, `samdump2`, `shuffledns`, `soapui`, `trufflehog`, `whatweb`, and `wuzz`. Some are source builds that might technically port, but overriding `arch` makes Quiver the packager and removes the evidence the question asks for. Do not do that merely through `paru`.

Also reject the three `any` matches as substitutes: `burpsuite` packages a vendor desktop JAR/distribution and needs runtime verification; `maltego` repackages a vendor Linux ZIP with no aarch64 proof; and `chisel` is the Chisel hardware-description-language package, not necessarily the intended tunnelling tool. They demonstrate why exact package-name matching alone is unsafe.

The remaining **101 of 165** draft names returned no exact AUR package. “No AUR match” is not an installation method; assess the upstream project and a pinned build independently. In particular, do not translate their BlackArch/amd64 draft placement into an ARM AUR assumption.

## Operational, security, and reproducibility trade-offs

* **What paru does / does not solve.** Paru is a pacman-wrapping AUR helper, explicitly “not an official tool”; upstream says to first establish that `makepkg` can build a failure ([paru README](https://github.com/Morganamilo/paru#debugging)). It offers useful review commands—`paru -G` (download) and `paru -Gp` (print PKGBUILD)—but it cannot make an x86-only recipe, non-ALARM dependency, or unmaintained upstream build on ARM.
* **Trust boundary.** AUR `PKGBUILD`s are unofficial and not thoroughly vetted; Arch specifically warns that malicious packages have existed and says to inspect all repository files, source URLs/checksums, patches, `.install` scripts and diffs on each update ([AUR security guidance](https://wiki.archlinux.org/title/Arch_User_Repository#Security)). Noninteractive Docker builds bypass meaningful human review, turning an upstream package update into arbitrary build-time code execution. AUR helpers are unsupported by definition ([ArchWiki FAQ](https://wiki.archlinux.org/title/Arch_User_Repository#What_is_the_difference_between_the_Arch_User_Repository_and_the_extra_repository?)).
* **Buildability and maintenance.** AUR packages assume `base-devel`; `makepkg -s` only resolves repo dependencies, not AUR dependency closures. AUR packages are the user's responsibility to update/rebuild when official library ABI changes ([AUR update guidance](https://wiki.archlinux.org/title/Arch_User_Repository#Updating_packages)). ARM native Rust/Go compilation also materially increases CI time and memory.
* **Reproducibility.** PKGBUILDs can pin checksums, and packages contain `.BUILDINFO` metadata, but reproducibility is weakened by mutable AUR Git heads, VCS dependencies, upstream downloads, compiler/base-image drift and a transitive AUR closure. Arch recommends a clean chroot and `namcap`; a successful package still needs functional testing ([package-build guidance](https://wiki.archlinux.org/title/Creating_packages#Using_pkgctl_to_build_in_a_clean_chroot_environment)). `makepkg.conf` also makes compiler flags and architecture part of the build environment ([makepkg.conf(5)](https://man.archlinux.org/man/makepkg.conf.5.en)).

## Recommended implementation policy (no manifest/Dockerfile change proposed)

1. **Do not install `paru` in production images or invoke it in Dockerfiles.** It expands the image build trust boundary and makes builds depend on live community packaging.
2. If product demand justifies one missing tool, choose from the explicit-aarch64/source list above, clone that AUR package-base at a reviewed commit, review every tracked file and the full dependency closure, and build it as an aarch64 CI artifact in a clean isolated environment. Run `namcap`, smoke-test the executable, record AUR commit, source checksums, build log, output package SHA-256 and `.BUILDINFO`.
3. Publish the reviewed binary to a Quiver-controlled, immutable artifact/repository; install that pinned artifact in a later image stage. Re-review every update and rebuild on official-library changes. For `ngrok`, additionally accept that the payload is a pinned upstream aarch64 binary rather than a source build.
4. Prefer an official ARM package or a pinned upstream-supported mechanism over an AUR helper. Promote only candidates that pass native ARM CI and have an explicit owner/update SLA; otherwise keep them absent from ARM profiles.

This retains the existing architecture gate, gains a controlled escape hatch for genuine gaps, and does not misrepresent a community recipe as official Arch Linux ARM support.
