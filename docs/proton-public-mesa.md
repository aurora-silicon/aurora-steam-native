# Guarded ARM64 Proton with system Mesa

A Proton wrapper may keep an explicit Mesa prefix after Steam itself has moved
to system Mesa. Remove only that wrapper's Mesa pins after identifying the
actual compatibility tool, verifying its source/build receipt, and recording
its current hash. This preserves its Wine/FEX setup and the game's prefix.

`proton/guarded-proton-source-lock.json` records the recovered GE-Proton11-7
source graph, custom FEX bridge revision, published Wine payload guard,
toolchain inputs and historical guarded Unix ntdll hash. All source revisions
are public. It distinguishes known inputs from missing reproduction and current
artifact correspondence. The published patch has a new descriptive header;
its diff section is byte-identical to the original build patch.

The working tool's Wine source was prepared from the release's Wine-staging and
GE patch queue before applying the guard. Rebuilding raw Valve Wine plus the
guard alone does not reproduce that source graph. The recorded Fedora Unix
ntdll build required GLIBC up to 2.38; its real Steam Runtime launch must also
be checked. Stock GE FEX and the custom FEX bridge are different source pins;
keep the current verified bridges for this narrow routing change.

## Inventory and source correspondence

Record the wrapper hash, tool name and build-manifest JSON. Check the guarded
Unix ntdll, Wine launcher, server, unchanged PE files and all four FEX bridge
hashes against the last build receipt. Check the latest wrapper separately:
removing test instrumentation can legitimately change that hash. If a relevant
artifact differs without a corresponding public source/build record, report
that gap before classifying the current tool as reproduced.

Determine the tool actually used by the game from its launcher/reaper and Wine
process descendants, executable paths and tool-local manifests. This procedure
does not read Steam authentication files or account configuration stores.

## Targeted wrapper change

Set `WRAPPER` to the one tool's wrapper, `LEGACY_PREFIX` to the precise old Mesa
installation prefix, and `RECORD` to a new transaction JSON outside the tool.
Stop any game using the tool before applying. Steam may remain open, but must
not launch another game during the change.

```sh
python3 tools/proton-mesa-routing.py inspect --wrapper "$WRAPPER"
python3 tools/proton-mesa-routing.py audit --wrapper "$WRAPPER" \
  --source-lock proton/guarded-proton-source-lock.json
python3 tools/proton-mesa-routing.py plan --wrapper "$WRAPPER" \
  --expected-sha256 "$BEFORE" --retire-prefix "$LEGACY_PREFIX"
python3 tools/proton-mesa-routing.py apply --wrapper "$WRAPPER" \
  --expected-sha256 "$BEFORE" --retire-prefix "$LEGACY_PREFIX" --record "$RECORD"
```

`BEFORE` is the exact hash from the inspection. Review the planned zero-context
diff and resulting hash before applying. Multiple exact prefixes can be supplied
with repeated `--retire-prefix`. The helper changes only `LD_LIBRARY_PATH`,
`LIBGL_DRIVERS_PATH`, `VK_DRIVER_FILES`, `VK_ICD_FILENAMES`, `GBM_BACKENDS_PATH`,
`__EGL_VENDOR_LIBRARY_FILENAMES` and `DRIRC_CONFIGDIR`. It preserves other public
path-list entries, refuses complex shell expressions or unhandled prefix
references, and runs Bash syntax validation. It checks for tool processes,
refuses symlinks/hard links and stale hashes, preserves owner/mode, creates a
hash-verified backup and atomically replaces the wrapper. It adds no runtime
instrumentation or launcher hook.

## One Geometry Dash qualification

Use the Steam-managed launch of AppId 322170 through the verified public Steam
launcher. Start from a clean game state, disable core storage, keep game audio
unreachable by process environment, and enforce the existing memory, swap,
thermal and free-space gates. Run this experiment in the target controller's
serialized hardware slot.

Allow up to 180 seconds for the game process, then a 60-second menu/rendering
sample with a screenshot and a simple input response. Record the actual process
tree, tool executable paths, Wine/FEX module hashes, renderer and loaded Mesa
paths. Require the intended public system or staged Mesa in every process that
uses Mesa, and no retired-prefix mappings. Require correct pixels, a responsive
game and Steam still alive after a graceful game exit. Record any assert,
GPU-process restart, device loss, crash or resource gate. Do not classify a
process appearing without a real rendered frame as a successful launch.

The graphics routing test does not establish every game's performance or the
public provenance of unrelated Linux FEX modifications. Do not replace FEX
bridges, update the global compatibility-tool default or restore a vendor FEX
installation as part of this one wrapper change. Actual process/tool evidence
is sufficient to establish which tool handled this smoke; no account-store
mapping extraction is needed.

## Rollback and old prefixes

The apply output reports `AFTER` and its verified backup. Restore only if the
current wrapper still has that exact hash and the tool is idle:

```sh
python3 tools/proton-mesa-routing.py rollback --wrapper "$WRAPPER" \
  --expected-sha256 "$AFTER" --record "$RECORD"
```

Rollback restores the prior wrapper, which may refer to the old Mesa runtime;
it is not a public-runtime qualification and does not automatically launch a
game. Preserve old prefixes during the qualification. After the game and Steam
pass, record remaining references and process mappings, archive rollback
material, and handle prefix retirement in a separate explicit maintenance
operation. Do not remove or quarantine a prefix still mapped or referenced by
an active/default tool.
