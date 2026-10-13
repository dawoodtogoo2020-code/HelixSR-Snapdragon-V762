# HelixSR Snapdragon V762 transform stage

This is a **public experimental source-only transform preview** for four shader overrides
used in a private HelixSR 1.8.0 test on an AYN Odin 3. It is not an installer,
HelixSR release, or general Snapdragon compatibility claim. The four output
hashes were reproduced offline from locally held SPIR-V inputs; a clean user
extraction and installation have not been tested.

The kit contains original transformation code, a pinned SPIR-V grammar (MIT,
copyright The Khronos Group), a hash manifest and its schema. It contains no
HelixSR DLL, DXIL, SPIR-V shader, NVIDIA DLL, model weights, network pack, game
file, or device credential. Obtain HelixSR and any required NVIDIA material
separately from their rights holders under their terms. The source kit itself
does not contain an extracted kernel body. The public file manifest has been reviewed. A usable end-to-end model package
still needs a clean extraction workflow.

The original tools are under the included MIT `LICENSE`. The unmodified
Khronos grammar retains its copyright header and the full upstream terms in
`LICENSE.Khronos-SPIRV-Headers.txt`.

## Exact tested transform stage

The input is four **raw numeric-ID** SPIR-V 1.3 assembly files, named as in
`manifest.json`. If you already have the four runtime `.spv` dumps from your
own HelixSR 1.8.0 installation, `prepare_dumps.py` checks all four input hashes
before creating a new output directory, runs `spirv-dis --raw-id`, and checks
each assembly hash:

```text
python prepare_dumps.py --manifest manifest.json --dump-dir <private-dump-dir> --output-dir <new-assembly-dir> --spirv-dis <spirv-dis>
```

The dump preparation step passed locally with all four pinned hashes. It is
not a DLL extractor and leaves the dumps and resulting assembly on the user's
machine. Keep those files private. The equivalent individual command is
`spirv-dis --raw-id -o <assembly-dir>/<shader-hash>.spvasm <dump-dir>/<shader-hash>.spv`.

With Python 3, SPIRV-Tools `spirv-as` and an independent `spirv-val`:

```text
python build.py --manifest manifest.json --assembly-dir <assembly-dir> --output-dir <new-output-dir> --spirv-as <spirv-as> --spirv-val <spirv-val>
```

The program checks the four assembly hashes, reassembles with
`spirv-as --preserve-numeric-ids --target-env spv1.3`, restores the original
SPIR-V generator and ID bound from each disassembly header, validates the
original module, applies logical 32-lane behavior on a physical 64-lane
subgroup, then splits bindless CBV accesses into subgroup-uniform loads where
needed. It validates and checks all four final V762 SHA-256 values before
writing outputs. The `22397a04d7ae4cf4` shader has no CBV chain to split.
Outputs are appropriate only when the actual Vulkan subgroup is 64 lanes
with the lane mapping assumed by this transformation.

The latest local end-to-end check used `spirv-dis` v2026.2, `spirv-as` v2026.4,
and an independently built `spirv-val` v2026.2. The manifest pins the exact assembly,
input binary and expected output hashes, so different conversion or tool
output fails closed. `spirv.core.grammar.json` is the Khronos grammar with
SHA-256 `048ead37f9bf16613b54a1cae0f671007bd032cbf0026a21c620dcec8ba70e83`.

## Upstream extraction and release gap

The private test used an unmodified HelixSR 1.8.0 provider through a game-free
FidelityFX/D3D12 probe under Proton ARM64 and vkd3d-proton commit
`44cf7c2042168f3`. The private probe loaded the provider DLL and its separate
kernel and weight assets, queried the FFX upscaler provider, created a D3D12
context, and dispatched an analytic scene. With `VKD3D_SHADER_DUMP_PATH` set
to a private directory, vkd3d-proton wrote four matching `.spv` files. Their
names and SHA-256 values are pinned in `manifest.json`; `prepare_dumps.py` then
reproduced the exact assembly hashes. The private probe source is
`ghost_probe.cpp` with `snap_hooks.h` in the development workspace. The snapshot
hooks are optional instrumentation and are not needed to generate shader dumps.

That probe has **not** been made into a clean redistributable program or tested
against an ordinary user's official HelixSR install. It depends on pinned AMD
FFX headers, a D3D12 runtime, a game-free provider bundle, and a working
Proton/vkd3d host. A runtime dump is not the same as direct extraction of
compiled shaders from the DLL. This kit therefore starts from locally generated
dumps and does not claim a complete official-DLL-to-output public build recipe.

The HelixSR 1.8.0 license normally restricts redistribution, modification
and disassembly of its compiled parts. The author was [asked explicitly about
the four runtime shader replacements and a free unofficial package/guide](https://github.com/lonewolf0622/HelixSR/issues/3)
with no NVIDIA-derived files, and [replied as owner that we may release it](https://github.com/lonewolf0622/HelixSR/issues/3#issuecomment-6128425735).
That is the recorded scope of author permission. It does not grant rights to
NVIDIA material, and the source-only public manifest has been reviewed. This folder contains only generic transformation code and pins, not
the extracted HelixSR kernel body.

HelixSR is by lonewolf0622. This experimental transform is unofficial and
is not endorsed by HelixSR, NVIDIA, AMD, Valve or AYN.

## Available model and game status

The current adaptation covers HelixSR 1.8.0 Model E generated locally from DLSS 310.7.0. This is not DLSS 3.7 and does not certify other models. PRAGMATA has historical physical confirmation on Odin 3 / Adreno 830 / Armada. Witcher 3 Remastered 5.01 runs Helix but still flashes black geometry or entire frames; its matched original FSR test was clean. Android GameHub, DeckDroid, GameNative and Thor remain unverified.

Use the [separate Decky Armada plugin](https://github.com/dawoodtogoo2020-code/HelixSR-Decky-Armada) to manage privately prepared assets. This release contains no ready-to-install model and cannot supply the missing private dumps.
