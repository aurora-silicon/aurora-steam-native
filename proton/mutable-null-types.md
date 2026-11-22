Build vkd3d-proton bfdb811 with `vkd3d-mutable-null-types.patch` and the matching ARM64X toolchain. Place the resulting `d3d12core.dll` in the Proton tool's vkd3d-proton/aarch64-windows directory alongside its compatible loader.

The patch selects a supported null descriptor type for each mutable descriptor set on both the descriptor-set path (`vkUpdateDescriptorSets`) and the non-embedded descriptor-buffer path (null-descriptor payload substitution and initial raw-set null type selection). On Borealis `aurora-wip`, `VK_EXT_descriptor_buffer` is currently gated to G17P, so G17C uses the mutable descriptor-set path by default while G17P (and G17C builds with `VK_EXT_descriptor_buffer` enabled) takes the split raw/typed mutable descriptor-buffer path.

It applies independently of the Borealis queue and layout policy. Both patches apply to the same source revision.
