# Licenses: the code vs. the models

**The generator's own code** (everything in this repository: `chargen/`, scripts, workflows, prompts, configuration,
tests, documentation) is open source under the **MIT License** ([`LICENSE`](../LICENSE)). You may use, copy, modify,
merge, publish, distribute, sublicense and sell it; keep the copyright and license notice.

**The MIT License does not cover the models, ComfyUI or other third-party software.** The generator downloads nothing
by itself; when you install a model, its own license applies to the model and, depending on that license, to how you
may use the images it produces. Check the license of every model you use, especially for commercial projects.

## Models (installed separately into ComfyUI)

| Model | Used by | License | Notes |
|---|---|---|---|
| Stable Diffusion XL 1.0 base (`sd_xl_base_1.0.safetensors`) | quality, fast, rig inpainting | CreativeML Open RAIL++-M | use-based restrictions (no illegal or harmful uses); outputs may be used commercially |
| Pixel Art XL v1.1 LoRA (`pixel-art-xl.safetensors`) | quality, fast, rig inpainting | CreativeML Open RAIL-M | same family of terms |
| LCM LoRA SDXL (`lcm-lora-sdxl.safetensors`) | fast | OpenRAIL++ | |
| SDPose-Wholebody (`sdpose_wholebody_fp16.safetensors`) | rig step (keypoints) | MIT (model card); its U-Net derives from Stable Diffusion 2.x (CreativeML Open RAIL++-M) | pose estimator; produces no images |
| Nova Pixels XL v3.0 (`novaPixelsXL_v30.safetensors`) | nova (optional) | Illustrious license (see the Civitai page) | Civitai permissions at the time of writing: images may be sold; read the current terms |
| Krea 2 Turbo / Raw (`krea2_*`), Qwen3-VL text encoder, Qwen-Image VAE | krea2 (optional, 16 GB+) | Krea 2 license | free commercial use **only below USD 1M revenue and 50 seats**; enterprise license otherwise |
| Krea-2-Pixel-Art LoRAs (`k2-pixel*.safetensors`) | krea2 (optional) | MIT | |
| Stable Diffusion 1.5 (`v1-5-pruned-emaonly.safetensors`) | smoke (plumbing test) | CreativeML Open RAIL-M | |

The authoritative list with download URLs and sizes is [`config/models.json`](../config/models.json) (`license`,
`source` fields). Licenses can change; the model's own page is the reference.

## Software

| Software | License | How it is used |
|---|---|---|
| ComfyUI | GPL-3.0 | a separate program the generator starts and talks to over HTTP; it is not distributed with or linked into this project |
| Python | PSF License | runtime |
| Pillow | MIT-CMU (HPND) | image processing |
| numpy | BSD-3-Clause | array math |

## Generated characters

Characters you generate are produced by the models above on your machine. Whether you can use them commercially is
governed by the licenses of the models you used (see the table), not by this repository's license. `metadata.json`
of every character records the exact model files and their license strings (`model_details`), so the provenance of
each sprite is documented.
