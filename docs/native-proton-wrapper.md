# Native ARM64 Proton entry point

`proton/aurora-proton` is a small public entry point for a GE ARM64 compatibility tool. Install it next to that tool's existing `proton` script, `files/` directory and manifests. Its normal Proton arguments are passed through unchanged. It requires the published Aurora userpages launcher and runs the GE Python entry point under its4K execution mode. Set `AURORA_USERPAGES_LAUNCHER` to the installed public launcher if it is outside PATH and `/usr/libexec/aurora/userpages`.

The wrapper selects `PROTON_USE_ARM64=1` and the tool's native `WINESERVER`. It does not add a `WINELOADER` override: GE chooses the native Wine binary directly. It inherits the caller's remaining environment and driver selection. It does not change Steam's stored choices or prefixes, add graphics-library pins, replace FEX modules, configure audio, or implement any Steam APIs.

The implementation contract comes from these public sources:

- [GE's pinned Proton launcher](https://github.com/GloriousEggroll/proton-ge-custom/blob/c191f35dcebbeccfacd3b4c6f6eea026e588c1c2/proton#L709) selects `files/bin-arm64/wine` and `wineserver` for ARM64. The same launcher handles native Wine library paths and FEX application configuration.
- [Wine's pinned manual](https://github.com/ValveSoftware/wine/blob/46b29104e3741fe23bf5e2547196a253aab88c89/tools/wine/wine.man.in) documents the `WINESERVER` path override. This wrapper does not set unsupported FEX tuning or loader controls.
- [The public native Steam launcher](../libexec/steam-native4k-launch-loop.sh) uses `userpages 4k` to execute native programs. Its source is the published `tools/aurora-userpages` in `aurora-silicon/linux`. [The pinned launcher implementation](https://github.com/aurora-silicon/linux/blob/3b2bd35a8591bcad766c60d0681e7f060cfd8cc7/tools/aurora-userpages/userpages.c) sets both the next execution and descendant execution page-size preference to4096 before delegating the command.

This entry point is independently written from those contracts. It does not establish byte correspondence with an older locally generated entry point or qualify a complete installed tool. Before replacement, preserve the old file with its SHA and exact metadata, verify the installed Wine/FEX components against the public source ledger, and qualify actual game processes, public driver mappings and a rendered frame. No wrapper upgrade should silently alter a bridge or vendor Proton implementation.
