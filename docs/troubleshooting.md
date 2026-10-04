# Troubleshooting

Start with `character-generator doctor`: it shows the Python, the ComfyUI launch command, whether ComfyUI is
running, the models folder and which profiles have all their models.

## ComfyUI

| Symptom | Cause / fix |
|---|---|
| `no ComfyUI found at C:\AI\ComfyUI` | ComfyUI is elsewhere. Create `config/config.local.json` with `{"comfyui": {"root": "D:/path/to/ComfyUI_windows_portable"}}` or set `CHARGEN_COMFYUI_ROOT`. The root is the folder that contains `python_embeded\` and `ComfyUI\main.py` (portable build) or `main.py` + `venv\` (git install). |
| `ComfyUI exited with code ... during startup` | Read `runs/comfyui.log`. Typical: port 8188 already used by another program (change `comfyui.url`, the port follows it), broken custom node, outdated NVIDIA driver (`c10.dll`: install the VC++ redistributable). |
| `ComfyUI did not answer within 240s` | First start after an update installs things or the disk is slow. Start it once by hand with `run_nvidia_gpu.bat`, then run the generator (it uses the running instance). Or raise `comfyui.start_timeout_seconds`. |
| `ComfyUI is not reachable ... auto_start is off` | Start ComfyUI yourself or set `comfyui.auto_start` to `true`. |
| `ComfyUI rejected the workflow (HTTP 400): ... value not in list: ckpt_name` | The model file is missing or has another name. `character-generator models list`; install with `models install --group <group>` (asks nothing, so only run it when you want the download), or fix the file name in `config/profiles.json`. |
| `... node ... does not exist` (class_type) | The ComfyUI version is too old for that workflow. Krea 2 needs ComfyUI 0.26+; SDXL workflows use only core nodes. Update ComfyUI with `update\update_comfyui.bat`. |
| ComfyUI left running after an error | It is only stopped when the generator started it. `character-generator comfy stop` stops the instance recorded in `runs/comfyui.pid`; it never kills a ComfyUI started by you or by the AI Sprite Animation package. |
| Both tools use ComfyUI at the same time | They share one server (port 8188) and queue on the same GPU; that works, but each waits for the other. Avoid generating characters during an AI Redraw / AI Pose run. |

## VRAM

Measured on an RTX 3080 10 GB:

| Profile | Peak GPU memory (whole card, incl. about 2.5 GB used by Windows/Unity) | Notes |
|---|---|---|
| `quality`, `fast` (SDXL) | about 7.2-9.7 GB | Fits. The first image after loading is the highest. |
| `nova` | about 7-8 GB | Fits. |
| `krea2` | about 9.4-9.7 GB | At the limit: ComfyUI streams the 12 GB model from RAM ("dynamic VRAM loading"). Close games/other GPU programs; also needs about 13 GB of free RAM. |

* `CUDA out of memory` / `allocation on device`: close other GPU programs (games, browsers with GPU video, other
  Unity editors), then retry. ComfyUI offloads automatically; if it still fails, start ComfyUI with `--lowvram`
  (add it to `comfyui.extra_args` in `config/config.local.json`) or use the SDXL profiles.
* **Stuck for minutes at 100 % GPU after sampling (VAE decode)**: the card is full and the Windows driver silently
  falls back to system memory instead of raising out-of-memory. The guided workflows decode/encode **tiled**
  (512 px tiles) and the tool starts ComfyUI with `--reserve-vram 1.5`, which together removed the stall in testing.
  If you start ComfyUI yourself, add `--reserve-vram 1.5`; close other GPU-heavy programs. A stalled generation
  fails after `comfyui.generation_timeout_seconds` (300 s); `curl -X POST http://127.0.0.1:8188/interrupt` stops it at once.
* Very slow (minutes per image) in general: VRAM is full and weights are being swapped. Same fixes; `nvidia-smi` shows who uses the GPU.
* Generation 1024x1024 is the SDXL native size. Do not raise it: it costs VRAM and does not improve the 64-128 px sprite.

## Failed generations

| Symptom | Meaning / fix |
|---|---|
| `"ok": false`, `"error": "profile 'x' needs models that are not installed: ..."` | install the named group (`models install --group ...`) or copy the files by hand (setup.md) |
| `"error": "project file not found: ..."` | `--project` points to a folder without `character-generator.project.json` (or a wrong path) |
| `"error": ".../<Name>.png already exists ..."` | use `--variant`, `--overwrite`, or another `--name` |
| `"ok": true` but `"warnings": ["quality check failed on every tried seed: ..."]` | every seed (default 6) failed the gate; the last image is kept. Look at the preview; try `--seed` with another range, a simpler description, `--guide-build`, or more `--attempts` |
| `generation failed: <node>: <message>` | ComfyUI raised an error in that node; the message is ComfyUI's (often a missing model or out of memory) |
| `timed out after 300s` | ComfyUI did not finish: usually the VRAM stall below. Raise `comfyui.generation_timeout_seconds` only for slow profiles (krea2) |
| Pure noise / static images, every seed rejected | ComfyUI reused a cached LoRA-patched model; see "Image quality" |
| `regenerate --verify` not identical | `workflow_changed: true` = a workflow file changed since; otherwise a model file, ComfyUI or PyTorch version changed (compare `metadata.json`) |

Every run keeps its files in `runs/<timestamp>_<name>_<profile>_s<seed>/` (`workflow_api.json`, `raw.png`,
`preview.png`) and ComfyUI's log in `runs/comfyui.log` (when the tool started ComfyUI).

## Rig step

| Symptom | Meaning / fix |
|---|---|
| `the rig step needs the SDPose model` | `character-generator models install --group rig` |
| `rig data needs a side-view character` | the spec's view is not `side`; rig data is only made for side views |
| `<Name>.png no longer matches its recipe` | the sprite was edited or re-cut; regenerate/re-cut it, then run `rig` again |
| `SDPose found no person` | background or style confused the estimator; check `references/source_raw.png`, re-roll the character |
| arm parts on the wrong arm | check `rig.json -> decisions.side_confidence`; a warning appears when the two sides score alike. Re-roll or fix the part map in the consuming tool |
| a cape edge or strap labelled `Weapon` | the weapon search follows pixels connected to the hand; harmless for idle/walk, visible in attacks |
| the underlay shows a hand/arm | should not happen (only torso colours are allowed); if it does, `rig --from-saved` after lowering `rig.inpaint_denoise` in the config |

## Image quality

* **Pure noise / static** (gate says "background not separable" for every seed): ComfyUI 0.38 reused a cached,
  LoRA-patched SDXL from another profile. The generator unloads models whenever the profile changes (it remembers
  the last profile in `runs/comfy_last_profile.json`). If it still happens (e.g. you ran other SDXL workflows in the
  same ComfyUI by hand): `curl -X POST http://127.0.0.1:8188/free -d "{\"unload_models\":true}"` or restart ComfyUI.
* **Character faces left**: SDXL does not always respect left/right. `regenerate --name X --from-raw --mirror --replace`
  flips it without the GPU; the flip is stored in the recipe.

| Problem | Fix |
|---|---|
| Sprite sheet / several characters | The quality gate retries the next seed automatically (up to `--attempts`, default 4). If all fail, change the spec (fewer items, simpler pose) or the seed. |
| Background not removed (rectangle of colour) | The model drew scenery or a gradient. Keep `background` as `plain white background`; try another seed. The gate rejects images where more than 60 % of the frame is foreground. |
| Grey floor shadow left under the feet | The shadow pass removes low-chroma pixels near the feet connected to the background. Coloured shadows survive: use another seed or clean the 1-2 pixels by hand. `pixelate(..., remove_shadow=False)` keeps them. |
| Back or front view instead of side | Text alone gave a side view for only 1 of 10 characters. Side views therefore start from a mannequin guide (on by default for `quality`/`fast`); check the result has `"guide"` set in `character.json`. `--no-guide` turns it off. |
| Body type ignored (thin dwarf, slim barbarian) | The guide's build comes from `body_type`/`species`/`clothing` (`chargen/guide.py`); force it with `--guide-build stocky` (slim, normal, broad, stocky, small, robed) or `guide_build` in the spec. |
| Too much noise / dithering at 64 px | Use `--size 96/128`, fewer colours (`--colors 16`) or `--outline`. |
| Character size differs from your reference sprite | Defaults: 48 px canvas, 45 px body, feet on row 46 (change `size`, `char_height_ratio`, `feet_margin` in the config or project file). `character-generator check <png> --reference <your hero sprite>` compares height, feet row, outline and noise. |
| Weapon missing | SDXL often drops small items. Mention it first in `weapons` and in `details`, or try more seeds. |

## Python

* `ModuleNotFoundError: PIL` / `numpy`: you are not using ComfyUI's embedded Python. Use the launchers
  (`character-generator`, `character-generator.cmd`) or `pip install -r requirements.txt` into your Python.
* The embedded Python ignores `PYTHONPATH`; always run through `character_generator.py` (the launchers do).

## Unity (example integration)

* Sprite blurry: it was imported before `GeneratedCharacterImporter` existed. Set Filter Mode = Point, Compression =
  None in the Inspector (or delete the `.meta` so it is re-imported with the defaults).
* New files not visible: the editor imports when it regains focus; or Assets > Refresh.
* The editor window says "Could not start Python": set the Python path in the window (stored in EditorPrefs).
