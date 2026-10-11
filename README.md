# aurora-steam-native

Run Valve's native ARM64 Linux Steam client on Aurora Silicon machines (MacBook
Neo, J700) without a virtual machine.

The client's ARM64 binaries assume 4 KiB pages. The Aurora kernel runs with a
16 KiB base page size, so this project starts Steam through `userpages 4k`,
which asks the kernel to give Steam and all of its descendants (Steam Runtime,
Proton, FEX, games) a 4 KiB userspace page size.

## Requirements

- An Aurora kernel built with `CONFIG_ARM64_USER4K_EXPERIMENTAL=y`
  (aurora-silicon/linux `aurora-edge` and `aurora-wip`).
- `userpages`, built from `tools/aurora-userpages` in the same kernel tree and
  installed either on `PATH` or as `/usr/libexec/aurora/userpages`.
- `python3` and `gtk2` (Steam's UI modules link against GTK 2).
- A working Vulkan driver for the GPU. The wrapper keeps Steam's embedded
  browser on Zink (`MESA_LOADER_DRIVER_OVERRIDE=zink`, `GALLIUM_DRIVER=zink`,
  `LIBGL_KOPPER_DISABLE=true`); each can be overridden from the environment.

## Install

    ./install-steam-arm64.py

The installer downloads Valve's ARM64 public-beta client from Valve's CDN,
checks every archive against the size and SHA-256 in Valve's manifest, unpacks
it into `~/.local/share/Steam-ARM64`, and creates the usual `~/.steam/root`,
`~/.steam/steam` and `~/.steam/sdkarm64` links. It refuses to overwrite an
existing installation or a conflicting link. It also installs
`steam-native4k`, its launch loop and a desktop entry under `~/.local`; the
icon is copied from the freshly installed client.

To keep an existing client (found through `~/.steam/root`) and only install or
refresh the launcher, desktop entry and links:

    ./install-steam-arm64.py --launcher-only

Valve's top-level `steam.sh` starts the legacy x86 client, so it is not used.
The launch loop starts `steamrtarm64/steam` directly and repeats it only when
the client exits with status 42, which is the client's request to be restarted
after a self-update.

## Run

    steam-native4k

Diagnostics go to `~/.local/state/aurora-steam-native4k/console.log`. Stop the
client with `steam -shutdown` (or the Steam menu) rather than killing it.

## Proton

Windows games run through an ARM64 Proton build (for example GE-Proton's
`aarch64` release), which uses FEX for x86 code. See `proton/README.md` for a
Wine signal-handling fix that ARM64 Proton currently needs on CPUs that
implement SME without SVE.

## Licence

The launcher, installer and documentation are MIT (`LICENSES/MIT.txt`).
The patch to Wine is LGPL-2.1-or-later, matching the Wine source file
(`LICENSES/LGPL-2.1-or-later.txt`). Steam, Proton and their components are
the property of their respective owners and are not distributed here.
