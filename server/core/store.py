"""项目制存储：每个项目一个目录，内含 project.json / 影像 / 结果 / 日志。"""
from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROJECTS_DIR
from .utils import new_id, now_iso, read_json, write_json

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ProjectNotFound(KeyError):
    pass


class ImageNotFound(KeyError):
    pass


class ProjectStore:
    def __init__(self, root: Path = PROJECTS_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    # ---------- 基础路径 ----------
    def _check_pid(self, pid: str) -> str:
        if not _ID_RE.match(pid or ""):
            raise ProjectNotFound(pid)
        return pid

    def project_dir(self, pid: str) -> Path:
        return self.root / self._check_pid(pid)

    def image_dir(self, pid: str, iid: str) -> Path:
        if not _ID_RE.match(iid or ""):
            raise ImageNotFound(iid)
        return self.project_dir(pid) / "images" / iid

    def project_file(self, pid: str) -> Path:
        return self.project_dir(pid) / "project.json"

    def logs_path(self, pid: str) -> Path:
        return self.project_dir(pid) / "logs" / "project.jsonl"

    # ---------- 项目 ----------
    def list_projects(self) -> List[Dict[str, Any]]:
        projects: List[Dict[str, Any]] = []
        if not self.root.exists():
            return projects
        for entry in self.root.iterdir():
            if not entry.is_dir() or not _ID_RE.match(entry.name):
                continue
            data = read_json(entry / "project.json")
            if isinstance(data, dict) and data.get("id"):
                projects.append(data)
        projects.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
        return projects

    def create_project(
        self,
        name: str,
        description: str = "",
        crop: str = "",
        field: str = "",
        note: str = "",
        project_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            pid = project_id or new_id("proj", 5)
            self._check_pid(pid)
            pdir = self.project_dir(pid)
            if pdir.exists():
                raise FileExistsError(pid)
            pdir.mkdir(parents=True, exist_ok=True)
            (pdir / "images").mkdir(exist_ok=True)
            (pdir / "logs").mkdir(exist_ok=True)
            now = now_iso()
            project = {
                "id": pid,
                "name": (name or "未命名项目").strip(),
                "description": description or "",
                "crop": crop or "",
                "field": field or "",
                "note": note or "",
                "created_at": now,
                "updated_at": now,
                "images": [],
            }
            write_json(self.project_file(pid), project)
            self.append_log(pid, f"创建项目「{project['name']}」", source="project")
            return project

    def get_project(self, pid: str) -> Dict[str, Any]:
        data = read_json(self.project_file(pid))
        if not isinstance(data, dict):
            raise ProjectNotFound(pid)
        data.setdefault("images", [])
        return data

    def save_project(self, project: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            pid = self._check_pid(project.get("id", ""))
            project["updated_at"] = now_iso()
            write_json(self.project_file(pid), project)
            return project

    def update_project(self, pid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            project = self.get_project(pid)
            for key in ("name", "description", "crop", "field", "note"):
                if key in patch and patch[key] is not None:
                    project[key] = patch[key]
            self.save_project(project)
            return project

    def delete_project(self, pid: str) -> None:
        with self._lock:
            pdir = self.project_dir(pid)
            if not pdir.exists():
                raise ProjectNotFound(pid)
            if pdir.parent != self.root:
                raise ValueError("非法的项目路径")
            shutil.rmtree(pdir)

    # ---------- 影像 ----------
    def add_image(self, pid: str, image: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            project = self.get_project(pid)
            iid = image.get("id") or new_id("img", 5)
            image["id"] = iid
            image.setdefault("imported_at", now_iso())
            image.setdefault("status", "imported")
            project["images"].append(image)
            self.save_project(project)
            return image

    def get_image(self, pid: str, iid: str) -> Dict[str, Any]:
        project = self.get_project(pid)
        for image in project.get("images", []):
            if image.get("id") == iid:
                return image
        raise ImageNotFound(iid)

    def update_image(self, pid: str, iid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            project = self.get_project(pid)
            for image in project.get("images", []):
                if image.get("id") == iid:
                    image.update(patch)
                    image["updated_at"] = now_iso()
                    self.save_project(project)
                    return image
            raise ImageNotFound(iid)

    def remove_image(self, pid: str, iid: str) -> None:
        with self._lock:
            project = self.get_project(pid)
            before = len(project.get("images", []))
            project["images"] = [im for im in project.get("images", []) if im.get("id") != iid]
            if len(project["images"]) == before:
                raise ImageNotFound(iid)
            self.save_project(project)
            idir = self.image_dir(pid, iid)
            if idir.exists() and idir.parent == self.project_dir(pid) / "images":
                shutil.rmtree(idir, ignore_errors=True)

    # ---------- 日志 ----------
    def append_log(
        self, pid: str, message: str, level: str = "info", source: str = "system"
    ) -> None:
        path = self.logs_path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "time": now_iso(),
            "level": level,
            "source": source,
            "message": message,
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def read_logs(self, pid: str, limit: int = 200) -> List[Dict[str, Any]]:
        path = self.logs_path(pid)
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return records[-max(1, min(limit, 1000)):][::-1]

    # ---------- 结果摘要历史（多期对比） ----------
    def results_history_path(self, pid: str) -> Path:
        return self.project_dir(pid) / "results_history.json"

    def append_result_summary(self, pid: str, summary: Dict[str, Any]) -> None:
        with self._lock:
            path = self.results_history_path(pid)
            data = read_json(path, default=[]) or []
            data.append(summary)
            write_json(path, data[-500:])

    def read_results_history(self, pid: str) -> List[Dict[str, Any]]:
        data = read_json(self.results_history_path(pid), default=[]) or []
        return data if isinstance(data, list) else []


store = ProjectStore()
