"""Model manifest: check which model files are installed and download a group on request.

Nothing here runs implicitly; downloads only happen through `character-generator models install`.
"""

import sys
import time
import urllib.request
from pathlib import Path

from .config import load_models

USER_AGENT = "local-character-generator/0.1"


def model_path(cfg, model):
    return cfg.models_dir / model["folder"] / model["file"]


def status(cfg, group=None):
    rows = []
    for m in load_models():
        if group and m["group"] != group:
            continue
        p = model_path(cfg, m)
        rows.append((m, p, p.exists() and not Path(str(p) + ".part").exists()))
    return rows


def _download(url, dest, expected=None):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = Path(str(dest) + ".part")
    have = part.stat().st_size if part.exists() else 0
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        if have and resp.status != 206:
            have = 0  # server ignored the range; start over
        total = resp.headers.get("Content-Length")
        total = int(total) + have if total else expected
        mode = "ab" if have else "wb"
        done, last, t0 = have, 0.0, time.time()
        with open(part, mode) as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last > 10:
                    last = now
                    pct = f"{100 * done / total:5.1f}%" if total else "?"
                    rate = (done - have) / max(now - t0, 1e-6) / 2**20
                    print(f"  {dest.name}: {done / 2**30:.2f} GiB {pct} ({rate:.1f} MiB/s)", flush=True)
    if total and done != total:
        raise IOError(f"incomplete download of {dest.name}: {done} of {total} bytes (run again to resume)")
    part.replace(dest)


def install(cfg, group):
    rows = status(cfg, group)
    if not rows:
        raise SystemExit(f"unknown model group '{group}'")
    for m, p, ok in rows:
        if ok:
            print(f"[ok]   {m['id']}: {p}")
            continue
        print(f"[get]  {m['id']} -> {p}\n       from {m['url']}", flush=True)
        for attempt in range(1, 6):
            try:
                _download(m["url"], p, m.get("size_bytes"))
                break
            except Exception as e:  # network hiccup: resume from the .part file
                print(f"  attempt {attempt} failed: {e}", file=sys.stderr, flush=True)
                if attempt == 5:
                    raise
                time.sleep(5 * attempt)
        if m.get("sha256"):
            digest = _sha256_file(p)
            if digest != m["sha256"]:
                p.replace(Path(str(p) + ".bad"))
                raise IOError(f"{m['file']}: sha256 mismatch ({digest} != {m['sha256']}); kept as {p.name}.bad")
        print(f"[done] {m['id']} ({p.stat().st_size / 2**30:.2f} GiB)", flush=True)


def _sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
