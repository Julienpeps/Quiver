# Desktop environment assessment for TigerVNC + noVNC

**Decision:** retain **Xfce on X11** for Quiver v1, on both amd64 and arm64. Do not replace it with MATE, LXQt, or Openbox, and explicitly do **not** use Hyprland (or another Wayland compositor) behind the present TigerVNC/noVNC architecture.

This is a source-cited assessment focused on a browser-mediated remote desktop, not a claim that Xfce is universally the best Linux desktop. “Recommendation” labels below are engineering judgments; all other material statements are sourced facts or observations of this repository.

## Executive recommendation

### Recommended v1 direction — **rule in Xfce/X11; keep the current setup**

**Recommendation.** Keep `xfce4-session` as Quiver's desktop session and retain the current Xvnc → websockify → noVNC path. Xfce has the best balance here: it is a complete X11 desktop with a visible panel, application menu/launchers, task buttons and workspace switcher, while still being comparatively modest; importantly, these daily operations have mouse-driven paths and do not require a Super/Meta or Alt chord. Xfce's own panel documentation identifies application launchers, panel menus, window buttons, and workspace switching as panel functions. [Xfce Panel](https://docs.xfce.org/xfce/xfce4-panel/start)

This direction also has **zero production-code changes**: Quiver already installs `xfce4` and `xfce4-terminal` in both base-image Dockerfiles and starts `DISPLAY=:1 xfce4-session` in `images/common/gui/quiver-gui`. The same script starts Xvnc with `-AcceptSetDesktopSize -SendCutText=on -AcceptCutText=on`; the CLI URL selects noVNC `resize=remote`. (Repository inspection: `images/base/Dockerfile.amd64`, `images/base/Dockerfile.arm64`, `images/common/gui/quiver-gui`, `src/quiver/core/gui.py`.)

**Why this satisfies the browser constraint.** noVNC is an HTML VNC client that supports modern browsers, scaling/clipping/desktop resizing, Unicode clipboard copy/paste, and mouse buttons. [noVNC README](https://github.com/novnc/noVNC/blob/master/README.md) Its stock control bar also exposes clickable Ctrl, Alt, Windows/Super, Tab, Esc and Ctrl+Alt+Del controls, plus a clipboard panel and remote-resize setting. [noVNC `vnc.html`](https://github.com/novnc/noVNC/blob/master/vnc.html) Those controls are a recovery path, **not** a reason to make a desktop whose normal launcher/window-management workflow depends on host-interceptable chords.

### Explicit v1 exclusions

* **Hyprland and other Wayland compositor sessions: rule out.** TigerVNC `Xvnc` is an X server with a virtual X screen; X applications see a normal X display and VNC viewers access that display. [TigerVNC Xvnc manual](https://tigervnc.org/doc/Xvnc.html) Starting a Wayland compositor in this Xvnc display is therefore not the native session model. Hyprland itself warns that it is not a full user-friendly desktop environment but a set of tools for constructing one. [Hyprland installation documentation](https://wiki.hypr.land/Getting-Started/Installation/) Its normal workflow is binding-centric, usually Meta (“Super”) based, and would additionally require choosing and integrating a Wayland remote-desktop/capture server, clipboard service, panel/launcher, input/session plumbing, and an amd64/arm64 support contract. That is a new architecture, not a desktop swap. **Recommendation:** defer Wayland to a separately designed v2 with a Wayland-native remote protocol/server and browser-client validation; do not nest or force it through Xvnc for v1.
* **Openbox alone: rule out as the default.** Openbox is correctly described by Arch as a highly configurable, lightweight **X11 window manager**—not a complete desktop. [Arch package metadata](https://archlinux.org/packages/extra/x86_64/openbox/) A usable Quiver image would have to compose a panel/menu, launcher, session/autostart behavior, file manager, settings, and clipboard policy. That increases configuration and test surface precisely where browser interaction must be dependable. It is an acceptable emergency/minimal profile only if product requirements later prioritize image size over integrated desktop usability.

## What the existing architecture guarantees—and what it does not

### Sourced transport facts

| Concern | Fact and evidence | Implication |
|---|---|---|
| Display model | Xvnc is both an X server with a virtual screen and a VNC server; applications treat it as an ordinary X display. [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html) | Choose an X11 session for the direct, low-risk v1 path. |
| Keyboard/pointer | Xvnc accepts client key press/release and pointer events by default. [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html) | Ordinary focused typing, clicks, drags, and window controls are transport-supported. Browser/OS-reserved shortcuts remain outside VNC's control. |
| Clipboard | TigerVNC documents `AcceptCutText` (client→server) and `SendCutText` (server→client), both default-on. [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html) noVNC advertises Unicode clipboard copy/paste and its stock UI provides a clipboard textarea. [noVNC README](https://github.com/novnc/noVNC/blob/master/README.md); [UI](https://github.com/novnc/noVNC/blob/master/vnc.html) | Text clipboard can work in both directions, but it is an end-to-end integration behavior, not a desktop-environment feature; test it. |
| Resize | Xvnc's `AcceptSetDesktopSize` accepts remote desktop-size requests. [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html) noVNC documents `resize=remote`, `scale`, and `off`. [noVNC embedding docs](https://github.com/novnc/noVNC/blob/master/docs/EMBEDDING.md) | Quiver's current flags/query are correct for a resizable X desktop. Confirm the selected session redraws/panel layout correctly after each resize. |
| Browser controls | noVNC offers clickable modifier keys and Ctrl+Alt+Del; its UI also exposes clipboard and scaling mode. [noVNC `vnc.html`](https://github.com/novnc/noVNC/blob/master/vnc.html) | Use only as an escape hatch for an unavoidable chord. Do not define a core workflow that requires it. |

### Constraint interpretation (recommendation)

Browsers and host operating systems may reserve or act on Super/Meta, Alt, browser tab/window, and security-related combinations before the noVNC canvas receives them. That behavior varies by browser, OS, desktop, keyboard layout, extensions, and fullscreen state; it must not be “solved” by selecting a window manager. The robust product design is:

1. a click-first launcher and visible task/panel controls;
2. ordinary text shortcuts (for example, terminal/application shortcuts) as enhancements, not the sole path;
3. a documented noVNC extra-keys fallback for the rare required modifier sequence;
4. no mandatory `Super+…`, `Alt+…`, or compositor-global binding for launch/focus/workspace recovery.

This makes Xfce's panel a better fit than a bare or tiling/binding-centric manager even if noVNC can synthesize modifiers.

## Candidates compared

Ratings are **recommendations for Quiver's stated remote-browser constraint**, not upstream quality rankings. “Architecture availability” means confidence from the current image design and authoritative package metadata, not a promise that a future repository snapshot will retain a package.

| Candidate | Complete, mouse-first recovery surface | Keyboard / global-shortcut exposure | Clipboard and resize in Xvnc/noVNC | amd64 + arm64 packaging / operational impact | v1 decision |
|---|---|---|---|---|---|
| **Xfce (X11)** | Strong. Xfce Panel has application launchers, menu, window buttons and workspace switcher. [Xfce](https://docs.xfce.org/xfce/xfce4-panel/start) | Configurable application shortcuts exist, so unsafe defaults can be avoided or changed. [Xfce keyboard settings](https://docs.xfce.org/xfce/xfce4-settings/keyboard) Mouse paths do not depend on them. | Native fit: Xfce runs on Xvnc. Clipboard/resize are the TigerVNC/noVNC transport contract, not unique to Xfce. | Already installed and launched in Quiver's amd64 Arch and arm64 Arch Linux ARM images (repository inspection). Arch's x86_64 Xfce group contains `xfce4-panel`, `xfce4-session`, `xfwm4`, `xfdesktop`, and related components. [Arch group](https://archlinux.org/groups/x86_64/xfce4/) | **Rule in / retain.** Lowest migration and test risk. |
| **MATE (X11)** | Good. The official Arch MATE group contains `mate-panel`, `mate-session-manager`, Caja, settings daemon, and Marco (default WM). [Arch group](https://archlinux.org/groups/x86_64/mate/) | Complete desktop offers mouse menu/panel paths, but it brings a second full desktop/session stack and needs a fresh shortcut audit. | Native Xvnc fit; transport behavior is equivalent in principle, but must be tested with MATE session processes. | Would replace existing packages/start command and must be package-availability tested on Arch Linux ARM. More moving parts than keeping Xfce. | **Rule out for v1.** Viable fallback if a product need specifically favors MATE; no identified browser advantage over Xfce. |
| **LXQt (X11 session with Openbox)** | Good panel/launcher/file-manager surface. Arch's LXQt group includes `lxqt-panel`, `lxqt-runner`, `pcmanfm-qt`, `lxqt-session`, and Openbox. [Arch group](https://archlinux.org/groups/x86_64/lxqt/) | The same group explicitly includes `lxqt-globalkeys`, a daemon/library for global keyboard shortcuts. [Arch group](https://archlinux.org/groups/x86_64/lxqt/) That is configurable but deserves particular audit under browser delivery. | Native Xvnc fit; no intrinsic clipboard/resize advantage over current Xfce. | Requires a switch to LXQt packages/start command and validation on both repositories. Its modular design offers no material v1 payoff sufficient to offset that change. | **Rule out for v1.** Candidate for a future resource-focused profile after empirical size/performance measurement. |
| **Openbox (X11 WM only)** | Weak alone. A WM does not supply the complete visible panel/launcher/session recovery UX Quiver needs. Arch calls it lightweight and highly configurable. [Arch package](https://archlinux.org/packages/extra/x86_64/openbox/) | Configurability is powerful but shifts launcher/window/workspace access into custom XML bindings and auxiliary tools—exactly the remote usability risk to avoid. | Native Xvnc fit, but clipboard manager/session integration must be explicitly assembled and tested. | Small package, but the apparent size win must be measured after adding panel, menu, settings and clipboard dependencies for a usable desktop. | **Rule out as default.** Consider only a deliberate minimal image profile. |
| **Hyprland (Wayland compositor)** | Not a complete DE according to upstream. [Hyprland docs](https://wiki.hypr.land/Getting-Started/Installation/) | Binding-centered configuration is a poor default for browser-hosted sessions; host capture is more consequential than in panel-first desktops. | Not a direct Xvnc session. Requires a new Wayland-native remote architecture rather than using TigerVNC Xvnc. | Requires a new cross-architecture build/runtime contract plus a remote server and UI composition. | **Rule out for v1 architecture.** |

### Detailed assessment by user interaction

#### Keyboard input

* **Sourced fact:** noVNC focuses its canvas on click/touch and creates a keyboard handler for it; the code also has explicit modifier controls in the stock UI. [noVNC RFB implementation](https://github.com/novnc/noVNC/blob/master/core/rfb.js); [noVNC UI](https://github.com/novnc/noVNC/blob/master/vnc.html)
* **Recommendation:** Xfce is suitable because a user can open the panel menu with a pointer, click a launcher, and click an existing window rather than needing a window-manager chord. Keep conventional in-app shortcuts (`Ctrl+C`, `Ctrl+V`, terminal editing, etc.) but make them nonexclusive. Avoid documenting `Alt+F2`, `Alt+Tab`, or `Super` as required recovery paths even if they work in one browser.
* **Recommendation:** define a manual test for non-US layouts, Caps Lock/Num Lock, `Ctrl`, `Alt`, `Super`, `Tab`, Escape and Ctrl+Alt+Del. TigerVNC documents `RawKeyboard` and `RemapKeys` options if a proven layout issue requires server-side remediation, but do not enable either speculatively. [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html)

#### Clipboard

* **Sourced fact:** the current Quiver Xvnc command explicitly enables `-SendCutText=on` and `-AcceptCutText=on`; these map to TigerVNC's documented server→client and client→server text clipboard parameters. [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html)
* **Recommendation:** retain those flags. Validate Xfce terminal copy/paste in both directions using ASCII, Unicode, multiline content, and a clipboard-panel fallback. Do not claim arbitrary rich data/files work: the v1 contract should be **text clipboard**, since that is what the current server flags and noVNC UI clearly expose.

#### Resize and panel layout

* **Sourced fact:** noVNC's remote resize sends an extended desktop-size request only when the server advertises support; Xvnc documents `AcceptSetDesktopSize`. [noVNC RFB implementation](https://github.com/novnc/noVNC/blob/master/core/rfb.js); [TigerVNC Xvnc](https://tigervnc.org/doc/Xvnc.html)
* **Recommendation:** retain `resize=remote`, test at a wide desktop, narrow browser, fullscreen, and reconnect. Check that Xfce panel remains visible/clickable, desktop background redraws, and a maximized terminal gets the new usable geometry. If remote resize is unavailable in a client/server combination, noVNC's documented `scale` and clip modes are the fallback—not a rationale to replace Xfce. [noVNC embedding docs](https://github.com/novnc/noVNC/blob/master/docs/EMBEDDING.md)

#### Panel, application launching and window recovery

* **Sourced fact:** Xfce describes Panel components for applications menu, launcher, window buttons, window menu, show-desktop, and workspace switching. [Xfce Panel](https://docs.xfce.org/xfce/xfce4-panel/start)
* **Recommendation:** preserve the default visible panel and ensure it includes an Applications Menu and Window Buttons. Add a visible launcher for the audited terminal only if the standard menu path is insufficient. Do not hide the panel, make it autohide-only, or replace it with a Super-key launcher in the default remote profile.

## Architecture and packaging evidence

1. **Current concrete baseline:** `images/base/Dockerfile.amd64` uses `archlinux:base-devel` and installs `novnc`, `tigervnc`, `websockify`, `xfce4`, and `xfce4-terminal`. `images/base/Dockerfile.arm64` bootstraps the Arch Linux ARM aarch64 rootfs and installs `tigervnc`, `xfce4`, and `xfce4-terminal`, uses pip for websockify, and clones noVNC. This is direct repository inspection, not an assertion that Arch and Arch Linux ARM repositories are identical.
2. **Upstream package corroboration:** Arch's official x86_64 Xfce group lists the session manager, panel, WM, desktop manager and terminal. [Arch Xfce group](https://archlinux.org/groups/x86_64/xfce4/) Arch officially lists comparable MATE and LXQt group contents and Openbox's X11-WM role. [MATE](https://archlinux.org/groups/x86_64/mate/); [LXQt](https://archlinux.org/groups/x86_64/lxqt/); [Openbox](https://archlinux.org/packages/extra/x86_64/openbox/)
3. **Recommendation:** do not infer arm64 availability from an x86_64 Arch package page. The current ARM Dockerfile is positive evidence that its required Xfce packages resolve today; lock this in with an arm64 image build test and `pacman -Qi xfce4 xfce4-terminal tigervnc` in CI/release validation. If evaluating a replacement, make that exact package-resolution test a prerequisite.

## Implementation checklist

### Chosen direction: retain Xfce (no desktop migration)

No production files need changing for this recommendation. Keep this operational shape:

```bash
# Existing Quiver pattern; shown for validation context, not a requested code edit.
Xvnc :1 -geometry "$geometry" -localhost yes \
  -AcceptSetDesktopSize -SendCutText=on -AcceptCutText=on &
DISPLAY=:1 xfce4-session &
DISPLAY=:1 xfce4-terminal --command /usr/local/bin/quiver-record-shell &
websockify --web /opt/novnc "$port" localhost:5901 &
```

**Implementation/validation checklist**

- [ ] Keep `xfce4`, `xfce4-terminal`, TigerVNC and noVNC in both Dockerfiles; do not add MATE/LXQt/Openbox/Hyprland packages to the base profile merely for comparison.
- [ ] Keep `DISPLAY=:1 xfce4-session`, `-AcceptSetDesktopSize`, `-SendCutText=on`, `-AcceptCutText=on`, and the `resize=remote` URL behavior.
- [ ] Start each architecture image and assert the processes exist: `Xvnc`, `xfce4-session`, `xfce4-panel`, `xfwm4`, `websockify` and the recorded terminal.
- [ ] Verify initial 1600×1000 (or configured) geometry and resize through browser widths/heights; inspect that Xfce panel and a maximized terminal relayout without unusable clipping.
- [ ] In Chrome/Chromium and Firefox on the supported host platforms, click panel menu → launch terminal; click task buttons to focus/recover windows; invoke Show Desktop/Workspace Switcher by mouse if enabled. Demonstrate success without Super, Alt, or global WM shortcuts.
- [ ] Focus the canvas and test typing, punctuation on the supported keyboard layouts, selection, pointer drag, wheel, middle/right click, Escape and Tab. Test modifier delivery separately, but record it as a fallback capability rather than a required workflow.
- [ ] Test text clipboard desktop→browser and browser→desktop with ASCII, non-ASCII and multiline strings. Also test the noVNC clipboard control-bar textarea as the fallback path.
- [ ] Build/run both `linux/amd64` and `linux/arm64`; record `pacman -Qi xfce4 xfce4-terminal tigervnc` and verify `/opt/novnc` exists. This catches repository drift, including the intentionally different arm64 noVNC installation method.
- [ ] Reconnect and restart the GUI supervisor process; confirm no stale X socket/session or missing panel blocks recovery.

### If product later requests a desktop change

Treat it as a migration, not a package rename:

1. Add an explicit desktop/profile configuration model rather than silently changing `desktop: xfce`.
2. Change both Dockerfile package lists and `quiver-gui` session/terminal commands together.
3. Create a visual, mouse-first panel/launcher/window-switching acceptance test before enabling the profile.
4. Run the checklist above on amd64 and arm64 and compare installed size, cold-start time and steady-state RAM with the existing Xfce image.
5. Reject any candidate whose only reliable launch/recovery path depends on a captured Super/Alt/global shortcut.

## Risks and mitigations

| Severity | Risk | Mitigation |
|---|---|---|
| High | A browser/host reserves a key chord before noVNC receives it. | Keep panel/menu/task-button workflows fully mouse-accessible; treat noVNC extra modifier buttons as fallback; test target browsers/hosts. |
| High | ARM package/repository drift causes an image build failure. | Build multi-arch in CI and query installed packages; do not infer aarch64 support from x86_64 package metadata. |
| Medium | Clipboard works only one direction or mishandles Unicode. | Retain explicit TigerVNC flags and test end-to-end text in both directions plus noVNC clipboard panel. |
| Medium | Resize produces a panel/layout issue at unusual viewport sizes. | Exercise remote resize and fallback scale/clip modes across a viewport matrix. |
| Medium | Replacing Xfce with a bare WM produces an apparently working terminal but a poor recoverability UX. | Require visible, click-first launcher/window/task UI in a testable profile before approving a replacement. |
| High (if pursued prematurely) | Wayland migration introduces a second remote-display architecture and no longer uses the documented Xvnc display model. | Defer to a separate architecture decision with a Wayland-native remote server, cross-arch packages, input/clipboard/resize test plan, and browser proof-of-concept. |

## Source list

Primary/official sources used:

- TigerVNC, **Xvnc manual** — https://tigervnc.org/doc/Xvnc.html
- noVNC, **README/features** — https://github.com/novnc/noVNC/blob/master/README.md
- noVNC, **embedding/settings documentation** — https://github.com/novnc/noVNC/blob/master/docs/EMBEDDING.md
- noVNC, **stock UI source** — https://github.com/novnc/noVNC/blob/master/vnc.html
- noVNC, **RFB implementation** — https://github.com/novnc/noVNC/blob/master/core/rfb.js
- Xfce, **Panel documentation** — https://docs.xfce.org/xfce/xfce4-panel/start
- Xfce, **Keyboard settings documentation** — https://docs.xfce.org/xfce/xfce4-settings/keyboard
- Arch Linux official metadata, **Xfce group** — https://archlinux.org/groups/x86_64/xfce4/
- Arch Linux official metadata, **MATE group** — https://archlinux.org/groups/x86_64/mate/
- Arch Linux official metadata, **LXQt group** — https://archlinux.org/groups/x86_64/lxqt/
- Arch Linux official metadata, **Openbox package** — https://archlinux.org/packages/extra/x86_64/openbox/
- Hyprland, **installation documentation** — https://wiki.hypr.land/Getting-Started/Installation/

## Conclusion

**Recommendation:** retain the present Xfce-on-Xvnc/noVNC implementation. It is already cross-architecture in Quiver, directly matches TigerVNC's X-server model, and provides visible mouse-first launch/window recovery paths that avoid making browser-captured Super/Alt/global shortcuts a normal operational dependency. MATE and LXQt are technically plausible X11 alternatives but provide no demonstrated v1 browser-interaction benefit that justifies migration. Openbox alone is too incomplete, and Hyprland/Wayland is an explicitly out-of-scope remote-desktop architecture change for v1.
