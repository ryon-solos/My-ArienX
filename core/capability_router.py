"""
core/capability_router.py — Capability routing and action mapping.

Maps intents/entities to capabilities, then capabilities to concrete actions.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from core.capability import Capability, CapabilityRegistry, get_capability_registry
from core.environment import EnvironmentContext


@dataclass
class RoutingDecision:
    """Result of routing an intent to a concrete action."""
    capability: str
    action: str
    args: Dict[str, Any]
    verification: str
    recovery_hints: List[str] = field(default_factory=list)
    requires_confirmation: bool = False
    reasoning: str = ""


class CapabilityRouter:
    """
    Routes intents/entities to concrete actions via capabilities.
    
    Does NOT make the model choose tools directly.
    The model describes intent; the router maps to capabilities -> actions.
    """
    
    def __init__(self, env: EnvironmentContext):
        self.env = env
        self.registry = get_capability_registry()
    
    def route(self, intent: str, entities: Dict[str, str]) -> List[RoutingDecision]:
        """Route an intent to one or more concrete actions."""
        caps = self.registry.get_for_intent(intent, entities, self.env)
        
        if not caps:
            return [RoutingDecision(
                capability="unknown",
                action="",
                args={},
                verification="none",
                reasoning=f"No capability found for intent: {intent}"
            )]
        
        decisions = []
        for cap in caps:
            decision = self._map_capability_to_action(cap, intent, entities)
            if decision:
                decisions.append(decision)
        
        return decisions
    
    def _map_capability_to_action(self, cap, intent: str, entities: Dict[str, str]):
        action = self._resolve_action(cap, entities)
        if not action:
            return None
        
        args = self._build_args(cap, entities, action)
        
        return RoutingDecision(
            capability=cap.name,
            action=action,
            args=args,
            verification=cap.verification,
            recovery_hints=cap.recovery_hints,
            requires_confirmation=cap.requires_confirmation,
            reasoning=f"Mapped intent to {cap.name} -> {action}"
        )
    
    def _resolve_action(self, cap, entities: Dict[str, str]):
        for key, action in cap.action_mapping.items():
            for entity_val in entities.values():
                if key.lower() in entity_val.lower():
                    return action
        return cap.action_mapping.get("default")
    
    def _build_args(self, cap, entities: Dict[str, str], action: str) -> Dict[str, Any]:
        args = {}
        
        if action == "open_app":
            for key in ("app", "application", "program", "name"):
                if key in entities:
                    args["app_name"] = entities[key]
                    break
            if "app_name" not in args:
                for key, val in entities.items():
                    if key in ("app", "application", "program") or "app" in key.lower():
                        args["app_name"] = val
                        break
        
        elif action == "browser_control":
            args["action"] = entities.get("action", "go_to")
            if "url" in entities:
                args["url"] = entities["url"]
            elif "query" in entities:
                args["query"] = entities["query"]
                args["action"] = "search"
            if "browser" in entities:
                args["browser"] = entities["browser"]
        
        elif action == "youtube_video":
            args["action"] = entities.get("action", "play")
            if "query" in entities:
                args["query"] = entities["query"]
            if "url" in entities:
                args["url"] = entities["url"]
        
        elif action == "web_search":
            args["query"] = entities.get("query", "")
            args["mode"] = entities.get("mode", "search")
            if "items" in entities:
                args["items"] = entities["items"]
            if "aspect" in entities:
                args["aspect"] = entities["aspect"]
        
        elif action == "computer_control":
            args["action"] = entities.get("action", "click")
            for key in ("x", "y", "x1", "y1", "x2", "y2", "duration", "button", "keys", "key", "direction", "amount", "text", "description", "title"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "open_app":
            if "app_name" in entities:
                args["app_name"] = entities["app_name"]
            elif "app" in entities:
                args["app_name"] = entities["app"]
        
        elif action == "web_search":
            args["query"] = entities.get("query", "")
            args["mode"] = entities.get("mode", "search")
            if "items" in entities:
                args["items"] = entities["items"]
            if "aspect" in entities:
                args["aspect"] = entities["aspect"]
        
        elif action == "computer_settings":
            args["action"] = entities.get("action", "")
            if "value" in entities:
                args["value"] = entities["value"]
            if "description" in entities:
                args["description"] = entities["description"]
        
        elif action == "file_controller":
            args["action"] = entities.get("action", "")
            for key in ("path", "destination", "new_name", "content", "name", "extension", "count"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "file_processor":
            if "file_path" in entities:
                args["file_path"] = entities["file_path"]
            args["action"] = entities.get("action", "")
            for key in ("instruction", "format", "width", "height", "scale", "quality", "start", "end", "timestamp", "column", "value", "condition", "ascending", "save", "destination"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "send_message":
            for key in ("receiver", "message_text", "platform"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "weather_report":
            args["city"] = entities.get("city", "")
            if "time" in entities:
                args["time"] = entities["time"]
        
        elif action == "flight_finder":
            for key in ("origin", "destination", "date", "return_date", "passengers", "cabin", "save"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "game_updater":
            for key in ("action", "platform", "game_name", "app_id", "hour", "minute", "shutdown_when_done"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "dev_agent":
            for key in ("description", "language", "project_name", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "code_helper":
            for key in ("action", "description", "language", "output_path", "file_path", "code", "args", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "weather_report":
            args["city"] = entities.get("city", "")
            if "time" in entities:
                args["time"] = entities["time"]
        
        elif action == "flight_finder":
            for key in ("origin", "destination", "date", "return_date", "passengers", "cabin", "save"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "game_updater":
            for key in ("action", "platform", "game_name", "app_id", "hour", "minute", "shutdown_when_done"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "dev_agent":
            for key in ("description", "language", "project_name", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "code_helper":
            for key in ("action", "description", "language", "output_path", "file_path", "code", "args", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "weather_report":
            args["city"] = entities.get("city", "")
            if "time" in entities:
                args["time"] = entities["time"]
        
        elif action == "flight_finder":
            for key in ("origin", "destination", "date", "return_date", "passengers", "cabin", "save"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "game_updater":
            for key in ("action", "platform", "game_name", "app_id", "hour", "minute", "shutdown_when_done"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "dev_agent":
            for key in ("description", "language", "project_name", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "code_helper":
            for key in ("action", "description", "language", "output_path", "file_path", "code", "args", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "weather_report":
            args["city"] = entities.get("city", "")
            if "time" in entities:
                args["time"] = entities["time"]
        
        elif action == "flight_finder":
            for key in ("origin", "destination", "date", "return_date", "passengers", "cabin", "save"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "game_updater":
            for key in ("action", "platform", "game_name", "app_id", "hour", "minute", "shutdown_when_done"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "reminder":
            for key in ("date", "time", "message"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "dev_agent":
            for key in ("description", "language", "project_name", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        elif action == "code_helper":
            for key in ("action", "description", "language", "output_path", "file_path", "code", "args", "timeout"):
                if key in entities:
                    args[key] = entities[key]
        
        return args


_router = None


def get_router(env) -> "CapabilityRouter":
    global _router
    if _router is None or _router.env != env:
        _router = CapabilityRouter(env)
    return _router
