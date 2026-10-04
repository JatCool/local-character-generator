"""Configuration: tool settings (config/config.json + config.local.json + env) and an optional project file.

The generator knows nothing about the project that consumes it. A consuming project (e.g. a Unity game) provides a
`character-generator.project.json` and passes it with `--project <file or folder>` (or CHARGEN_PROJECT). It sets where
characters are written and may override generation defaults. Without a project the tool writes to its own `output/`.
"""

import json
import os
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = TOOL_ROOT / "config"
PROJECT_FILE_NAME = "character-generator.project.json"


def _merge(base, extra):
    for key, value in extra.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_project_file(project):
    """`project` is a project file or a folder containing character-generator.project.json."""
    path = Path(project).expanduser().resolve()
    if path.is_dir():
        path = path / PROJECT_FILE_NAME
    if not path.is_file():
        raise FileNotFoundError(f"project file not found: {path}")
    return path


class Config:
    def __init__(self, data, project_file=None, project=None):
        self.data = data
        c = data["comfyui"]
        self.comfy_url = os.environ.get("CHARGEN_COMFYUI_URL", c["url"]).rstrip("/")
        self.comfy_root = Path(os.environ.get("CHARGEN_COMFYUI_ROOT", c["root"]))
        self.auto_start = bool(c.get("auto_start", True))
        self.stop_if_started = bool(c.get("stop_if_started", True))
        self.start_timeout = float(c.get("start_timeout_seconds", 240))
        self.generation_timeout = float(c.get("generation_timeout_seconds", 300))
        self.comfy_extra_args = list(c.get("extra_args", []))
        self.runs_dir = (TOOL_ROOT / data.get("runs_dir", "runs")).resolve()
        self.defaults = dict(data.get("defaults", {}))
        self.project_file = project_file
        self.project = project or {}
        if project_file:
            # paths in the project file are relative to the folder that contains it
            self.project_root = project_file.parent
            self.output_root = self.project.get("output_root", "GeneratedCharacters")
            ref = self.project.get("reference_sprite")
            self.reference_sprite = (self.project_root / ref) if ref else None
            self.project_name = self.project.get("name", self.project_root.name)
            self.defaults.update(self.project.get("defaults", {}))
        else:
            self.project_root = TOOL_ROOT
            self.output_root = data.get("output_root", "output")
            self.reference_sprite = None
            self.project_name = None

    @property
    def models_dir(self):
        # Portable build layout: <root>/ComfyUI/models; plain git checkout: <root>/models.
        nested = self.comfy_root / "ComfyUI" / "models"
        return nested if nested.is_dir() else self.comfy_root / "models"

    def character_dir(self, name):
        return self.project_root / self.output_root / name


def load_config(project=None):
    data = load_json(CONFIG_DIR / "config.json")
    local = CONFIG_DIR / "config.local.json"
    if local.exists():
        _merge(data, load_json(local))
    project = project or os.environ.get("CHARGEN_PROJECT") or None
    if project:
        project_file = find_project_file(project)
        return Config(data, project_file, load_json(project_file))
    return Config(data)


def load_profiles():
    return load_json(CONFIG_DIR / "profiles.json")["profiles"]


def load_models():
    return load_json(CONFIG_DIR / "models.json")["models"]
