"""
core/model_provider.py — Model provider abstraction.

Abstract base class for LLM providers (Gemini, OpenRouter, Ollama, OpenAI-compatible).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, AsyncGenerator, Optional

@dataclass
class ModelConfig:
    """Configuration for a model call."""
    provider: str                    # "gemini", "openrouter", "ollama", "openai"
    model: str
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 8192
    supports_tools: bool = True
    supports_streaming: bool = True
    supports_vision: bool = False


class ModelProvider(ABC):
    """Abstract base class for LLM providers."""
    
    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        config: ModelConfig
    ) -> Dict[str, Any]:
        """
        Single call, returns {"text": ..., "tool_calls": [...], "usage": ...}
        """
        pass
    
    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        config: ModelConfig
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Streaming call. Yields {"delta_text": ..., "delta_tool_calls": ..., "done": bool}
        """
        yield
    
    def count_tokens(self, text: str) -> int:
        """Estimate token count. Override for provider-specific counting."""
        # Rough approximation: 1 token ≈ 4 chars
        return len(text) // 4
    
    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model name."""
        pass
    
    def supports_tool_calling(self) -> bool:
        """Whether this provider supports tool/function calling."""
        return True
    
    def supports_vision(self) -> bool:
        """Whether this provider supports vision/multimodal input."""
        return False
    
    def health_check(self) -> bool:
        """Check if the provider is available."""
        return True


class ModelProviderRegistry:
    """Registry of available model providers."""
    
    def __init__(self):
        self._providers: Dict[str, type] = {}
        self._instances: Dict[str, "ModelProvider"] = {}
    
    def register(self, name: str, provider_class: type) -> None:
        """Register a provider class."""
        self._providers[name.lower()] = provider_class
    
    def get(self, name: str, config) -> "ModelProvider":
        """Get or create a provider instance."""
        name = name.lower()
        if name in self._instances:
            return self._instances[name]
        
        if name not in self._providers:
            raise ValueError(f"Unknown provider: {name}. Available: {list(self._providers.keys())}")
        
        provider_class = self._providers[name]
        instance = provider_class(config)
        self._instances[name] = instance
        return instance
    
    def list_providers(self) -> List[str]:
        return list(self._providers.keys())


# Global registry
_provider_registry = ModelProviderRegistry()


def get_provider_registry() -> ModelProviderRegistry:
    return _provider_registry


# ──────────────────────────────────────────────────────────────────
# Built-in providers (lazy imports to avoid hard dependencies)
# ──────────────────────────────────────────────────────────────────

def _register_builtin_providers(registry: ModelProviderRegistry) -> None:
    """Register built-in providers. Called on first use."""
    
    # Gemini (existing)
    try:
        from core.model_provider_gemini import GeminiProvider
        registry.register("gemini", GeminiProvider)
    except ImportError:
        pass
    
    # OpenRouter / OpenAI-compatible
    try:
        from core.model_provider_openrouter import OpenRouterProvider
        registry.register("openrouter", OpenRouterProvider)
        registry.register("openai", OpenRouterProvider)
        registry.register("lmstudio", OpenRouterProvider)
        registry.register("localai", OpenRouterProvider)
        registry.register("vllm", OpenRouterProvider)
    except ImportError:
        pass
    
    # Ollama
    try:
        from core.model_provider_ollama import OllamaProvider
        registry.register("ollama", OllamaProvider)
    except ImportError:
        pass
    
    # Anthropic
    try:
        from core.model_provider_anthropic import AnthropicProvider
        registry.register("anthropic", AnthropicProvider)
    except ImportError:
        pass


def get_provider_registry() -> ModelProviderRegistry:
    """Get the global provider registry, auto-registering built-ins."""
    _register_builtin_providers(_provider_registry)
    return _provider_registry
