"""Bounded worker execution controlled by the Live Gemini lead agent."""
from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Protocol
from urllib import request

from memory.config_manager import get_external_providers, load_api_keys


class WorkerStatus(str, Enum):
    QUEUED = "Queued"; PLANNING = "Planning"; RUNNING = "Running"; WAITING = "Waiting"
    VERIFYING = "Verifying"; COMPLETED = "Completed"; FAILED = "Failed"; CANCELLED = "Cancelled"


@dataclass
class WorkerTask:
    heading: str
    instruction: str
    role: str = "reasoning"
    dependencies: list[str] = field(default_factory=list)
    priority: int = 0
    timeout_seconds: int = 60
    retries: int = 1
    token_budget: int = 8000
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


@dataclass
class WorkerResult:
    text: str = ""
    provider: str = ""
    model: str = ""
    tokens: int = 0
    error: str = ""


@dataclass
class WorkerRecord:
    task: WorkerTask
    status: WorkerStatus = WorkerStatus.QUEUED
    result: WorkerResult = field(default_factory=WorkerResult)
    started_at: float = 0.0
    finished_at: float = 0.0
    attempt: int = 0


class WorkerProvider(Protocol):
    name: str
    def run(self, model: str, prompt: str, timeout: int, token_budget: int) -> WorkerResult: ...


class OpenRouterWorkerProvider:
    name = "openrouter"
    def run(self, model: str, prompt: str, timeout: int, token_budget: int) -> WorkerResult:
        cfg = get_external_providers()["openrouter"]
        if not cfg.get("enabled") or not cfg.get("api_key"):
            return WorkerResult(error="OpenRouter is not enabled.")
        body = json.dumps({"model": model, "temperature": 0.2,
            "max_tokens": max(256, min(token_budget, 16000)), "messages": [
            {"role": "system", "content": "You are an isolated worker. Return evidence and a concise result only. You cannot use tools or communicate with the user."},
            {"role": "user", "content": prompt[:12000]},
        ]}).encode()
        req = request.Request("https://openrouter.ai/api/v1/chat/completions", body, method="POST",
            headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"})
        try:
            with request.urlopen(req, timeout=max(5, timeout)) as response:
                data = json.loads(response.read().decode())
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list): content = "".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
            usage = data.get("usage") or {}
            return WorkerResult(str(content).strip(), self.name, str(data.get("model") or model), int(usage.get("total_tokens") or 0))
        except Exception as exc:
            return WorkerResult(error=f"{type(exc).__name__}: {str(exc)[:160]}")


class GeminiWorkerProvider:
    """Default-provider fallback when OpenRouter is unavailable or out of credit."""
    name = "gemini"
    def run(self, model: str, prompt: str, timeout: int, token_budget: int) -> WorkerResult:
        try:
            from core import gemini
            text = gemini.text(prompt[:12000], gemini.SMART, None, max(10_000, timeout * 1000))
            return WorkerResult(str(text or "").strip(), self.name, "Gemini", 0,
                                "" if text else "Gemini returned no result.")
        except Exception as exc:
            return WorkerResult(error=f"Gemini fallback failed: {type(exc).__name__}: {str(exc)[:160]}")


class WorkerManager:
    """Provider-agnostic allocator. New providers register one WorkerProvider."""
    def __init__(self, providers: dict[str, WorkerProvider] | None = None):
        self.providers = providers or {"openrouter": OpenRouterWorkerProvider(), "gemini": GeminiWorkerProvider()}
        self.allocations = self._allocations()

    @staticmethod
    def _allocations() -> dict[str, tuple[str, str]]:
        raw = load_api_keys().get("worker_models")
        raw = raw if isinstance(raw, dict) else {}
        default = str(get_external_providers()["openrouter"].get("model") or "openrouter/auto")
        allocations = {}
        for role in ("reasoning", "coding", "research", "vision", "summarization", "planning"):
            choice = raw.get(role) or default
            if isinstance(choice, dict):
                allocations[role] = (str(choice.get("provider") or "openrouter"), str(choice.get("model") or default))
            else:
                allocations[role] = ("openrouter", str(choice))
        return allocations

    def register(self, provider: WorkerProvider) -> None:
        """Future provider adapters plug in here; orchestration stays unchanged."""
        self.providers[provider.name] = provider

    def execute(self, task: WorkerTask, dependency_context: str) -> WorkerResult:
        provider_name, model = self.allocations.get(task.role.lower(), self.allocations["reasoning"])
        provider = self.providers.get(provider_name)
        if provider is None: return WorkerResult(error=f"Provider '{provider_name}' is unavailable.")
        prompt = f"Task: {task.heading}\nRole: {task.role}\nInstruction: {task.instruction}\n"
        if dependency_context: prompt += "Dependency evidence:\n" + dependency_context[:6000]
        result = provider.run(model, prompt, task.timeout_seconds, task.token_budget)
        if result.error and provider_name == "openrouter":
            fallback = self.providers.get("gemini")
            if fallback:
                return fallback.run("Gemini", prompt, task.timeout_seconds, task.token_budget)
        return result


class MultiAgentOrchestrator:
    MAX_WORKERS = 5
    MAX_TOKENS = 50000
    def __init__(self, manager: WorkerManager | None = None):
        self.manager = manager or WorkerManager(); self._jobs: dict[str, dict] = {}; self._lock = threading.Lock()

    def run(self, objective: str, tasks: list[dict], max_workers: int = 3,
            notify: Callable[[dict], None] | None = None) -> dict:
        job_id, cancelled = uuid.uuid4().hex[:10], threading.Event()
        records = [WorkerRecord(WorkerTask(heading=str(t.get("heading") or "Worker task"),
            instruction=str(t.get("instruction") or objective), role=str(t.get("role") or "reasoning"),
            dependencies=list(t.get("dependencies") or []), priority=int(t.get("priority") or 0),
            timeout_seconds=max(10, min(int(t.get("timeout_seconds") or 60), 180)), retries=max(0, min(int(t.get("retries") or 1), 2)),
            token_budget=max(256, min(int(t.get("token_budget") or 8000), 16000)),
            id=str(t.get("id") or uuid.uuid4().hex[:8]))) for t in tasks[:self.MAX_WORKERS]]
        with self._lock: self._jobs[job_id] = {"cancel": cancelled, "records": records}
        def snapshot():
            now = time.monotonic(); rows = []
            for r in records:
                elapsed = (r.finished_at or now) - r.started_at if r.started_at else 0
                provider, model = self.manager.allocations.get(r.task.role.lower(), ("openrouter", ""))
                progress = {WorkerStatus.QUEUED: 0, WorkerStatus.PLANNING: 10,
                    WorkerStatus.RUNNING: 50, WorkerStatus.WAITING: 60,
                    WorkerStatus.VERIFYING: 85, WorkerStatus.COMPLETED: 100,
                    WorkerStatus.FAILED: 100, WorkerStatus.CANCELLED: 100}[r.status]
                rows.append({"id": r.task.id, "heading": r.task.heading, "provider": r.result.provider or provider, "model": r.result.model or model, "status": r.status.value, "elapsed_seconds": round(elapsed, 1), "tokens": r.result.tokens, "progress": progress, "diagnostic": r.result.error or r.result.text[:800]})
            return {"job_id": job_id, "objective": objective[:120], "workers": rows, "idle": all(r.status in (WorkerStatus.COMPLETED, WorkerStatus.FAILED, WorkerStatus.CANCELLED) for r in records)}
        def emit():
            if notify: notify(snapshot())
        def work(record: WorkerRecord):
            record.started_at = time.monotonic(); record.status = WorkerStatus.PLANNING; emit()
            context = "\n\n".join(f"{r.task.heading}: {r.result.text[:1500]}" for r in records if r.task.id in record.task.dependencies and r.status == WorkerStatus.COMPLETED)
            for attempt in range(record.task.retries + 1):
                if cancelled.is_set(): record.status = WorkerStatus.CANCELLED; break
                record.attempt = attempt + 1; record.status = WorkerStatus.RUNNING; emit()
                record.result = self.manager.execute(record.task, context)
                if cancelled.is_set():
                    record.status = WorkerStatus.CANCELLED
                    break
                if not record.result.error and record.result.text.strip():
                    record.status = WorkerStatus.VERIFYING; emit()
                    # Worker-level verification is intentionally structural.
                    # Gemini remains the final semantic verifier and merger.
                    record.status = WorkerStatus.COMPLETED; break
                if not record.result.error: record.result.error = "Worker returned no verifiable result."
                record.status = WorkerStatus.WAITING if attempt < record.task.retries else WorkerStatus.FAILED; emit()
            record.finished_at = time.monotonic(); emit()
        pending, active = sorted(records, key=lambda r: -r.task.priority), {}
        reserved_tokens = 0
        limit = max(1, min(int(max_workers or 3), self.MAX_WORKERS)); emit()
        with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="arienx-worker") as pool:
            while pending or active:
                if cancelled.is_set():
                    for r in pending: r.status = WorkerStatus.CANCELLED
                    pending.clear(); emit()
                completed_ids = {r.task.id for r in records if r.status == WorkerStatus.COMPLETED}
                ready = [r for r in pending if set(r.task.dependencies).issubset(completed_ids)]
                slots = max(0, limit-len(active))
                for r in ready:
                    if not slots:
                        break
                    if reserved_tokens + r.task.token_budget > self.MAX_TOKENS:
                        r.status = WorkerStatus.FAILED; r.result.error = "Job token budget exceeded."
                        pending.remove(r); emit(); continue
                    pending.remove(r); reserved_tokens += r.task.token_budget
                    active[pool.submit(work, r)] = r; slots -= 1
                if not active:
                    for r in pending: r.status = WorkerStatus.FAILED; r.result.error = "Dependency failed or is missing."
                    pending.clear(); emit(); break
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done: active.pop(future); future.result()
        return snapshot()

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job: return False
            job["cancel"].set(); return True


_ORCHESTRATOR = MultiAgentOrchestrator()
def get_orchestrator() -> MultiAgentOrchestrator: return _ORCHESTRATOR
