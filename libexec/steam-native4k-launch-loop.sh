#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Ryan Murray <ryan@aurorasilicon.org>
#
# Run Valve's native ARM64 Linux Steam client in a 4 KiB userspace page tree
# and honour the client's exit-status-42 restart request after self-updates.
set -euo pipefail
: "${AURORA_STEAM_ROOT:?native Valve Steam root required}"
# userpages is built from tools/aurora-userpages in the aurora-silicon/linux tree.
# Exported: Proton compatibility tools started by Steam run their native Wine under
# the same launcher and read this variable from the environment.
export AURORA_USERPAGES_LAUNCHER=${AURORA_USERPAGES_LAUNCHER:-$(command -v userpages || echo /usr/libexec/aurora/userpages)}
[ -x "$AURORA_USERPAGES_LAUNCHER" ] || { echo "userpages launcher not found: $AURORA_USERPAGES_LAUNCHER" >&2; exit 2; }
[ -x "$AURORA_STEAM_ROOT/steamrtarm64/steam" ]
export AURORA_STEAM_ROOT
# Valve's steam.sh raises the soft open-file limit to the hard limit before it
# starts the client; the native entry point skips that script. Under a systemd
# user unit the soft limit is 1024, and current clients exhaust it while starting
# ("socket(): Too many open files"), then crash.
ulimit -Sn "$(ulimit -Hn)" 2>/dev/null || true
# FEX titles render through the guest graphics provider. The stock guest rootfs
# carries a Mesa without Apple G17P support (every GPU job fails with EINVAL);
# prefer the Aurora provider, built from the same Mesa source as the host
# driver, when it is installed and the caller did not choose one.
AURORA_FEX_GRAPHICS_PROVIDER=${AURORA_FEX_GRAPHICS_PROVIDER:-/usr/share/guestos/aurora-fex-mesa/graphics_provider.json}
if [ -z "${STEAM_COMPAT_GRAPHICS_PROVIDER:-}" ] && [ -r "$AURORA_FEX_GRAPHICS_PROVIDER" ]; then
 export STEAM_COMPAT_GRAPHICS_PROVIDER=$AURORA_FEX_GRAPHICS_PROVIDER
fi
# The session can retain an older deprecated ICD override after the Steam
# wrapper selects a newer prefix. Keep one source of Vulkan driver selection
# for Steam and every runtime child; older loaders use their default catalog.
if [[ ${VK_DRIVER_FILES+x} ]]; then
 unset VK_ICD_FILENAMES
fi
# Steam and its runtime children outlive the process that starts this loop.
# A hardware runner or terminal may close its capture pipes after startup;
# pressure-vessel resets SIGPIPE and would die on its next diagnostic write.
# Keep both inherited streams on an owned regular file across every restart.
AURORA_STEAM_CONSOLE_LOG=${AURORA_STEAM_CONSOLE_LOG:-${XDG_STATE_HOME:-$HOME/.local/state}/aurora-steam-native4k/console.log}
umask 077
mkdir -p -- "$(dirname -- "$AURORA_STEAM_CONSOLE_LOG")"
exec >> "$AURORA_STEAM_CONSOLE_LOG" 2>&1
# Vendor steam.sh documents exit42 as an explicit request to restart.
# Keep the parent and every requested restart inside this inherited4K tree.
exec "$AURORA_USERPAGES_LAUNCHER" 4k /bin/bash -c '
 set -euo pipefail
 [ "$(getconf PAGE_SIZE)" = 4096 ] || { echo "4K process contract not active" >&2; exit 2; }
 cd "$AURORA_STEAM_ROOT"
 export LD_LIBRARY_PATH="$AURORA_STEAM_ROOT/steamrtarm64:$AURORA_STEAM_ROOT/steamrtarm64/libs${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
 unset STEAM_RUNTIME STEAM_RUNTIME_LIBRARY_PATH
 AURORA_RESTARTS=0
 AURORA_RESTART_LIMIT=${AURORA_STEAM_RESTART_LIMIT:-8}
 [[ "$AURORA_RESTART_LIMIT" =~ ^[0-9]+$ ]] || { echo "Invalid restart limit" >&2; exit 2; }
 while :; do
  [ "$(getconf PAGE_SIZE)" = 4096 ] || { echo "4K contract lost" >&2; exit 2; }
  AURORA_NATIVE_BINARY="$AURORA_STEAM_ROOT/steamrtarm64/steam"
  [ -x "$AURORA_NATIVE_BINARY" ] || { echo "Native Steam entry missing after update" >&2; exit 2; }
  python3 -c "import pathlib,struct,sys; d=pathlib.Path(sys.argv[1]).open(\"rb\").read(20); assert d[:4]==b\"\\x7fELF\" and d[4:6]==bytes([2,1]) and struct.unpack_from(\"<H\",d,18)[0]==183, \"Steam entry is not ELF64 little-endian AArch64\"" "$AURORA_NATIVE_BINARY"
  printf "Aurora native Steam parent_pid=%s pages=4096 requested_restarts=%s\n" "$$" "$AURORA_RESTARTS" >&2
  sha256sum "$AURORA_NATIVE_BINARY" >&2
  if "$AURORA_NATIVE_BINARY" -beta publicbeta "$@"; then
   AURORA_STATUS=0
  else
   AURORA_STATUS=$?
  fi
  printf "Aurora native Steam exit_status=%s requested_restarts=%s\n" "$AURORA_STATUS" "$AURORA_RESTARTS" >&2
  [ "$AURORA_STATUS" -eq 42 ] || exit "$AURORA_STATUS"
  [ "$AURORA_RESTARTS" -lt "$AURORA_RESTART_LIMIT" ] || { echo "Native Steam exhausted explicit restart requests" >&2; exit 42; }
  AURORA_RESTARTS=$((AURORA_RESTARTS+1))
 done
' aurora-steam "$@"
