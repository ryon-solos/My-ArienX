# ArienX project guidance

- Preserve protected systems named in the phase brief: audio and interruption (see `VOICE.md`), keyboard, browser lifecycle, YouTube, personality, memory, confirmation, undo, avatar, and UI layout.
- Prefer existing registries, routers, trackers, actions, and verification hooks over parallel implementations.
- Do not add providers or dependencies without a direct requirement.

## Project Map

- `AI.md` - agent loop, runtime state, environment, capabilities, and model integration.
- `CLOUD.md` - Netlify Cloud Core, bridge protocol, cloud-safe memory, and deployment boundaries.
- `TESTING.md` - focused checks and acceptance-test entry points.
- `VOICE.md` - protected interruption boundaries, source baseline, rollback scope and physical acceptance.

## Protected voice system

- Read `VOICE.md` before any change that touches voice capture, interruption, playback or Live activity signaling.
- For unrelated updates, run `python3 -m diagnostics.interruption_boundary_diag`; preserve the protected audio source.
- A changed fingerprint requires an explicit voice change and reviewed regression results. Never silently regenerate the baseline to make the check pass.
- Keep sidebar/to-do/activity changes separate from voice rollback. Synthetic audio passes are not physical microphone acceptance.
