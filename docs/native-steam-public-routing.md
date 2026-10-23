# Native Steam launcher routing

The public native Steam entry point and restart loop use `/usr/libexec/aurora/userpages` by default. They preserve an explicit `AURORA_USERPAGES_LAUNCHER` override, including a reviewed test launcher. A home-local or PATH-selected older binary cannot silently replace the standard default.

To update an existing install without changing its client, aliases, SDK or settings, install the exact published `bin/steam-native4k` and `libexec/steam-native4k-launch-loop.sh` at the corresponding prefix paths. Preserve the prior files with their SHA, ownership, mode and timestamps before replacement. The entry point resolves the existing `~/.steam/root` unless a caller explicitly supplies `AURORA_STEAM_ROOT`; the loop resides at the prefix's `libexec/steam-native4k-launch-loop.sh`.

Qualification must inspect the actual CEF process helper environment and4K execution, expected public Mesa mappings, memory/crash guards and real UI frames. A launch environment or default alone does not prove the child's selected route. If a child reports an explicit helper different from the reviewed expected path, abort instead of calling the route qualified. Missing process environment remains unknown and requires another actual execution proof.
