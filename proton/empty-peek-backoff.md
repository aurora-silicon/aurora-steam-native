# Empty message polling on Linux ARM64

Apply `wine-arm64-empty-peek-backoff.patch` to the prepared GE-Proton11-9 Wine source, based on Wine commit `981e2f0b9007fd70a6e0ed6119cb18f4164e26f9`. Build `dlls/win32u/win32u.so` for Linux AArch64 and install it as `files/lib/wine/aarch64-unix/win32u.so` in a separate copy of the matching compatibility tool. Selecting that tool enables the behavior for all of its applications.

After an empty `PeekMessage`, Wine yields the thread. When the same thread makes eight empty polls within a single 125 µs driver-event interval, it requests a 50 µs sleep. A successful peek resets the count. Under continuous polling this can delay message pickup and gives other threads more time to run.
