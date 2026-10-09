"""Offline regression, concurrency, cancellation, timeout and UI checks."""
from __future__ import annotations

import threading
import time

from core.multi_agent import (MultiAgentOrchestrator, WorkerManager, WorkerResult,
                              WorkerStatus)


class FakeProvider:
    name = "fake"

    def __init__(self, delay: float = 0.04, fail_once: bool = False):
        self.delay, self.fail_once, self.calls, self.timeouts = delay, fail_once, 0, []

    def run(self, model, prompt, timeout, token_budget):
        self.calls += 1
        self.timeouts.append(timeout)
        time.sleep(min(self.delay, timeout))
        if self.fail_once and self.calls == 1:
            return WorkerResult(error="temporary")
        return WorkerResult(text=f"ok:{model}", provider=self.name, model=model, tokens=9)


def make_orchestrator(provider: FakeProvider) -> MultiAgentOrchestrator:
    manager = WorkerManager({"fake": provider})
    manager.allocations = {role: ("fake", "test-model") for role in manager.allocations}
    return MultiAgentOrchestrator(manager)


def test_parallel_and_dependencies():
    provider = FakeProvider(0.08)
    orchestrator = make_orchestrator(provider)
    started = time.monotonic()
    result = orchestrator.run("test", [
        {"id": "a", "heading": "A", "instruction": "a", "role": "research"},
        {"id": "b", "heading": "B", "instruction": "b", "role": "research"},
        {"id": "c", "heading": "C", "instruction": "c", "role": "summarization", "dependencies": ["a", "b"]},
    ], max_workers=2)
    assert time.monotonic() - started < 0.22, "independent workers did not run concurrently"
    assert [row["status"] for row in result["workers"]] == ["Completed"] * 3


def test_retry_timeout_and_budget():
    provider = FakeProvider(0.001, fail_once=True)
    result = make_orchestrator(provider).run("test", [{"heading": "retry", "instruction": "x", "retries": 1, "timeout_seconds": 10, "token_budget": 256}])
    assert result["workers"][0]["status"] == WorkerStatus.COMPLETED.value
    assert provider.calls == 2
    assert provider.timeouts == [10, 10], "worker timeout was not passed to provider"
    too_many = [{"heading": str(i), "instruction": "x", "token_budget": 16000} for i in range(5)]
    result = make_orchestrator(FakeProvider()).run("test", too_many, max_workers=5)
    assert any(row["status"] == WorkerStatus.FAILED.value for row in result["workers"])


def test_cancellation():
    orchestrator = make_orchestrator(FakeProvider(0.10))
    holder = {}
    def run_slow():
        holder["result"] = orchestrator.run(
            "test", [{"heading": "slow", "instruction": "x"}]
        )
    thread = threading.Thread(target=run_slow, daemon=True)
    thread.start()
    for _ in range(50):
        if orchestrator._jobs:
            break
        time.sleep(0.005)
    assert orchestrator.cancel(next(iter(orchestrator._jobs)))
    thread.join(1)
    assert holder["result"]["workers"][0]["status"] == WorkerStatus.CANCELLED.value


if __name__ == "__main__":
    test_parallel_and_dependencies()
    test_retry_timeout_and_budget()
    test_cancellation()
    print("multi-agent diagnostics: passed")
