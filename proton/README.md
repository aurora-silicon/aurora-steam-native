# Wine ARM64: guard the FEX SVE predicate setup

`wine-arm64-sve-payload-guard.patch` changes `dlls/ntdll/unix/signal_arm64.c`.

## Problem

Before returning from a signal, Proton's ARM64 Wine initialises two SVE
predicate registers for FEX whenever `get_sve_context()` finds an SVE record
in the signal frame. It computes the offsets with `SVE_SIG_PREG_OFFSET()` from
the record's vector length.

On CPUs that implement SME but not SVE, Linux includes an SVE record that is
only a 16-byte header with `vl == 0` and no payload while streaming mode is
off. With `vq == 0` both predicate offsets fall at byte 16 of the record, which
is the start of the next record in the frame (on the machines we observed, the
TPIDR2 record). Wine overwrites that header, the kernel rejects the corrupted
frame in `rt_sigreturn`, and the process dies with a segmentation fault at the
restored stack pointer. The Linux UAPI headers say the predicate offsets may
only be used when `head.size` covers `SVE_SIG_CONTEXT_SIZE(vq)`.

## Fix

Write the two predicates only when the vector length is valid, the record's
flags contain nothing but `SVE_SIG_FLAG_SM`, and the record is large enough to
hold the payload. Full streaming and non-streaming records keep the existing
FEX initialisation; header-only, truncated or unknown records are left alone.

The patch applies unchanged to ValveSoftware/wine `proton_11.0`,
`experimental_11.0` and `bleeding-edge`, and to the Wine tree used by
GE-Proton11-7. A stand-alone unit test of the guard against the kernel's UAPI
macros passed 14 record-extent cases under AddressSanitizer and
UndefinedBehaviorSanitizer.

## Using it

Rebuild `ntdll.so` (the aarch64 Unix side of ntdll) from the Proton release's
own Wine source with the patch applied, and replace
`files/lib/wine/aarch64-unix/ntdll.so` in a copy of the compatibility tool
under a new tool name. The PE side of ntdll does not change.

### Building it

    proton/build-guarded-ntdll.sh [WORKDIR]

The script needs `git`, `curl`, `python3` and `podman` on the host, about
1.6 GB in WORKDIR (default `./guarded-build`) and some ten minutes. It fetches
GE-Proton, Wine, Wine-staging, the llvm-mingw toolchain and the Vulkan registry
at the commits and digests in `guarded-proton-source-lock.json`, checks GE's
preparation script and this patch against the lock, and runs GE's own Wine
preparation section unchanged. The Wine preparation and the build run in a
Debian 13 container, the base of Steam Linux Runtime 4. Valve's Wine tree does
not carry `include/wine/vulkan.h`, so the script generates it with
`make_vulkan` from the pinned `vk.xml` and `video.xml` before configuring.

The build stops unless the patched `signal_arm64.c` matches
`prepared_ge_signal_source_after_guard_sha256`. It writes `out/ntdll.so` and
`out/receipt.json` (library digest, source digest, newest glibc symbol version,
image and compiler). The Unix side is compiled with the pinned llvm-mingw
clang, which the script puts first on `PATH`. The library is not
byte-identical to the historical artifact, whose compiler build was not
recorded, nor between builds in different work directories; in testing its
exported symbols matched the release's own `ntdll.so` exactly.

To install it, copy `GE-Proton11-7-aarch64` in `compatibilitytools.d` to a new
directory, replace `files/lib/wine/aarch64-unix/ntdll.so` there, and give the
copy its own internal and display names in `compatibilitytool.vdf`.

The fix belongs in Wine/Proton; this copy is kept until it lands there.
