# Agent and intelligence map

- `main.py` - live conversation and existing tool/action execution path; smallest safe integration surface.
- `core/task_state.py` - bounded task tracking, continuation, and step records.
- `core/agent_state.py` - operational runtime state structures.
- `core/environment.py` - environment snapshot and host facts.
- `core/app_discovery.py` - installed-application discovery.
- `core/capability.py` - semantic capability definitions.
- `core/capability_router.py` - semantic-to-concrete capability routing.
- `core/model_provider.py` - model-provider abstraction; keep injection-ready.
- `core/model_router.py` - logical model roles and bounded provider fallback.
- `core/external_router.py` - opt-in specialist-provider eligibility; Gemini Live remains primary.
- `core/vision_state.py` - screen/camera availability, activity, and freshness.
- `memory/chat_store.py` - local multi-chat history with bounded lexical context retrieval.
- `memory/config_manager.py` - bounded response, research, vision, and optional-provider preferences.
- `core/live_speaker_diagnostics.py` - opt-in metadata-only probe for Live transcript speaker-label availability; no audio or transcript content is logged.
- `core/action_loader.py` and `core/plugin_loader.py` - existing action/plugin registries.
- `actions/` - concrete capabilities; reuse rather than duplicate.
- `diagnostics/` - lightweight manual diagnostics.
- `core/cloud_bridge.py` - opt-in local Cloud Core presence/task bridge; it never handles audio or bypasses existing action gates.
