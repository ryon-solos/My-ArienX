"""Gemini Live submits a dependency plan; workers never control local tools."""
from core.multi_agent import get_orchestrator

def multi_agent_task(parameters: dict, player=None, **_kwargs) -> str:
    p = parameters or {}; objective = str(p.get("objective") or "").strip(); tasks = p.get("tasks") or []
    if not objective or not isinstance(tasks, list): return "A multi-agent plan needs an objective and task list."
    def notify(snapshot):
        if player: player.show_worker_monitor(snapshot)
    result = get_orchestrator().run(objective, tasks, p.get("max_workers", 3), notify)
    return "[LEAD AGENT WORKER RESULTS]\n" + "\n\n".join(f"{w['heading']} ({w['provider']}/{w['model']}, {w['status']}): {w['diagnostic']}" for w in result["workers"])

_TASK = {"type": "OBJECT", "properties": {"objective": {"type": "STRING"}, "max_workers": {"type": "INTEGER"}, "tasks": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "heading": {"type": "STRING"}, "instruction": {"type": "STRING"}, "role": {"type": "STRING"}, "dependencies": {"type": "ARRAY", "items": {"type": "STRING"}}, "priority": {"type": "INTEGER"}, "timeout_seconds": {"type": "INTEGER"}, "retries": {"type": "INTEGER"}, "token_budget": {"type": "INTEGER"}}, "required": ["heading", "instruction"]}}}, "required": ["objective", "tasks"]}
TOOL = {"name": "multi_agent_task", "description": "Lead-only orchestration for complex work. Gemini creates dependency-aware worker tasks; independent tasks run in parallel and dependent tasks run after evidence is available. Workers only return isolated evidence; Gemini alone talks to the user and calls local/browser tools.", "parameters": _TASK, "handler": multi_agent_task}
