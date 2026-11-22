# ARM64EC: pass the right exception record to x64 dispatcher hooks

`wine-arm64ec-exception-record.patch` changes `dlls/ntdll/signal_x86_64.c`.

## Problem

Wine commit `fc0d79cf5b` ("ntdll: Support xstate in ARM64EC exception
dispatcher") changed the frame that the ARM64EC `KiUserExceptionDispatcher`
in `dlls/ntdll/signal_arm64ec.c` sets up. The frame now holds a
`struct xcontext` (0x670 bytes: the x64 context, a `CONTEXT_EX` and an XSTATE
area) instead of a bare `ARM64EC_NT_CONTEXT` (0x4d0 bytes). The exception
record moved with it, to `sp + 0x670 + 0x3b0`.

The x64 entry `EXP+#KiUserExceptionDispatcher` in
`dlls/ntdll/signal_x86_64.c` was not updated. It still computes the record as
`0x30+0x3b0+0x4d0(%rsp)`, which is 0x1a0 bytes too low and points into the
ARM64 context. Two callers depend on this offset:
- `Wow64PrepareForException`, which gets the wrong record.
- x64 code that hooks `KiUserExceptionDispatcher` and takes the record location
  from it.

Diablo IV's protected loader (`diablo_iv_loader.dll`) does the second. On
ARM64EC it reaches its own fatal breakpoint during startup: an
`EXCEPTION_BREAKPOINT` in the loader, with no earlier exception reaching
`dispatch_exception`. The game then exits with 0x80000003 before writing its
first log line.

## Fix

Use the new frame size in the x64 entry, so it passes `sp + 0x3b0 + 0x670` as
the record, the same address the ARM64EC side uses. The rest of the xstate
series is kept.

## Where it applies

The patch applies unchanged to ValveSoftware/wine `experimental_11.0` and
`bleeding-edge`, and to upstream Wine master (checked 2026-11-22). Proton
Experimental `experimental-11.0-20261112` (Wine `5b557605`) has the bug. Wine
trees without `fc0d79cf5b`, such as `proton_11.0` and GE-Proton11, don't have
it and don't need the patch.

## Evidence

Run on a MacBook Pro M4 Pro (J616s), kernel 7.3.0-m4.177, with Proton
Experimental ARM64 `experimental-11.0-20261112` and a FEX ARM64EC DLL that has
aurora-silicon/fex #19. Diablo IV needs #19 as well: without it, the loader
dies earlier with an execute fault in a non-image view. Diablo IV 3.2.4.74043
was launched directly with `-launch -uid fenris`, with
`WINEDEBUG=+seh,+loaddll,+process`. The GPU guard counted 0 GPU recoveries and
0 client failures in every run.

| ntdll | Result |
|---|---|
| stock | fatal breakpoint at `diablo_iv_loader.dll+0x1B0D2C5`, exit 0x80000003, no `FenrisDebug.txt` |
| Wine `5b557605` + this patch only | the loader finishes: `FenrisDebug.txt` is written and the game runs until it waits for Battle.net |

To isolate the cause:
- **Not FEX:** the stock-ntdll failure is the same with an older FEX DLL, and
  that DLL works on GE-Proton11.
- **Bisect over the 2026-11-06 ARM64EC xstate series:** run on `5b557605` with
  an unrelated syscall-thunk patch. Reverting the newest one or two commits
  still fails, and reverting three or more passes. That points at
  `fc0d79cf5b`.

## Using it

1. Apply `wine-arm64ec-exception-record.patch` to the Wine tree of the
   compatibility tool.
2. Prepare the tree the way Proton's build does: run `tools/make_requests`,
   `tools/make_specfiles` and `dlls/winevulkan/make_vulkan`, then `autoreconf`.
3. Configure with `--enable-archs=arm64ec,aarch64` and run
   `make dlls/ntdll/all`.
4. In a separate copy of the tool, replace
   `files/lib/wine/aarch64-windows/ntdll.dll` (the ARM64X PE side, which holds
   the x64 entry) and `files/lib/wine/aarch64-unix/ntdll.so` with the built
   pair.

The fix belongs in Wine/Proton; this copy is kept until it lands there.
