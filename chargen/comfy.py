"""Minimal ComfyUI HTTP client + process manager (standard library only).

Lifecycle rule (same as the AI Sprite Animation package): use a ComfyUI that is already running and never
stop it; if none is running and auto-start is on, start one and stop it again when we are done.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url, timeout=30):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.client_id = uuid.uuid4().hex

    def _request(self, path, data=None, timeout=None):
        body = None if data is None else json.dumps(data).encode("utf-8")
        req = urllib.request.Request(self.url + path, data=body,
                                     headers={"Content-Type": "application/json"} if body else {})
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
        return json.loads(raw) if "json" in ctype else raw

    def is_up(self):
        try:
            self._request("/system_stats", timeout=3)
            return True
        except Exception:
            return False

    def system_stats(self):
        return self._request("/system_stats")

    def object_info(self, node_class):
        return self._request("/object_info/" + urllib.parse.quote(node_class)).get(node_class)

    def free(self, unload_models=True, settle_seconds=2.0):
        """Unload models and clear ComfyUI's caches. ComfyUI applies this request in its worker loop only after it
        has taken the next queued job, so a job queued right away would still run with the old models in VRAM (on a
        10 GB card: system-memory fallback, minutes instead of seconds). Wait until the queue is idle and give the
        worker time to apply the request before returning."""
        try:
            self._request("/free", {"unload_models": unload_models, "free_memory": True})
        except Exception:
            return
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                q = self._request("/queue", timeout=5)
                if not q.get("queue_running") and not q.get("queue_pending"):
                    break
            except Exception:
                break
            time.sleep(0.5)
        time.sleep(settle_seconds)

    def upload_image(self, path, subfolder="chargen"):
        """Upload a local image to ComfyUI's input folder; returns the name LoadImage expects."""
        path = Path(path)
        boundary = uuid.uuid4().hex
        crlf = "\r\n"
        parts = []
        for name, value in (("overwrite", "true"), ("subfolder", subfolder), ("type", "input")):
            parts.append(f'--{boundary}{crlf}Content-Disposition: form-data; name="{name}"{crlf}{crlf}{value}{crlf}'.encode())
        head = (f'--{boundary}{crlf}Content-Disposition: form-data; name="image"; filename="{path.name}"{crlf}'
                f'Content-Type: image/png{crlf}{crlf}')
        parts.append(head.encode() + path.read_bytes() + crlf.encode())
        parts.append(f"--{boundary}--{crlf}".encode())
        req = urllib.request.Request(self.url + "/upload/image", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            res = json.loads(resp.read())
        return f"{res['subfolder']}/{res['name']}" if res.get("subfolder") else res["name"]

    def queue(self, workflow):
        try:
            res = self._request("/prompt", {"prompt": workflow, "client_id": self.client_id})
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise ComfyError(f"ComfyUI rejected the workflow (HTTP {e.code}): {_summarize_error(detail)}") from None
        if res.get("node_errors"):
            raise ComfyError(f"ComfyUI node errors: {json.dumps(res['node_errors'])[:2000]}")
        return res["prompt_id"]

    def wait(self, prompt_id, timeout, poll=0.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            hist = self._request("/history/" + prompt_id)
            entry = hist.get(prompt_id) if isinstance(hist, dict) else None
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError("generation failed: " + _execution_error(status))
                if status.get("completed", True):
                    return entry
            time.sleep(poll)
        raise ComfyError(f"timed out after {timeout:.0f}s waiting for prompt {prompt_id} (is the GPU busy or out of VRAM?)")

    def output_images(self, entry, node_ids=None):
        images = []
        for nid, out in entry.get("outputs", {}).items():
            if node_ids and nid not in node_ids:
                continue
            images.extend(out.get("images", []))
        return images

    def output_texts(self, entry, node_ids=None):
        texts = []
        for nid, out in entry.get("outputs", {}).items():
            if node_ids and nid not in node_ids:
                continue
            texts.extend(out.get("text", []))
        return texts

    def fetch_image(self, image):
        q = urllib.parse.urlencode({"filename": image["filename"], "subfolder": image.get("subfolder", ""),
                                    "type": image.get("type", "output")})
        return self._request("/view?" + q, timeout=120)


def _summarize_error(detail):
    try:
        d = json.loads(detail)
        parts = [d.get("error", {}).get("message", "")]
        for nid, ne in (d.get("node_errors") or {}).items():
            for err in ne.get("errors", []):
                parts.append(f"node {nid} ({ne.get('class_type')}): {err.get('message')} {err.get('details', '')}")
        return "; ".join(p for p in parts if p)
    except Exception:
        return detail[:1000]


def _execution_error(status):
    for kind, data in status.get("messages", []):
        if kind == "execution_error":
            return f"{data.get('node_type')} (node {data.get('node_id')}): {data.get('exception_message', '').strip()}"
    return json.dumps(status)[:1000]


class ComfyProcess:
    """Starts ComfyUI from a portable install (or a git checkout with a venv) and stops it again."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.proc = None
        self.pid_file = cfg.runs_dir / "comfyui.pid"
        self.log_file = cfg.runs_dir / "comfyui.log"

    def launch_command(self):
        root = self.cfg.comfy_root
        port = urllib.parse.urlparse(self.cfg.comfy_url).port or 8188
        args = ["--port", str(port)] + self.cfg.comfy_extra_args
        portable_py = root / "python_embeded" / "python.exe"
        if portable_py.exists() and (root / "ComfyUI" / "main.py").exists():
            return [str(portable_py), "-s", "ComfyUI/main.py", "--windows-standalone-build"] + args, root
        for venv_py in (root / "venv" / "Scripts" / "python.exe", root / ".venv" / "Scripts" / "python.exe"):
            if venv_py.exists() and (root / "main.py").exists():
                return [str(venv_py), "main.py"] + args, root
        raise ComfyError(f"no ComfyUI found at {root} (expected python_embeded\\python.exe + ComfyUI\\main.py). "
                         "Set comfyui.root in config/config.local.json or CHARGEN_COMFYUI_ROOT.")

    def start(self, client, log=print):
        cmd, cwd = self.launch_command()
        self.cfg.runs_dir.mkdir(parents=True, exist_ok=True)
        log(f"Starting ComfyUI: {' '.join(cmd)} (log: {self.log_file})")
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        logf = open(self.log_file, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=logf, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, creationflags=flags)
        self.pid_file.write_text(str(self.proc.pid))
        deadline = time.time() + self.cfg.start_timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise ComfyError(f"ComfyUI exited with code {self.proc.returncode} during startup; see {self.log_file}")
            if client.is_up():
                log(f"ComfyUI is up (pid {self.proc.pid}).")
                return
            time.sleep(1)
        self.stop()
        raise ComfyError(f"ComfyUI did not answer within {self.cfg.start_timeout:.0f}s; see {self.log_file}")

    def stop(self, log=print):
        pid = self.proc.pid if self.proc else _read_pid(self.pid_file)
        if not pid:
            return False
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        else:
            _kill_pid(pid)
        self.pid_file.unlink(missing_ok=True)
        log(f"Stopped ComfyUI (pid {pid}).")
        return True


def _read_pid(path):
    try:
        return int(Path(path).read_text().strip())
    except Exception:
        return None


def _kill_pid(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, 15)
        except OSError:
            pass


class ComfySession:
    """Context manager: ensures ComfyUI is reachable; stops it afterwards only if this session started it."""

    def __init__(self, cfg, keep_running=False, log=print):
        self.cfg = cfg
        self.client = ComfyClient(cfg.comfy_url)
        self.process = ComfyProcess(cfg)
        self.started = False
        self.keep_running = keep_running
        self.log = log

    def __enter__(self):
        if self.client.is_up():
            return self.client
        if not self.cfg.auto_start:
            raise ComfyError(f"ComfyUI is not reachable at {self.cfg.comfy_url} and auto_start is off. "
                             "Start it (run_nvidia_gpu.bat) or enable comfyui.auto_start.")
        self.process.start(self.client, self.log)
        self.started = True
        return self.client

    def __exit__(self, *exc):
        if self.started and self.cfg.stop_if_started and not self.keep_running:
            self.process.stop(self.log)
        elif self.started:
            self.log(f"ComfyUI left running (pid {self.process.proc.pid}); stop it with: character-generator comfy stop")
        return False
