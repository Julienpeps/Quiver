# Tool package validation (Arch Linux)

## Scope and method

The three supplied lists contain 581 entries (358 distinct names): `ad_3.1.18_amd64.csv`, `osint_3.1.18_amd64.csv`, and `web_3.1.18_amd64.csv`. Validation is against **repository database snapshots**, rather than package-search guesses:

* official Arch Linux **x86_64** `core` and `extra`;
* Arch Linux ARM **aarch64** `core` and `extra`; and
* BlackArch **x86_64**.

`any` packages are usable on both architectures. A matching official package in both the Arch and ALARM snapshots is marked **yes/yes** below. BlackArch packages are an x86_64/amd64 option only: do **not** put a BlackArch package name in an arm64 manifest.

Repository references: [Arch package database format](https://man.archlinux.org/man/alpm-db.5), [Arch official repositories](https://archlinux.org/packages/), [Arch Linux ARM repositories](https://archlinuxarm.org/packages/), and [BlackArch repository](https://blackarch.org/).

## Recommended official packages

These are the low-risk package names to use when the requested executable/tool is the corresponding upstream project. They are available from official repositories for **amd64 and arm64** (core/extra or `any`):

| Requested tool(s) | Arch package | amd64 | arm64 | Notes |
|---|---|---:|---:|---|
| asciinema | `asciinema` | yes | yes | |
| crunch | `crunch` | yes | yes | |
| dtrx | `dtrx` | yes | yes | |
| exiftool | `perl-image-exiftool` | yes | yes | command is `exiftool` |
| fcrackzip | `fcrackzip` | yes | yes | |
| firefox | `firefox` | yes | yes | |
| fping | `fping` | yes | yes | |
| fzf | `fzf` | yes | yes | |
| git-dumper | `git-dumper` | yes | yes | |
| gitleaks | `gitleaks` | yes | yes | |
| glow | `glow` | yes | yes | |
| gobuster | `gobuster` | yes | yes | |
| gron | `gron` | yes | yes | |
| hashcat | `hashcat` | yes | yes | GPU support is hardware/driver dependent |
| hping3 | `hping` | yes | yes | package is not named `hping3` |
| imagemagick | `imagemagick` | yes | yes | |
| iptables | `iptables` | yes | yes | |
| john | `john` | yes | yes | |
| keepassxc | `keepassxc` | yes | yes | |
| ldapsearch | `openldap` | yes | yes | provides `ldapsearch` |
| libmspack | `libmspack` | yes | yes | library, not a standalone scanner |
| mariadb-client | `mariadb-clients` | yes | yes | |
| masscan | `masscan` | yes | yes | |
| mdcat | `mdcat` | yes | yes | |
| metasploit | `metasploit` | yes | yes | |
| mitmproxy | `mitmproxy` | yes | yes | |
| naabu | `naabu` | yes | yes | |
| nbtscan | `nbtscan` | yes | yes | |
| neo4j | `neo4j` | yes | yes | |
| neovim | `neovim` | yes | yes | |
| nmap | `nmap` | yes | yes | |
| nuclei | `nuclei` | yes | yes | |
| openvpn | `openvpn` | yes | yes | |
| pass | `pass` | yes | yes | The CSV link incorrectly points to hashcat. |
| pdfcrack | `pdfcrack` | yes | yes | |
| powershell | `powershell` | yes | yes | |
| proxychains | `proxychains-ng` | yes | yes | |
| redis-tools | `redis` | yes | yes | provides `redis-cli` and related tools |
| remmina | `remmina` | yes | yes | |
| rdesktop | `rdesktop` | yes | yes | |
| rlwrap | `rlwrap` | yes | yes | |
| rsync | `rsync` | yes | yes | |
| rustscan | `rustscan` | yes | yes | |
| semgrep | `semgrep` | yes | yes | |
| seclists | `seclists` | yes | yes | data files, not a command |
| smbclient | `smbclient` | yes | yes | |
| sqlmap | `sqlmap` | yes | yes | |
| ssh-audit | `ssh-audit` | yes | yes | |
| sshuttle | `sshuttle` | yes | yes | |
| sslscan | `sslscan` | yes | yes | |
| swaks | `swaks` | yes | yes | |
| tailscale | `tailscale` | yes | yes | |
| tcpdump | `tcpdump` | yes | yes | |
| testssl | `testssl.sh` | yes | yes | command is commonly `testssl.sh` |
| tig | `tig` | yes | yes | |
| tor | `tor` | yes | yes | |
| traceroute | `traceroute` | yes | yes | |
| trufflehog | `trufflehog` | yes | yes | |
| tshark / wireshark | `wireshark-cli` / `wireshark-qt` | yes | yes | choose CLI vs GUI |
| upx | `upx` | yes | yes | |
| wafw00f | `wafw00f` | yes | yes | |
| whatweb | `whatweb` | yes | yes | |
| whois | `whois` | yes | yes | |
| wireguard | `wireguard-tools` | yes | yes | userspace tools; kernel support is separate |
| wpscan | `wpscan` | yes | yes | |
| xsser | `xsser` | yes | yes | |
| yarn | `yarn` | yes | yes | |
| youtube-dl / youtubedl | `youtube-dl` | yes | yes | legacy upstream; prefer `yt-dlp` |
| yt-dlp | `yt-dlp` | yes | yes | |

## BlackArch-only handling and unavailable requests

Most of the remaining names in the CSVs are niche offensive-security, one-off Python/Go/Ruby repositories, data/rule sets, GUI products, or scripts—not official Arch/ALARM core/extra packages. Examples include AD tooling (`bloodyAD`, `certipy`, `coercer`, `donpapi`, `impacket`, `netexec`, `pypykatz`, `responder`), ProjectDiscovery/tomnomnom-style utilities not listed above, and the many proof-of-concept repositories.

Where a name is present in the **BlackArch x86_64** snapshot, its package is an amd64-only candidate. Treat it as **unavailable for arm64**. Do not silently substitute it into an ARM image: use the upstream source/release only after verifying it builds on aarch64, or choose an official cross-architecture alternative from the table.

For names absent from all three snapshots, the correct result is **no repository package**. Install only with the upstream-supported mechanism (often `pipx`, a language package manager, or a pinned source build) and record the executable name separately. This applies especially to repositories whose CSV name contains spaces or suffixes such as `rules`, `PHP filter chain generator`, `Powerview.py`, `nmap-parse-ouptut` (also misspelled), and `NSAKEY rules`: these are not pacman package requests.

### Practical alternatives

* **`youtube-dl`/`youtubedl` → `yt-dlp`**: actively maintained compatible replacement.
* **`tshark` → `wireshark-cli`**: use `wireshark-qt` only where a desktop GUI is wanted.
* **`redis-tools` → `redis`**, **`ldapsearch` → `openldap`**, **`smbclient` → `smbclient`**: request the package that provides the command, not a Debian-style component name.
* For unpackageable web recon tools, prefer the supported official tools already available on both architectures (`nmap`, `masscan`, `naabu`, `nuclei`, `sqlmap`, `whatweb`, `wafw00f`, `gobuster`) before adding an unpinned source build.

## Manifest guidance

Use exact package names from the table, with normal pacman installation:

```Dockerfile
RUN pacman -Syu --noconfirm --needed nmap nuclei sqlmap wireshark-cli \
    seclists openldap mariadb-clients wireguard-tools yt-dlp \
 && pacman -Scc --noconfirm
```

Keep BlackArch packages behind an `amd64` platform gate. For an unverified upstream tool, prefer an explicit, pinned install such as `pipx install '<project>==<version>'`, and test the resulting executable on both target architectures.

## Findings / residual risk

* **High:** BlackArch is not an arm64 source. Any unqualified BlackArch addition will break multi-architecture builds.
* **Medium:** CSV display names are frequently not package names; several are data sets, libraries, typoed names, or GUI/commercial tools. Installing by name without a package-db match is unreliable.
* **Medium:** Package availability is snapshot-time state; re-check the target repo database during image refreshes.
