"""Fast, side-effect-free checks for the AgentCore coordinator."""

from __future__ import annotations

from core.agent_core import AgentCore
from core.app_discovery import resolve_app
from core.echo import barge_fallback_candidate
from core.model_router import ModelDescriptor, ModelRouter


def run() -> None:
    logs: list[str] = []
    agent = AgentCore(logger=logs.append, environment_fn=lambda: None)

    conversation = agent.understand("How are you?")
    assert not conversation.needs_action

    launch = agent.understand("Open Notepad.")
    assert launch.intent == "launch_application"
    assert launch.needs_action and launch.mode == "fast"

    prepared = agent.prepare_tool("open_app", {"app_name": "Notepad"})
    discovered_editor = resolve_app("text_editor")
    if discovered_editor:
        assert prepared["app_name"] == discovered_editor

    verified = agent.observe("open_app", "Opened editor (verified: editor running).")
    assert verified.status == "VERIFIED_SUCCESS"
    assert "latest_observation=Opened editor" in agent.awareness_directive()

    assert agent.can_recover("try_alternative_editor")
    assert not agent.can_recover("try_alternative_editor")
    assert barge_fallback_candidate(0.15, 0.06, 0.80)
    assert not barge_fallback_candidate(0.15, 0.06, 0.99)

    route = agent.model_route(agent.understand("Research the latest developments in AI and compare sources."))
    assert route.role == "RESEARCH" and route.provider == "gemini"

    camera = agent.understand("What are you seeing now?")
    assert camera.model_role == "VISION"
    agent.vision.set_active("camera", True)
    assert "angle=camera" in agent.vision_directive(camera)

    assert agent.understand("Tell me my system stats.").intent == "system_status"
    assert "system_status" in agent.execution_directive()
    assert agent.understand("Just open YouTube.").intent == "youtube"
    agent.understand("Just open YouTube.")
    assert agent.prepare_tool("youtube_video", {})["action"] == "open"
    seeing = agent.understand("Can you see now?")
    assert seeing.intent == "vision" and seeing.model_role == "VISION"

    fallback_router = ModelRouter([
        ModelDescriptor("offline", "fast-model", frozenset({"FAST"}), available=True),
    ])
    fallback = fallback_router.select("REASONING", "recovery")
    assert fallback.descriptor is not None
    assert fallback.provider == "offline"

    print("AgentCore diagnostics passed")


if __name__ == "__main__":
    run()
