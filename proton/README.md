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

The fix belongs in Wine/Proton; this copy is kept until it lands there.
