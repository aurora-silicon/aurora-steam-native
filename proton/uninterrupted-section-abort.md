# Wine: a terminated thread must not die holding an ntdll lock

`wine-uninterrupted-section-abort.patch` changes `dlls/ntdll/unix/server.c`
and `dlls/ntdll/unix/unix_private.h`.

## Problem

ntdll's Unix side guards some of its caches with "uninterrupted sections":
`server_enter_uninterrupted_section()` blocks the server signals and takes a
mutex. Several of them make a server request while they hold the mutex.
`NtClose()` holds `fd_cache_mutex` around `close_handle`. The fsync and
in-process sync lookups (`get_object()` in `fsync.c`, `get_inproc_sync()` in
`sync.c`) hold it around their fd requests.

When another thread calls `TerminateThread()` on such a thread, the server's
`kill_thread()` closes the thread's request and reply pipes. The thread's next
read or write on them fails, and `read_reply_data()` or `send_request()` calls
`abort_thread(0)` at that point. `pthread_exit()` then runs with the
section's mutex still locked. Blocking SIGQUIT in the section does not help,
because this path does not go through the signal. The mutex stays locked by a
thread that no longer exists, and every thread that takes it afterwards hangs
forever.

Hearthstone hangs this way when the player quits. The main thread waits in
`NtWaitForMultipleObjects` for 33 threads. Four of them are blocked in
`NtClose` on `fd_cache_mutex`, and the mutex's owner thread no longer exists.

## Fix

Count how deep each thread is in uninterrupted sections, in
`ntdll_thread_data`. When the server connection is gone inside a section, the
request fails with `STATUS_THREAD_IS_TERMINATING`, and every later request of
that thread fails the same way at once. The caller unwinds through its normal
error path and releases the mutex. The thread dies when it leaves the
outermost section, after the unlock. Outside a section, a lost connection
still ends the thread at once, as before.

No reply was read on that path, and a request's reply shares its buffer with
the request, so the reply is cleared before the error is returned. Without
that, `server_get_unix_fd()` would read the request's handle as
`reply->cacheable` and cache the error for that handle in the process-wide fd
cache.

Sections entered before the first TEB exists (while it is being allocated)
are not counted. No thread can be terminated at that point.

## Where it applies

The patch applies unchanged to ValveSoftware/wine `proton_11.0`,
`experimental_11.0` and `bleeding-edge`, and to the Wine tree of
GE-Proton11-11 (checked 2026-11-26). The patched `server.c` compiles in the
GE tree. Upstream Wine master has the same `abort_thread()` path and the same
`NtClose()` section, but its context differs and the patch does not apply
there as is.

## Evidence

Run on a MacBook Pro M4 Pro (J616s), kernels 7.3.0-m4.190 and m4.195, with Proton
Experimental ARM64 `experimental-11.0-20261112` (Wine `5b557605` plus the
ARM64EC exception-record patch), fsync on, in a fresh prefix.

The test program, `terminate-thread-lock-test.c`, is built as a native ARM64
PE, so FEX is not involved. A victim thread loops
`CloseHandle(CreateEventW(...))`. The main thread repeats 20,000 times: create
the victim, `Sleep(i % 3)`, `TerminateThread()`, wait for it, close its handle
and one more event. A watchdog thread reports a deadlock if the main thread
makes no progress for 5 s.

| ntdll | Result |
|---|---|
| stock | 16 of 16 runs deadlock after 1 to 22 iterations |
| with this patch | about 100 runs: all but two finish all 20,000 iterations (25 to 36 s each) |

In both stock runs inspected with gdb, `fd_cache_mutex` is owned by a thread
ID that is no longer in the process, and the main thread is blocked in
`get_object()` (`fsync.c`) on that mutex. That is the same state as in the hung Hearthstone
process.

Two runs with the patch stalled in a different place: after the victim was
gone, in the main thread's next event create/close or thread creation. Both
were the first run after the tool or the test processes had changed, no stack
was captured, and a 60-run soak that saves stacks on a stall did not hit it
again. It is open.

Separately, once in about 160 prefix starts with the patched tool, the
prefix's `explorer.exe` spun at 100% CPU from its own start: `winex11.so` was
no longer mapped, and `NtUserGetMessage()` faulted at a PC inside the hole it
left, returned an error and was called again. Nothing terminates explorer's
threads, so the patch's changed path is not reached there. It did not recur in
60 starts each with stock and patched ntdll. It is open as well.

## Running the test

It needs only clang, lld and llvm-dlltool, with Wine's headers for
`windows.h`:

    { printf 'LIBRARY kernel32.dll\nEXPORTS\n'
      printf '%s\n' CloseHandle CreateEventW CreateFileA CreateThread \
          ExitProcess FlushFileBuffers GetCurrentProcess InterlockedIncrement \
          Sleep TerminateProcess TerminateThread WaitForSingleObject WriteFile
    } > k32.def
    llvm-dlltool -m arm64 -d k32.def -l libk32.a
    clang --target=aarch64-windows-gnu -O2 -fno-builtin -nostdlib \
        -I "$WINE/include/msvcrt" -I "$WINE/include" -D__WINESRC__ \
        -fuse-ld=lld -Wl,--entry=mainCRTStartup -Wl,--subsystem=windows \
        proton/terminate-thread-lock-test.c libk32.a -o terminate-thread-lock-test.exe

Run it with the tool under test in a scratch prefix and read
`terminate-thread-lock-test.txt` next to it: `PASS: ...` or
`DEADLOCK at iteration N`. A deadlocked run stays alive for inspection, so
end it yourself.

## Using it

1. Apply `wine-uninterrupted-section-abort.patch` to the Wine tree of the
   compatibility tool.
2. Prepare and configure the tree the way the tool's build does (see
   `arm64ec-exception-record.md`), and run `make dlls/ntdll/all`.
3. In a separate copy of the tool, replace
   `files/lib/wine/aarch64-unix/ntdll.so` and
   `files/lib/wine/aarch64-windows/ntdll.dll` with the built pair.

The fix belongs in Wine/Proton; this copy is kept until it lands there.
