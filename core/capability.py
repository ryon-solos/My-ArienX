"""
core/capability.py — Capability registry.

Defines logical capabilities that the agent can perform.
Each capability maps to concrete actions with verification/recovery strategies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Dict, Optional, Any
from core.environment import EnvironmentContext


@dataclass
class Capability:
    """
    A logical capability the agent can perform.
    
    Capabilities are high-level (semantic) — the model reasons about these,
    not about low-level tools. The capability router maps them to concrete
    actions based on the current environment.
    """
    name: str                              # e.g., "launch_application"
    description: str                       # Human-readable description
    category: str                          # "system", "web", "media", "files", "ui"
    
    # Availability check: returns True if this capability can be used in the given environment
    availability: Callable[[EnvironmentContext], bool] = lambda env: True
    
    # Prerequisites that must be met (e.g., specific apps installed)
    prerequisites: List[str] = field(default_factory=list)
    
    # Cost hint for planner
    cost: str = "low"                      # "low" | "medium" | "high"
    
    # Verification strategy name (implemented in verifiers)
    verification: str = "none"             # "process_check" | "window_detect" | "content_diff" | "url_match" | "none"
    
    # Whether confirmation gate is required
    requires_confirmation: bool = False
    
    # Verification timeout in seconds
    verification_timeout: float = 10.0
    
    # Recovery hints for when verification fails
    recovery_hints: List[str] = field(default_factory=list)
    
    # Maps to concrete action names (tool names)
    action_mapping: Dict[str, str] = field(default_factory=dict)  # env_key -> action_name


class CapabilityRegistry:
    """Registry of all available capabilities."""
    
    def __init__(self):
        self._capabilities: Dict[str, Capability] = {}
        self._register_builtins()
    
    def _register_builtins(self) -> None:
        """Register all built-in capabilities."""
        
        # ── Application Launching ──
        self.register(Capability(
            name="launch_application",
            description="Launch a desktop application by name or generic type",
            category="system",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="process_check",
            recovery_hints=["try_alternative_editor", "check_install", "try_flatpak", "try_snap"],
            action_mapping={
                "text_editor": "open_app",
                "browser": "open_app",
                "terminal": "open_app",
                "file_manager": "open_app",
                "image_viewer": "open_app",
                "pdf_viewer": "open_app",
                "media_player": "open_app",
                "ide": "open_app",
                "default": "open_app",
            },
        ))
        
        # ── Web Browsing ──
        self.register(Capability(
            name="browse_website",
            description="Navigate to a URL in a web browser",
            category="web",
            availability=lambda env: bool(env.browsers),
            prerequisites=["browser"],
            cost="low",
            verification="url_match",
            recovery_hints=["try_alternative_browser", "check_url", "try_native_open"],
            action_mapping={"default": "browser_control"},
        ))
        
        self.register(Capability(
            name="search_web",
            description="Search the web for current information",
            category="web",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_check",
            recovery_hints=["try_alternative_query", "try_different_engine"],
            action_mapping={"default": "web_search"},
        ))
        
        # ── YouTube ──
        self.register(Capability(
            name="search_youtube",
            description="Search YouTube for videos",
            category="media",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_check",
            recovery_hints=["try_alternative_query"],
            action_mapping={"default": "youtube_video"},
        ))
        
        self.register(Capability(
            name="play_youtube",
            description="Play a specific YouTube video or search result",
            category="media",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="url_match",
            recovery_hints=["try_search_then_play", "open_youtube_manually"],
            action_mapping={"default": "youtube_video"},
        ))
        
        # ── Text Input ──
        self.register(Capability(
            name="type_text",
            description="Type text into the currently focused field",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_diff",
            recovery_hints=["try_at_spi", "try_primary_paste", "try_clipboard"],
            action_mapping={"default": "computer_control"},
        ))
        
        # ── Mouse / Click ──
        self.register(Capability(
            name="click_element",
            description="Click a UI element by description or coordinates",
            category="ui",
            availability=lambda env: env.screen_available,
            prerequisites=[],
            cost="low",
            verification="screen_diff",
            recovery_hints=["re_identify_target", "try_screen_find", "try_at_spi"],
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="right_click",
            description="Right-click a UI element",
            category="ui",
            availability=lambda env: env.screen_available,
            prerequisites=[],
            cost="low",
            verification="screen_diff",
            recovery_hints=["re_identify_target"],
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="double_click",
            description="Double-click a UI element",
            category="ui",
            availability=lambda env: env.screen_available,
            prerequisites=[],
            cost="low",
            verification="screen_diff",
            recovery_hints=["re_identify_target"],
            action_mapping={"default": "computer_control"},
        ))
        
        # ── Window Management ──
        self.register(Capability(
            name="move_window",
            description="Move a window by dragging its titlebar",
            category="ui",
            availability=lambda env: env.os == "Linux" and env.desktop in ("Cinnamon", "GNOME", "KDE", "XFCE"),
            prerequisites=["wmctrl"],
            cost="low",
            verification="geometry_diff",
            recovery_hints=["unmaximize_first", "try_alt_drag"],
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="focus_window",
            description="Bring a window to foreground by title",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="window_list_check",
            recovery_hints=["try_partial_title", "list_windows"],
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="switch_window",
            description="Switch to a specific window by title",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="window_list_check",
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="maximize_window",
            description="Maximize the current or specified window",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="window_state_check",
            action_mapping={"default": "computer_settings"},
        ))
        
        self.register(Capability(
            name="minimize_window",
            description="Minimize the current or specified window",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="window_state_check",
            action_mapping={"default": "computer_settings"},
        ))
        
        self.register(Capability(
            name="close_window",
            description="Close the current or specified window",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="window_gone_check",
            action_mapping={"default": "computer_control"},
        ))
        
        # ── Keyboard / Hotkeys ──
        self.register(Capability(
            name="press_key",
            description="Press a single key or key combination",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="screen_diff",
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="hotkey",
            description="Execute a key combination (hotkey)",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="screen_diff",
            action_mapping={"default": "computer_control"},
        ))
        
        # ── Scrolling ──
        self.register(Capability(
            name="scroll",
            description="Scroll the current window",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="screen_diff",
            action_mapping={"default": "computer_control"},
        ))
        
        # ── Text Selection / Clipboard ──
        self.register(Capability(
            name="select_all",
            description="Select all content in focused field",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="clipboard_check",
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="copy",
            description="Copy selected content to clipboard",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="clipboard_check",
            action_mapping={"default": "computer_control"},
        ))
        
        self.register(Capability(
            name="paste",
            description="Paste clipboard content at cursor",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_diff",
            action_mapping={"default": "computer_control"},
        ))
        
        # ── Screenshot ──
        self.register(Capability(
            name="take_screenshot",
            description="Capture screenshot of screen or window",
            category="ui",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="file_exists",
            action_mapping={"default": "computer_control"},
        ))
        
        # ── File Operations ──
        self.register(Capability(
            name="find_file",
            description="Find files by name or pattern",
            category="files",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="path_exists",
            recovery_hints=["broaden_search", "check_trash"],
            action_mapping={"default": "file_controller"},
        ))
        
        self.register(Capability(
            name="read_file",
            description="Read contents of a file",
            category="files",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_check",
            action_mapping={"default": "file_processor"},
        ))
        
        self.register(Capability(
            name="write_file",
            description="Create or overwrite a file with content",
            category="files",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_check",
            recovery_hints=["check_permissions", "check_disk_space"],
            action_mapping={"default": "file_controller"},
        ))
        
        # ── System Control ──
        self.register(Capability(
            name="system_control",
            description="Adjust system settings: volume, brightness, WiFi, power",
            category="system",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="setting_check",
            requires_confirmation=True,
            recovery_hints=["check_permissions", "check_dbus"],
            action_mapping={"default": "computer_settings"},
        ))
        
        # ── Screenshot / Vision ──
        self.register(Capability(
            name="screen_understand",
            description="Analyze current screen content via vision",
            category="vision",
            availability=lambda env: True,
            prerequisites=[],
            cost="high",
            verification="content_check",
            action_mapping={"default": "screen_process"},
        ))
        
        # ── Web Search ──
        self.register(Capability(
            name="search_web",
            description="Search the web for current information",
            category="web",
            availability=lambda env: True,
            prerequisites=[],
            cost="low",
            verification="content_check",
            recovery_hints=["try_alternative_query", "try_different_engine"],
            action_mapping={"default": "web_search"},
        ))
    
    def register(self, capability: Capability) -> None:
        """Register a capability."""
        self._capabilities[capability.name] = capability
    
    def get(self, name: str) -> Optional[Capability]:
        return self._capabilities.get(name)
    
    def all(self) -> List[Capability]:
        return list(self._capabilities.values())
    
    def get_available(self, env: EnvironmentContext) -> List[Capability]:
        """Return capabilities available in the given environment."""
        return [cap for cap in self._capabilities.values() if cap.availability(env)]
    
    def get_for_intent(self, intent: str, entities: Dict[str, str], env: EnvironmentContext) -> List[Capability]:
        """
        Find capabilities matching the given intent and entities.
        
        This is a simple keyword-based matcher; the planner can override.
        """
        intent_lower = intent.lower()
        results = []
        
        # Intent-to-capability mapping
        intent_map = {
            "launch_application": ["launch_application"],
            "open_application": ["launch_application"],
            "open_app": ["launch_application"],
            "launch": ["launch_application"],
            "start": ["launch_application"],
            "open": ["launch_application", "browse_website"],
            "browse": ["browse_website"],
            "navigate": ["browse_website"],
            "go_to": ["browse_website"],
            "visit": ["browse_website"],
            "search": ["search_web", "search_youtube"],
            "search_web": ["search_web"],
            "search_youtube": ["search_youtube", "play_youtube"],
            "youtube": ["search_youtube", "play_youtube"],
            "play": ["play_youtube", "launch_application"],
            "watch": ["play_youtube"],
            "type": ["type_text"],
            "type_text": ["type_text"],
            "write": ["type_text", "write_file"],
            "click": ["click_element"],
            "double_click": ["double_click"],
            "right_click": ["right_click"],
            "drag": ["drag_element"],
            "scroll": ["scroll"],
            "type_text": ["type_text"],
            "paste": ["paste"],
            "copy": ["copy"],
            "select_all": ["select_all"],
            "focus_window": ["focus_window", "switch_window"],
            "switch_window": ["switch_window", "focus_window"],
            "move_window": ["move_window"],
            "close_window": ["close_window"],
            "minimize_window": ["minimize_window"],
            "maximize_window": ["maximize_window"],
            "close_window": ["close_window"],
            "scroll": ["scroll"],
            "screenshot": ["take_screenshot"],
            "screen_understand": ["screen_understand"],
            "search_web": ["search_web"],
            "web_search": ["search_web"],
            "search_youtube": ["search_youtube"],
            "play_youtube": ["play_youtube"],
            "youtube": ["search_youtube", "play_youtube"],
            "system_control": ["system_control"],
            "volume": ["system_control"],
            "brightness": ["system_control"],
            "wifi": ["system_control"],
            "screenshot": ["take_screenshot"],
            "screen_understand": ["screen_understand"],
            "find_file": ["find_file"],
            "read_file": ["read_file"],
            "write_file": ["write_file"],
        }
        
        # Match intent
        for key, caps in intent_map.items():
            if key in intent_lower:
                for cap_name in caps:
                    cap = self.get(cap_name)
                    if cap and cap.availability(env):
                        results.append(cap)
        
        # Also match entities for specific apps
        for entity_type, entity_value in entities.items():
            entity_lower = entity_value.lower()
            if entity_type in ("app", "application", "program"):
                cap = self.get("launch_application")
                if cap and cap.availability(env):
                    results.append(cap)
        
        # Deduplicate
        seen = set()
        unique = []
        for cap in results:
            if cap.name not in seen:
                seen.add(cap.name)
                unique.append(cap)
        
        return unique


# Global registry instance
_capability_registry: Optional[CapabilityRegistry] = None


def get_capability_registry() -> CapabilityRegistry:
    global _capability_registry
    if _capability_registry is None:
        _capability_registry = CapabilityRegistry()
    return _capability_registry