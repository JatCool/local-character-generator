"""Load an API-format workflow and fill its {{placeholders}}.

A value that is exactly "{{name}}" is replaced by the typed value (int/float/str), so numeric inputs stay
numeric; placeholders inside longer strings are replaced textually. Unfilled placeholders are an error.
"""

import copy
import hashlib
import json
import re

from .config import TOOL_ROOT

WORKFLOWS_DIR = TOOL_ROOT / "workflows"
_EXACT = re.compile(r"^\{\{(\w+)\}\}$")
_INLINE = re.compile(r"\{\{(\w+)\}\}")


def load(name):
    path = WORKFLOWS_DIR / name
    text = path.read_text(encoding="utf-8")
    return json.loads(text), hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def fill(workflow, values):
    missing = set()

    def sub(node):
        if isinstance(node, dict):
            return {k: sub(v) for k, v in node.items()}
        if isinstance(node, list):
            return [sub(v) for v in node]
        if isinstance(node, str):
            m = _EXACT.match(node)
            if m:
                if m.group(1) not in values:
                    missing.add(m.group(1))
                    return node
                return values[m.group(1)]

            def inline(mm):
                if mm.group(1) not in values:
                    missing.add(mm.group(1))
                    return mm.group(0)
                return str(values[mm.group(1)])

            return _INLINE.sub(inline, node)
        return node

    filled = sub(copy.deepcopy(workflow))
    if missing:
        raise ValueError(f"workflow placeholders without a value: {', '.join(sorted(missing))}")
    return filled


def save_image_nodes(workflow):
    return [nid for nid, node in workflow.items() if node.get("class_type") == "SaveImage"]
