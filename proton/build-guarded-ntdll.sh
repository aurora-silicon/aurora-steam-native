#!/bin/bash
# SPDX-License-Identifier: MIT
# Build the aarch64 Unix ntdll.so of GE-Proton11-7 with the SVE payload guard,
# from the sources pinned in guarded-proton-source-lock.json.
#
#   proton/build-guarded-ntdll.sh [WORKDIR]
#
# The host needs git, curl, python3 and podman. Sources are fetched on the host
# and checked against the lock; the Wine preparation and the build run in a
# Debian 13 (trixie) container, the base of Steam Linux Runtime 4, so the result
# needs no newer glibc than that runtime has. WORKDIR (default ./guarded-build)
# ends up holding the sources, out/ntdll.so and out/receipt.json.
set -euo pipefail
HERE=$(dirname -- "$(readlink -f -- "$0")")
LOCK=$HERE/guarded-proton-source-lock.json
PATCH=$HERE/wine-arm64-sve-payload-guard.patch
WORK=$(mkdir -p -- "${1:-guarded-build}" && cd -- "${1:-guarded-build}" && pwd)
IMAGE=${AURORA_GUARD_BUILD_IMAGE:-docker.io/library/debian:trixie}

for tool in git curl python3 podman sha256sum; do
	command -v "$tool" >/dev/null || { echo "missing host tool: $tool" >&2; exit 2; }
done
lock() { python3 -I -c 'import json,sys; v=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): v=v[k]
print(v)' "$LOCK" "$1"; }
check() { echo "$1  $2" | sha256sum -c --quiet - || { echo "SHA-256 mismatch: $2" >&2; exit 1; }; }
fetch_commit() { # dir repo commit [depth]
	[ -d "$1/.git" ] || git init -q "$1"
	git -C "$1" fetch -q --depth "${4:-1}" "$2" "$3"
}

check "$(lock guard.public_patch_sha256)" "$PATCH"

echo "== GE-Proton $(lock base.release)"
fetch_commit "$WORK/ge" "$(lock base.repo)" "$(lock base.commit)"
git -C "$WORK/ge" checkout -q --force "$(lock base.commit)"
PREP=$WORK/ge/patches/protonprep-valve-staging.sh
[ "$(git -C "$WORK/ge" hash-object "$PREP")" = "$(lock wine.upstream_preparation_script_git_blob)" ] ||
	{ echo "GE preparation script differs from the lock" >&2; exit 1; }

echo "== Wine and Wine-staging"
fetch_commit "$WORK/ge/wine" "$(lock wine.repo)" "$(lock wine.commit)"
git -C "$WORK/ge/wine" checkout -q --force "$(lock wine.commit)"
# The preparation reverts this commit, so it needs the commit and its parent.
REVERT=$(sed -n 's/^ *git revert --no-commit \([0-9a-f]\{40\}\)$/\1/p' "$PREP")
[ -n "$REVERT" ] && fetch_commit "$WORK/ge/wine" "$(lock wine.repo)" "$REVERT" 2
fetch_commit "$WORK/ge/wine-staging" "$(lock wine.wine_staging_repo)" "$(lock wine.wine_staging_commit)"
git -C "$WORK/ge/wine-staging" checkout -q --force "$(lock wine.wine_staging_commit)"

echo "== Vulkan registry $(lock wine_generator_inputs.vulkan_docs_tag)"
for xml in vk video; do
	curl -sSfL -o "$WORK/$xml.xml" \
		"https://raw.githubusercontent.com/KhronosGroup/Vulkan-Docs/$(lock wine_generator_inputs.vulkan_docs_tag)/xml/$xml.xml"
	check "$(lock "wine_generator_inputs.${xml}_xml_sha256")" "$WORK/$xml.xml"
done

echo "== llvm-mingw $(lock toolchain.version)"
if [ ! -x "$WORK/llvm-mingw/bin/aarch64-w64-mingw32-clang" ]; then
	curl -sSfL -o "$WORK/llvm-mingw.tar.xz" "$(lock toolchain.llvm_mingw_url)"
	check "$(lock toolchain.archive_sha256)" "$WORK/llvm-mingw.tar.xz"
	rm -rf "$WORK/llvm-mingw" && mkdir "$WORK/llvm-mingw"
	tar -xJf "$WORK/llvm-mingw.tar.xz" -C "$WORK/llvm-mingw" --strip-components=1
fi

# GE's helper functions and its Wine section, verbatim: the script's blob is
# pinned above, so its section markers are too.
{
	sed -n '1,/^### (1) PREP SECTION ###/p' "$PREP" | sed '$d'
	sed -n '/^### (2) WINE PATCHING ###/,/^### END WINE PATCHING ###/p' "$PREP"
} > "$WORK/prep-wine.sh"
cp -- "$PATCH" "$WORK/guard.patch"

cat > "$WORK/container-build.sh" <<'EOF'
#!/bin/bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends clang lld llvm make autoconf automake \
	bison flex perl patch git python3 gettext ca-certificates xz-utils pkg-config >/dev/null
git config --global user.email build@localhost; git config --global user.name build
git config --global --add safe.directory '*'
export PATH=/b/llvm-mingw/bin:$PATH
cd /b/ge
echo "== GE Wine preparation"
bash /b/prep-wine.sh > /b/prep.log 2>&1 || { tail -30 /b/prep.log; exit 1; }
! grep -qE 'FAILED|saving rejects' /b/prep.log || { grep -E 'FAILED|rejects' /b/prep.log; exit 1; }
cd wine
echo "== SVE payload guard"
patch -Np1 < /b/guard.patch
sha256sum dlls/ntdll/unix/signal_arm64.c | cut -d' ' -f1 > /b/out/signal_arm64.c.sha256
autoreconf -f
./tools/make_requests
./tools/make_specfiles
(cd dlls/winevulkan && python3 ./make_vulkan -x /b/vk.xml -X /b/video.xml)
mkdir -p /b/obj && cd /b/obj
echo "== configure"
CC=clang /b/ge/wine/configure --enable-archs=arm64ec,aarch64,x86_64,i386 --with-mingw=clang \
	--disable-tests --without-x --without-freetype > /b/configure.log 2>&1 || { tail -30 /b/configure.log; exit 1; }
echo "== build"
make -j"$(nproc)" dlls/ntdll/ntdll.so > /b/make.log 2>&1 || { tail -40 /b/make.log; exit 1; }
cp dlls/ntdll/ntdll.so /b/out/ntdll.so
clang --version | head -1 > /b/out/compiler.txt
EOF

rm -rf "$WORK/out" "$WORK/obj" && mkdir "$WORK/out"
podman run --rm -v "$WORK:/b:Z" "$IMAGE" bash /b/container-build.sh

SIGNAL=$(cat "$WORK/out/signal_arm64.c.sha256")
[ "$SIGNAL" = "$(lock guard.prepared_ge_signal_source_after_guard_sha256)" ] ||
	{ echo "patched signal_arm64.c ($SIGNAL) differs from the lock" >&2; exit 1; }
GLIBC=$(objdump -T "$WORK/out/ntdll.so" | grep -oE 'GLIBC_[0-9.]+' | sort -Vu | tail -1)
python3 -I - "$WORK/out" "$SIGNAL" "$GLIBC" "$IMAGE" "$(lock base.release)" <<'PY'
import hashlib, json, sys
out, signal, glibc, image, release = sys.argv[1:]
digest = hashlib.sha256(open(f"{out}/ntdll.so", "rb").read()).hexdigest()
receipt = {"release": release, "ntdll_so_sha256": digest, "signal_arm64_c_sha256": signal,
           "max_glibc": glibc, "image": image, "compiler": open(f"{out}/compiler.txt").read().strip()}
json.dump(receipt, open(f"{out}/receipt.json", "w"), indent=1)
print(json.dumps(receipt, indent=1))
PY
echo "Built $WORK/out/ntdll.so; the patched source matches the lock."
