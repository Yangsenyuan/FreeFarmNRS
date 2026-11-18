"""后台任务管理（线程池简化版），用于预处理与反演进度跟踪。"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional

from ..core.utils import new_id, now_iso

ProgressCb = Callable[[int, str, str], None]
Worker = Callable[[ProgressCb], Any]


class JobManager:
    def __init__(self):
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    def submit(
        self,
        job_type: str,
        worker: Worker,
        project_id: Optional[str] = None,
        image_id: Optional[str] = None,
        label: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        jid = new_id("job", 5)
        job = {
            "id": jid,
            "type": job_type,
            "label": label,
            "project_id": project_id,
            "image_id": image_id,
            "status": "queued",
            "progress": 0,
            "stage": "排队中",
            "message": "等待执行",
            "created_at": now_iso(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
            "_started_ts": None,
            "_finished_ts": None,
            "extra": extra or {},
        }
        with self._lock:
            self._jobs[jid] = job
        thread = threading.Thread(target=self._run, args=(jid, worker), daemon=True)
        thread.start()
        return jid

    def _run(self, jid: str, worker: Worker) -> None:
        with self._lock:
            job = self._jobs.get(jid)
            if not job:
                return
            job["status"] = "running"
            job["started_at"] = now_iso()
            job["_started_ts"] = time.time()
            job["stage"] = "开始"
            job["message"] = "处理中…"

        def progress(percent: int, stage: str, message: str = "") -> None:
            with self._lock:
                j = self._jobs.get(jid)
                if not j:
                    return
                j["progress"] = max(0, min(100, int(percent)))
                j["stage"] = stage or j["stage"]
                if message:
                    j["message"] = message

        try:
            result = worker(progress)
            with self._lock:
                job = self._jobs.get(jid)
                if job:
                    job["status"] = "completed"
                    job["progress"] = 100
                    job["finished_at"] = now_iso()
                    job["_finished_ts"] = time.time()
                    job["stage"] = "完成"
                    job["message"] = "任务完成"
                    job["result"] = result
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            with self._lock:
                job = self._jobs.get(jid)
                if job:
                    job["status"] = "failed"
                    job["finished_at"] = now_iso()
                    job["_finished_ts"] = time.time()
                    job["stage"] = "失败"
                    job["error"] = str(exc) or "未知错误"
                    job["message"] = str(exc) or "任务失败"
        finally:
            self._prune()

    def _prune(self) -> None:
        with self._lock:
            if len(self._jobs) <= 120:
                return
            finished = [
                (j.get("finished_at") or "", jid)
                for jid, j in self._jobs.items()
                if j.get("status") in ("completed", "failed")
            ]
            finished.sort()
            for _, jid in finished[: len(self._jobs) - 100]:
                self._jobs.pop(jid, None)

    def get(self, jid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(jid)
            if not job:
                return None
            data = dict(job)
            data.pop("_started_ts", None)
            data.pop("_finished_ts", None)
            started = job.get("_started_ts")
            finished = job.get("_finished_ts")
            if started:
                end = finished if finished else time.time()
                data["elapsed"] = round(end - started, 1)
            return data

    def list(
        self, project_id: Optional[str] = None, active_only: bool = False
    ) -> List[Dict[str, Any]]:
        with self._lock:
            jobs = []
            for jid in self._jobs:
                job = self._jobs[jid]
                if project_id and job.get("project_id") != project_id:
                    continue
                if active_only and job.get("status") not in ("queued", "running"):
                    continue
                jobs.append(jid)
            jobs.sort(key=lambda j: self._jobs[j].get("created_at") or "", reverse=True)
            return [self.get(j) for j in jobs]


jobs = JobManager()
