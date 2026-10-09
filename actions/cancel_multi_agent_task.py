from core.multi_agent import get_orchestrator

def cancel_multi_agent_task(parameters: dict, **_kwargs) -> str:
    job_id = str((parameters or {}).get("job_id") or "")
    return "Cancellation requested." if get_orchestrator().cancel(job_id) else "No active worker job found."

TOOL = {"name": "cancel_multi_agent_task", "description": "Cancel an active multi-agent worker job by its job id.", "parameters": {"type": "OBJECT", "properties": {"job_id": {"type": "STRING"}}, "required": ["job_id"]}, "handler": cancel_multi_agent_task}
