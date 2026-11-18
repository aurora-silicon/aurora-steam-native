# D3D12 queue policy on Borealis G17C

Apply `vkd3d-borealis-g17c.patch` to vkd3d-proton commit `bfdb811c6d7ccd8c6151f19532654b063a6631f2` and build its ARM64X DLL using the project's `build-arm64x.txt` cross file. In a separate GE-Proton11-9 ARM64 compatibility tool, install the resulting `libs/d3d12core/d3d12core.dll` at `files/lib/wine/vkd3d-proton/aarch64-windows/d3d12core.dll`. The existing ARM64X d3d12 loader must be compatible with this revision.

D3D12 on Borealis G17C uses one physical queue, allows two staggered command chunks in flight, keeps barriers local to the render pass and uses explicit attachment and shader-read layouts. The GPU and driver identifiers select the policy across applications. The standard vkd3d-proton skip-driver-workarounds setting disables it.
