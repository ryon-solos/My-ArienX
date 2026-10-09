# Voice and interruption system

This is the dedicated change record and boundary guide requested by Ryon. Source
checks help detect accidental edits; they cannot guarantee that microphone,
speaker, room, device latency or model behavior will remain constant.

## Ownership

- `core/interruption.py`: the code-only playback-reference filter and local speech controller.
- `main.py`, class `JarvisLive`: `interrupt`, `_send_realtime`, `_enqueue_speech_events`,
  `_listen_audio`, `_play_audio`, `_receive_audio`, `_watch_input_stall`, and `_relay_phone_audio`.
- Live setup always disables server automatic voice detection. Confirmed local
  speech sends one `ActivityStart`, buffered speech, and an `ActivityEnd` after silence.
- Playback must feed its reference before the speaker write. Preserve the reference
  through speech endings and gaps. The output stream must await an in-flight native
  write before closing. A normal interruption must not reconnect Live or Cloud Core.
- `core/activity.py`, `core/todo_panel.py`, `memory/todo_store.py`, and the sidebar
  additions in `ui.py` are separate UI/data features. Do not revert them to change voice behavior.

## Rules for future changes

1. Unrelated UI, to-do, branding, activity-log or cloud changes must preserve the
   controller and executable Live audio wiring. Run the boundary check below.
2. If voice behavior is explicitly being changed, inspect its complete capture,
   classification, queue, server acknowledgement and playback path. Back up the
   exact affected files first; restore only voice code, never a whole old checkout.
3. Run the offline regressions below. Do not silently rewrite the boundary baseline
   to make a failing check pass. Update it only for an intentional, reviewed voice change,
   and record that change here.
4. Verify with the user's built-in speakers and microphone: playback alone must
   not trigger interruption, and speech overlapping playback must stop the reply
   and preserve the user's first words. Synthetic checks are insufficient acceptance.
5. Do not install audio packages, change OS audio processing, add a second detector,
   or reintroduce a native fallback as part of unrelated work.

## Required checks

```
PYTHONDONTWRITEBYTECODE=1 python3 -m diagnostics.interruption_boundary_diag
OPENBLAS_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1 python3 -m diagnostics.interruption_diag
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m diagnostics.smooth_voice_cloud_diag
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 python3 -m diagnostics.todo_activity_diag
PYTHONDONTWRITEBYTECODE=1 python3 -m diagnostics.dashboard_recovery_diag
```

The boundary check uses executable AST fingerprints for the protected methods,
so unrelated formatting, line numbers, sidebar changes and cloud startup repairs
can proceed without changing the reviewed audio code.

## Change record — 8 October 2026

- Native SpeexDSP/EchoGuard/VoiceGate integration was replaced by a single code-only
  controller. The installed system SpeexDSP library was not installed or removed by this work.
- Controller SHA-256: `2d97ae1d545f9f2e87be655b716a03b03b9bec72fa391c736b57a47e01f3f23b`.
- The activity-log/to-do update did not change this controller or `main.py`.
  The saved pre-update `main.py` and installed copy both hash to
  `d922291c4c2e21f3d6066d90d1395bbe60f433eedf5e0b6c81f0bec737c9794d`.
- Offline checks pass, but Ryon reported renewed self-interruption. Runtime events
  around 17:21 confirm local voice interruptions; those metrics alone cannot label
  each event as user speech or playback echo. Physical acceptance remains unresolved.
- An older initial code-only backup exists with controller hash
  `104f0faff5028b59059a9abd2422c2edf295cc324be548c4182d1668c90c875c`.
  It predates the silent-reference numerical correction and richer speech tests;
  it is not a verified working rollback target.

## Intentional repair — 8 October 2026

Ryon authorized repair of the remaining self-interruption rather than an arbitrary
rollback. No changes were made to the to-do, activity-log, chat UI, or Live wiring.

- Reproduced an echo-only false interruption after speaker equalization,
  fractional delay, reflections and noise were applied to generated speech.
- Replaced scalar-only echo subtraction with a seven-tap, regularized local fit
  of recent playback. It handles frequency coloration and fractional sample delay;
  matching still uses a bounded history and requires sustained speech evidence.
- Recognize dominant fitted echo by explained microphone energy, not solely the
  original scalar correlation. Residual energy may never exceed raw microphone energy.
- Added paired echo-only/user-overlap checks for five deterministic room filters.
  These checks and the previous voice regressions pass. No new dependency, native
  audio module, OS audio changes, recordings or account traffic were introduced.
- Updated the protected baseline intentionally after reviewing the regression
  results. It now also guards speaker-state methods, sample constants and the
  unconditional disabled-server-VAD configuration.
- Active controller SHA-256: `28abf847eebacfb50eda7f6cb9eba02d7397d2a7565e036eb50178b8854f8568`.
- The sandbox initially reported no audio devices; a host-level check exposed
  the normal input/output devices. An 8-second local speaker/microphone check
  played generated speech-like audio and processed microphone samples only in RAM.
  The prior filter produced one interruption event; the repaired filter produced
  zero. Neither callback reported an error. No audio was saved or sent anywhere.
- Paired synthetic near-speech tests pass, but a live user speaking over an actual
  ArienX reply is still needed for full physical acceptance. The local echo check
  does not prove reliability at every volume, microphone position or room state.
