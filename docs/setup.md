# Setup

## 1. Prerequisites

| Item | Required | Notes |
|---|---|---|
| OS | Windows 10/11 (tested); Linux should work (launcher `character-generator`, ComfyUI from git + venv) | |
| GPU | NVIDIA with CUDA, **8 GB VRAM minimum, 10-12 GB comfortable** for the default profile | see VRAM table below |
| RAM | 16 GB minimum, 32 GB recommended | SDXL models are cached in RAM between runs |
| Disk | ~7.5 GB for the default models (+ ComfyUI ~6 GB) | ~31 GB if you install every alternative |
| ComfyUI | 0.26 or newer (tested 0.38.0 portable NVIDIA build) | only built-in nodes are used; no custom nodes |
| Python | 3.10+ with Pillow and numpy | ComfyUI's portable build ships Python 3.13 with both; the launchers use it automatically |
| Internet | only to download ComfyUI and the models once | generation is fully offline |

## 2. GPU / VRAM expectations

Measured on an RTX 3080 10 GB (figures are whole-card usage, including ~2 GB used by Windows and other programs):

| Profile | Model | Peak VRAM | Time per character | Fits |
|---|---|---|---|---|
| `quality` (default) | SDXL 1.0 + Pixel Art XL | ~9.3 GB (with ComfyUI `--reserve-vram 1.5`) | ~15 s (+ ~15 s model load on the first run) | 8 GB cards: yes with offloading; 10-12 GB: comfortable |
| `fast` | + LCM LoRA, 8 steps | ~8-9 GB | ~5 s | same |
| `nova` | Nova Pixels XL v3 (SDXL) | ~9.5 GB | ~12 s | same |
| `krea2` | Krea 2 Turbo 12.9B fp8 | 9.5-9.9 GB, streamed from RAM | ~22 s, spikes to 100+ s | **16 GB+ recommended**; kept for future testing |

Other programs that use the GPU (games, video in browsers, several open Unity editors) reduce the headroom. When the
card is full, Windows does not raise "out of memory" but becomes extremely slow; see
[troubleshooting.md](troubleshooting.md#vram).

## 3. ComfyUI: install and location

1. Download the portable NVIDIA build from <https://github.com/comfyanonymous/ComfyUI/releases>
   (`ComfyUI_windows_portable_nvidia.7z`) and extract it.
2. Default expected location: **`C:\AI\ComfyUI`**, i.e. the folder that contains `python_embeded\`, `ComfyUI\main.py`
   and `run_nvidia_gpu.bat`. Models live in `C:\AI\ComfyUI\ComfyUI\models\<folder>\`.
3. Elsewhere? Either create `config/config.local.json` (git-ignored):

   ```json
   { "comfyui": { "root": "D:/Tools/ComfyUI_windows_portable", "url": "http://127.0.0.1:8188" } }
   ```

   or set environment variables `CHARGEN_COMFYUI_ROOT` and `CHARGEN_COMFYUI_URL`. A git checkout of ComfyUI with a
   `venv` (or `.venv`) next to `main.py` is detected too.
4. ComfyUI may be shared with other tools (e.g. an animation pipeline); the generator only adds model files and
   uploads its guide images into `ComfyUI/input/chargen/`.

<a id="models"></a>
## 4. Models

Listed in [`config/models.json`](../config/models.json) and installed **only on request**:

```bash
./character-generator models list                  # what is installed, where
./character-generator models install --group sdxl  # downloads missing files of a group (resumable *.part files)
```

| Group | File -> `ComfyUI/models/<folder>/` | Size | Used by | License |
|---|---|---|---|---|
| **`sdxl`** (required) | `checkpoints/sd_xl_base_1.0.safetensors` ([SDXL 1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0)) | 6.94 GB | quality, fast | CreativeML Open RAIL++-M |
| | `loras/pixel-art-xl.safetensors` ([Pixel Art XL v1.1](https://huggingface.co/glabs/pixel-art-xl)) | 0.17 GB | quality, fast | CreativeML Open RAIL-M |
| | `loras/lcm-lora-sdxl.safetensors` ([LCM LoRA SDXL](https://huggingface.co/latent-consistency/lcm-lora-sdxl), downloaded as `pytorch_lora_weights.safetensors` and renamed) | 0.39 GB | fast | OpenRAIL++ |
| `nova` (optional) | `checkpoints/novaPixelsXL_v30.safetensors` ([Nova Pixels XL v3.0](https://civitai.com/models/1856313/nova-pixels-xl)) | 6.9 GB | nova | Illustrious license (Civitai: selling images allowed) |
| `krea2` (optional, 16 GB+ GPU) | `diffusion_models/krea2_turbo_fp8_scaled.safetensors`, `text_encoders/qwen3vl_4b_fp8_scaled.safetensors`, `vae/qwen_image_vae.safetensors` ([Comfy-Org/Krea-2](https://huggingface.co/Comfy-Org/Krea-2)); `loras/k2-pixel{128,64,32}.safetensors` ([Krea-2-Pixel-Art](https://huggingface.co/e-n-v-y/Krea-2-Pixel-Art)) | 12.2 + 4.9 + 0.24 + 3x0.07 GB | krea2, krea2-64, krea2-32 | Krea 2: free commercial use only under $1M revenue and 50 seats; LoRAs MIT |
| `rig` (for the `rig` step) | `checkpoints/sdpose_wholebody_fp16.safetensors` ([SDPose-Wholebody](https://huggingface.co/Comfy-Org/SDPose), SHA-256 verified) | 1.92 GB | rig | MIT (SD 2.x U-Net: OpenRAIL++-M) |
| `smoke` (optional) | `checkpoints/v1-5-pruned-emaonly.safetensors` (SD 1.5) | 4.3 GB | smoke (plumbing test only) | CreativeML Open RAIL-M |

Manual install: download the file and put it in the folder from the table **with exactly that file name**.
`doctor` reports per profile whether every model is present.

## 5. Starting ComfyUI

You normally don't: `generate`, `regenerate --verify` and `benchmark` start ComfyUI when nothing answers on the
configured URL (hidden window, log in `runs/comfyui.log`, `--reserve-vram 1.5`), wait until it is up (up to 240 s),
and stop it again afterwards. A ComfyUI that was already running (started by you or another tool) is used and **never
stopped**. Manual control:

```bash
./character-generator comfy status
./character-generator comfy start     # leaves it running for faster repeated runs
./character-generator comfy stop      # only stops an instance this tool started (runs/comfyui.pid)
C:\AI\ComfyUI\run_nvidia_gpu.bat      # or start it yourself, with its web UI at http://127.0.0.1:8188
```

`--keep-comfyui` on `generate`/`regenerate`/`benchmark` keeps a tool-started instance running after the command.

## 6. First test

```bash
./character-generator doctor
python -m unittest discover -s tests                     # offline, no GPU; use ComfyUI's python if needed
./character-generator --json generate --spec examples/specs/Rogue.json --seed 1840959430
```

`doctor` prints the Python/Pillow/numpy versions, whether ComfyUI is running and how it would be launched, the models
folder, which profiles have all their models, and the project/output folder. The example run writes
`output/Rogue/` (see [usage.md](usage.md#output-structure)).

To try a model by hand in the ComfyUI web UI, see [pipeline.md](pipeline.md#running-a-workflow-by-hand).
