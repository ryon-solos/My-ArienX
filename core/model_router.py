"""Role-based model selection with deterministic, bounded fallback."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .model_provider import ModelConfig

ROLES = ("LIVE", "FAST", "REASONING", "RESEARCH", "CODING", "VISION")


@dataclass(frozen=True)
class ModelDescriptor:
    provider: str
    model: str
    roles: frozenset[str] = field(default_factory=lambda: frozenset(ROLES))
    available: bool = True
    priority: int = 0
    cost_class: str = "default"
    latency_class: str = "default"
    capabilities: frozenset[str] = field(
        default_factory=lambda: frozenset({"text", "tools"})
    )

    def supports(self, role: str) -> bool:
        return role.upper() in self.roles and self.available


@dataclass(frozen=True)
class ModelRoute:
    role: str
    reason: str
    descriptor: ModelDescriptor | None
    fallback_available: bool = False

    @property
    def provider(self) -> str:
        return self.descriptor.provider if self.descriptor else ""

    @property
    def model(self) -> str:
        return self.descriptor.model if self.descriptor else ""


class ModelRouter:
    """Selects a configured model role; it never performs inference itself."""

    def __init__(self, descriptors: Iterable[ModelDescriptor] = ()):
        self._descriptors = list(descriptors)

    @classmethod
    def single_gemini(cls, model: str) -> "ModelRouter":
        return cls([ModelDescriptor(
            provider="gemini", model=model,
            roles=frozenset(ROLES),
            capabilities=frozenset({"text", "tools", "vision", "structured"}),
        )])

    def register(self, descriptor: ModelDescriptor) -> None:
        self._descriptors.append(descriptor)

    def select(self, role: str, reason: str = "default") -> ModelRoute:
        role = (role or "LIVE").upper()
        if role not in ROLES:
            role = "LIVE"
        ordered = sorted(self._descriptors, key=lambda d: d.priority, reverse=True)
        exact = [d for d in ordered if d.supports(role)]
        chosen = exact[0] if exact else next((d for d in ordered if d.available), None)
        fallback = bool(chosen and (len(exact) > 1 or not exact))
        if chosen is None:
            return ModelRoute(role, "unavailable", None, False)
        return ModelRoute(role, reason, chosen, fallback)

    def config_for(self, route: ModelRoute, **overrides) -> ModelConfig | None:
        if not route.descriptor:
            return None
        values = {
            "provider": route.provider,
            "model": route.model,
            "supports_tools": "tools" in route.descriptor.capabilities,
            "supports_vision": "vision" in route.descriptor.capabilities,
        }
        values.update(overrides)
        return ModelConfig(**values)
