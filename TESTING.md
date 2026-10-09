# Testing map

- `diagnostics/` - manual environment and computer-control diagnostics.
- `python3 -m compileall -q main.py ui.py core memory actions diagnostics` - import/syntax smoke check.
- Agent behavior checks should use fake actions/environment where possible and must not touch protected audio or UI systems.
- Chat storage should be tested with a temporary chat: append, context retrieval, rename, and delete. Do not retain test chats.
- Optional provider tests must use disabled configuration; never send API keys or live external requests in diagnostics.
- `python3 -m diagnostics.live_speaker_metadata_diag` - offline safety check for the opt-in Live speaker-metadata probe; it must not log transcript content or contact Gemini.
- `python3 -m diagnostics.cloud_bridge_diag` - offline authentication, pairing, heartbeat, chat, cloud-safe memory, task delegation, disconnect/reconnect, and signing checks; it must not contact Netlify.
- `python3 -m diagnostics.multi_agent_diag` - offline orchestration, parallelism, timeout, cancellation, and retry checks; it must not contact Gemini or OpenRouter.
- `python3 -m diagnostics.extension_runtime_diag` - offline extension validation, lifecycle, storage-preserving update, rollback, disable, and uninstall checks.

- `cd cloud && npm test` - isolated mobile API security, concurrency, streaming and transfer checks.
- `cd mobile && flutter analyze && flutter test` - mobile analyzer, unit and widget checks.
- `python3 -m diagnostics.mobile_ecosystem_diag` - offline memory merge, pairing validation and telemetry privacy checks.

- `python3 -m diagnostics.updater_diag` - offline Git source update, local edit protection, data preservation and dependency-change checks.
- `docs/SOURCE_UPDATES.md` - GitHub source update workflow and launcher requirements.

- `VOICE.md` - interruption change record and acceptance boundaries.
- `python3 -m diagnostics.interruption_boundary_diag` - detect accidental changes to the documented voice source during unrelated updates.
- `python3 -m diagnostics.interruption_diag` - synthetic playback echo, overlapping speech, activity protocol and phone relay regressions.
