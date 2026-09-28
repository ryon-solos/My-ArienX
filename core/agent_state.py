"""
core/agent_state.py — Agent state management.

Lightweight structured state for the agent core.
Thread-safe with a single Lock like TaskTracker.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any


@dataclass
class StepResult:
    """Result of a single plan step execution."""
    tool: str
    args: Dict[str, Any]
    result: str
    verified: bool
    hint: str
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class PlanStep:
    """A single step in an execution plan."""
    capability: str                    # Logical capability name
    action: str                        # Concrete action/tool name
    args: Dict[str, Any]               # Arguments for the action
    verification: str                  # Verification strategy name
    recovery_hints: List[str] = field(default_factory=list)


class AgentState:
    """
    Lightweight agent state tracker.
    
    Thread-safe via single Lock. All state transitions are atomic.
    Does NOT store unlimited history - bounded collections only.
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        
        # Request context
        self.original_request: str = ""
        self.intent: str = ""
        self.entities: Dict[str, str] = {}
        
        # Execution state
        self.status: str = "idle"                    # idle | planning | executing | verifying | recovering | cancelled | done
        self.plan: List[PlanStep] = []
        self.current_step: int = 0
        self.current_action: str = ""
        self.current_args: Dict[str, Any] = {}
        
        # Context
        self.environment: Optional["EnvironmentContext"] = None
        self.site_context: str = ""
        self.current_focus: str = ""
        self.active_personality: str = ""
        
        # History / verification
        self.step_history: List[StepResult] = []
        self.consecutive_failures: int = 0
        self.last_verification: str = ""          # "verified" | "unverified" | "failed"
        self.recovery_attempts: int = 0
        
        # Control
        self.cancellation_requested: bool = False
        self.awaiting_confirmation: bool = False
        self.confirmation_promise: Optional[Callable] = None
        
        # Meta
        self.created_at: float = 0.0
        self.updated_at: float = 0.0
    
    # ── Public API ────────────────────────────────────────────────────
    
    def reset_for_new_request(self, request: str, intent: str, entities: Dict[str, str]) -> None:
        """Start a fresh task chain."""
        with self._lock:
            self.original_request = request
            self.intent = intent
            self.entities = entities or {}
            self.status = "planning"
            self.plan = []
            self.current_step = 0
            self.current_action = ""
            self.current_args = {}
            self.site_context = ""
            self.current_focus = ""
            self.step_history = []
            self.consecutive_failures = 0
            self.last_verification = ""
            self.recovery_attempts = 0
            self.cancellation_requested = False
            self.awaiting_confirmation = False
            self.confirmation_promise = None
            self.created_at = time.monotonic()
            self.updated_at = time.monotonic()
    
    def set_plan(self, plan: List[PlanStep]) -> None:
        """Set the execution plan."""
        with self._lock:
            self.plan = plan
            self.current_step = 0
            self.status = "executing"
            self.updated_at = time.monotonic()
    
    def get_current_step(self) -> Optional[PlanStep]:
        """Get the current step to execute."""
        with self._lock:
            if 0 <= self.current_step < len(self.plan):
                return self.plan[self.current_step]
            return None
    
    def advance_step(self) -> bool:
        """Advance to next step. Returns True if more steps remain."""
        with self._lock:
            self.current_step += 1
            self.updated_at = time.monotonic()
            return self.current_step < len(self.plan)
    
    def record_step_result(self, tool: str, args: Dict, result: str, verified: bool, hint: str) -> None:
        """Record the result of a step execution."""
        with self._lock:
            self.step_history.append(StepResult(
                tool=tool,
                args=args or {},
                result=result,
                verified=verified,
                hint=hint
            ))
            self.last_verification = "verified" if verified else ("failed" if not verified and "failed" in result.lower() else "unverified")
            if verified:
                self.consecutive_failures = 0
            else:
                self.consecutive_failures += 1
            self.updated_at = time.monotonic()
    
    def mark_recovering(self) -> None:
        """Enter recovery mode."""
        with self._lock:
            self.status = "recovering"
            self.recovery_attempts += 1
            self.updated_at = time.monotonic()
    
    def mark_done(self, summary: str = "") -> None:
        """Mark task as complete."""
        with self._lock:
            self.status = "done"
            if summary:
                self.step_history.append(StepResult(
                    tool="(summary)",
                    args={},
                    result=summary,
                    verified=True,
                    hint=""
                ))
            self.updated_at = time.monotonic()
    
    def mark_failed(self, reason: str) -> None:
        """Mark task as failed."""
        with self._lock:
            self.status = "failed"
            self.updated_at = time.monotonic()
    
    def request_cancel(self) -> None:
        """Request cancellation of current task."""
        with self._lock:
            self.cancellation_requested = True
            if self.status in ("executing", "verifying", "recovering"):
                self.status = "cancelled"
            self.updated_at = time.monotonic()
    
    def set_awaiting_confirmation(self, promise: Callable) -> None:
        """Park state awaiting user confirmation."""
        with self._lock:
            self.awaiting_confirmation = True
            self.confirmation_promise = promise
            self.status = "awaiting_confirmation"
            self.updated_at = time.monotonic()
    
    def resolve_confirmation(self, accepted: bool) -> None:
        """Resolve a pending confirmation."""
        with self._lock:
            self.awaiting_confirmation = False
            self.confirmation_promise = None
            if accepted:
                self.status = "executing"
            else:
                self.status = "cancelled"
            self.updated_at = time.monotonic()
    
    def is_cancelled(self) -> bool:
        with self._lock:
            return self.status == "cancelled" or self.cancellation_requested
    
    def can_execute_next(self) -> bool:
        """Check if we can proceed to next action."""
        with self._lock:
            return (
                self.status == "executing" and
                not self.cancellation_requested and
                not self.awaiting_confirmation and
                self.current_step < len(self.plan)
            )
    
    def update_context(self, site: str = "", focus: str = "", personality: str = "") -> None:
        """Update contextual information."""
        with self._lock:
            if site:
                self.site_context = site
            if focus:
                self.current_focus = focus
            if personality:
                self.active_personality = personality
            self.updated_at = time.monotonic()
    
    def snapshot(self) -> Dict[str, Any]:
        """Thread-safe snapshot for debugging/logging."""
        with self._lock:
            return {
                "status": self.status,
                "original_request": self.original_request,
                "intent": self.intent,
                "entities": self.entities,
                "step": self.current_step,
                "plan_length": len(self.plan),
                "site": self.site_context,
                "focus": self.current_focus,
                "personality": self.active_personality,
                "consecutive_failures": self.consecutive_failures,
                "last_verification": self.last_verification,
                "recovery_attempts": self.recovery_attempts,
                "cancelled": self.cancellation_requested,
                "awaiting_confirmation": self.awaiting_confirmation,
            }


