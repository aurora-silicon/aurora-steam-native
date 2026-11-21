# D3D12 queue and layout policy on Borealis

Apply `vkd3d-borealis-queues.patch` to vkd3d-proton commit `bfdb811c6d7ccd8c6151f19532654b063a6631f2` and build its ARM64X DLL using the project's `build-arm64x.txt` cross file. In a separate GE-Proton11-9 ARM64 compatibility tool, install the resulting `libs/d3d12core/d3d12core.dll` at `files/lib/wine/vkd3d-proton/aarch64-windows/d3d12core.dll`. The existing ARM64X d3d12 loader must be compatible with this revision.

The policy applies to every Apple GPU driven by Borealis. It is selected by the Vulkan driver name `Borealis`, which the driver reports on every GPU it opens. The driver ID is not used, because Borealis reports either `VK_DRIVER_ID_MESA_HONEYKRISP` or its own ID depending on its configuration. No GPU generation is excluded.

With the policy, D3D12 on Borealis:

- uses one Vulkan queue for the normal graphics, compute and copy paths, as `VKD3D_CONFIG=single_queue` does. vkd3d still creates its internal-compute queue on the compute family it found before this remap (a dedicated compute family where the driver exposes one), and still selects a separate sparse-binding family where one is available. This only changes GPUs on which the driver exposes more than one queue or queue family, which Borealis does when the kernel provides independent queues;
- keeps barriers local to the render pass instead of suspending rendering across command lists, under either driver ID. vkd3d only staggers submissions when render passes are not suspended, so this also enables staggering under the Honeykrisp driver ID;
- when graphics and non-graphics D3D12 queues are active on the shared queue, lets each logical queue keep up to ten staggered command chunks in flight instead of one (`VKD3D_BOREALIS_STAGGER_DEPTH`). Ten is a steady-state throttle per logical queue, not a hard bound on the shared queue: the history starts empty and is cleared when staggering changes, and several logical queues submit to the same Vulkan queue. The depth was measured on the M5 Max (G17C) only. A queue that staggers to let an active higher-priority queue's work in sooner keeps vkd3d's one chunk, since that throttling exists for latency;
- turns off `unifiedImageLayouts`, so attachments and shader reads use their explicit layouts. This only changes GPUs on which the driver exposes `VK_KHR_unified_image_layouts`.

`VKD3D_CONFIG=skip_driver_workarounds` disables the policy.
