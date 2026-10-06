# Testing map

- `diagnostics/` - manual environment and computer-control diagnostics.
- `python3 -m compileall -q main.py ui.py core memory actions diagnostics` - import/syntax smoke check.
- Agent behavior checks should use fake actions/environment where possible and must not touch protected audio or UI systems.
- Chat storage should be tested with a temporary chat: append, context retrieval, rename, and delete. Do not retain test chats.
- Optional provider tests must use disabled configuration; never send API keys or live external requests in diagnostics.
- `python3 -m diagnostics.live_speaker_metadata_diag` - offline safety check for the opt-in Live speaker-metadata probe; it must not log transcript content or contact Gemini.
- `python3 -m diagnostics.cloud_bridge_diag` - offline authentication, pairing, heartbeat, chat, cloud-safe memory, task delegation, disconnect/reconnect, and signing checks; it must not contact Netlify.

- `python3 -m diagnostics.updater_diag` - offline Git source update, local edit protection, data preservation and dependency-change checks.
- `docs/SOURCE_UPDATES.md` - GitHub source update workflow and launcher requirements.
