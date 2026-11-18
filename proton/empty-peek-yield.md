# Direct yield on empty Wine message polls

`wine-empty-peek-yield.patch` changes `dlls/win32u/message.c`.

## Problem

When `NtUserPeekMessage()` finds no matching message, it yields the thread by
calling `NtYieldExecution()` and discards the returned `NTSTATUS`. On Unix
builds with `RUSAGE_THREAD`, `NtYieldExecution()` calls
`getrusage(RUSAGE_THREAD, ...)` before and after `sched_yield()` solely to
decide whether to return `STATUS_SUCCESS` or `STATUS_NO_YIELD_PERFORMED`.
Applications that poll an empty message queue in a loop pay for two wasted
`getrusage()` syscalls on every empty poll.

## Fix

Call `sched_yield()` directly on the empty `NtUserPeekMessage()` path when
`HAVE_SCHED_H` and `HAVE_SCHED_YIELD` are defined, and fall back to
`NtYieldExecution()` otherwise. No sleep or polling delay is added.

## Using it

Apply `wine-empty-peek-yield.patch` to the prepared GE-Proton11-9 Wine source
(based on Wine commit `981e2f0b9007fd70a6e0ed6119cb18f4164e26f9`), rebuild
`dlls/win32u/win32u.so` for Linux AArch64, and install it as
`files/lib/wine/aarch64-unix/win32u.so` in a separate copy of the matching
compatibility tool.

The fix belongs in Wine/Proton; this copy is kept until it lands there.
