Build vkd3d-proton bfdb811 with `vkd3d-mutable-null-types.patch` and the matching ARM64X toolchain. Place the resulting `d3d12core.dll` in the Proton tool's vkd3d-proton/aarch64-windows directory alongside its compatible loader.

The patch selects a supported null descriptor type for each mutable descriptor set. It applies independently of the Borealis queue and layout policy. Both patches apply to the same source revision.
