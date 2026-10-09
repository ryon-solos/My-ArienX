# ArienX desktop–mobile integration

Mobile 1.2.1 adds automatic Wi-Fi update downloads and idle self-installation through Android PackageInstaller, with mandatory-approval fallback. It also replaces dictation-based Live Talk with native streaming audio, automatic replies and Desktop-compatible Charon speech. Existing chat, safe memory, pairing and queues remain. Current behavior and exact phone acceptance steps: [1.2.1 release notes](../releases/mobile/RELEASE-1.2.1.md).

## Architecture

The Flutter app uses the existing Netlify Identity account, Cloud Core, cloud-safe memory, and signed Device Bridge. Riverpod injects one repository; go_router manages routes. Hive encrypts the local cache with an AES key protected by Android secure storage. Camera, microphone, notification, and file sharing permissions are requested only for the relevant feature.

Cloud chat runs when the desktop is off. Desktop commands queue for up to 24 hours and use the existing action dispatcher and its confirmation gates. Claimed commands are never automatically replayed after a disconnect: their outcome can be uncertain. Worker monitoring is read-only. Extensions are managed by the existing desktop sandbox runtime.

## Implemented surfaces

- QR pairing, account login, session restore, phone rename/revocation, secure logout.
- Dark cyan desktop-style UI, optional light theme, dashboard, device selection, manual refresh, cross-module search, and foreground reconnect with exponential backoff.
- Shared cloud conversations on both desktop and mobile; streaming mobile replies, Markdown, search, pins, encrypted offline drafts, and conflict protection.
- Push-to-talk, read-aloud, continuous turn-taking, and interruption of phone speech playback. Recognition availability depends on the phone's installed speech service.
- Three-way cloud-safe memory merge with revision checks, cloud-only reading when desktop is unavailable, and explicit conflict detection.
- Optional desktop CPU/RAM/battery, extension metadata, and redacted worker status. Sharing is **off by default** and requires enabling the named desktop checkbox.
- Existing bridge app launch, browser navigation, lock/restart/shutdown requests, queue cancellation and task timeline.
- Extension create/open/enable/disable/remove/rollback via existing runtime.
- Explicit account-scoped file transfers up to 3 MB, photo capture, Android share-menu receiving, download/share on phone, and desktop delivery into `Downloads/ArienX` without automatic execution.
- Foreground task notifications, settings, diagnostics and a home-screen launcher widget.

## API endpoints

Existing: `/api/bridge/register`, `/api/bridge/status`, `/api/bridge/devices`, `/api/bridge/heartbeat`, `/api/bridge/tasks`, `/api/memory`, `/api/chat`, `/api/research`.

Added: `/api/mobile/pair`, `/api/mobile/redeem`, `/api/mobile/sessions`, `/api/mobile/logout`, `/api/mobile/chat`, `/api/mobile/files`, `/api/conversations`, `/api/bridge/telemetry`.

Mobile headers carry `X-ArienX-Session`; only its SHA-256 digest is stored server-side. Account ownership is checked on private resources. Pair codes expire after two minutes and are consumed with conditional writes. Conversations, memory updates and task claims use ETag conditions to prevent concurrent lost updates. Device signatures require valid numeric timestamps and Ed25519 keys. Revoked desktop records cannot automatically reactivate.

## Privacy and continuity

Existing local desktop chat history and long-term memory are not bulk uploaded. Use Shared Cloud Conversations for explicit cross-device conversation continuity. Only entries in the existing `memory/cloud_safe.json` participate in memory synchronization. Cloud facts are included as saved user data in the desktop prompt on session construction. Conflicting facts pause synchronization; neither version is silently chosen. Worker prompts/results, API keys, OAuth credentials, browser cookies, private bridge keys, camera streams and screen streams are not included in telemetry.

Selected file/photo transfers are explicit uploads to the user's account and are separate from automatic memory synchronization. File contents may be sensitive; the upload dialog names the cloud destination before sending. File previews do not execute downloaded content.

## Verification commands

```sh
cd cloud && npm ci && npm test && npm run build
cd ../mobile && flutter analyze && flutter test
cd .. && python3 -m diagnostics.mobile_ecosystem_diag
python3 -m diagnostics.cloud_bridge_diag
python3 -m compileall -q main.py ui.py core memory actions diagnostics
```

Cloud tests mock storage, Identity and provider HTTP; they never touch a live account. Mobile tests cover pairing input, offline drafts, stream completion/interruption, revision conflicts, logout and read-only monitoring, plus phone-sized UI checks. Desktop diagnostics verify memory merge conflicts, secret rejection, pairing, telemetry consent and redaction.

## Release limitations

- Cloud Core 1.0.0 is deployed to `https://myarienx.netlify.app` (Netlify deploy `6abfc3c66be717278d5c3f45`). Native HTTPS checks confirmed the APK download returns `200`, and the protected pairing, memory, and mobile-chat APIs return JSON `401` responses rather than a Netlify SSO/login page. Account authentication remains enabled. A real signed-in account and paired desktop are still required to exercise successful pairing, sync, and chat in production.
- Remote phone hardware, camera, microphone, file sharing, actual QR scanning, desktop confirmation prompts, performance targets and an end-to-end signed-in production session require physical-device verification.
- Notifications currently run while the app is connected in the foreground. OS push delivery/background notifications require a configured push service and are not implemented as fake push.
- Live Talk uses native Android PCM capture/playback and a constrained, short-lived Gemini Live token issued only to authenticated accounts. Regular chat microphone input is explicitly dictation. Wake word is not implemented.
- GPU/model/token metrics are shown only where real data is available; no synthetic readings. File transfer limit is 3 MB. No automatic screenshot/camera-frame upload or arbitrary privileged Android automation.
- Full extension generation/update UI, cloud automation authoring, mobile image reasoning, automatic local-desktop-chat handoff, iOS/Wear OS/Android Auto builds, and conflict-resolution UI remain outside the implemented surfaces.
- The tests cannot establish that every possible bug is absent or certify the requested startup/memory/frame-rate targets.

## Physical-device acceptance

1. Install the signed APK; deny camera once to check the fallback, then grant it and redeem a desktop QR. Confirm expired/reused codes fail.
3. Send a cloud chat from the phone; open Shared Cloud Conversations on desktop and continue the same thread. Pin, search, restart, and confirm session restoration.
4. Turn the desktop off. Confirm cloud chat/memory still work. Queue an app launch and reconnect the desktop. Check that one command runs once.
5. Enable telemetry explicitly and check vitals, extensions and worker status. Disable it and confirm reporting stops.
6. Edit a safe memory fact on separate devices from stale revisions; confirm no silent overwrite. Test offline drafts and reconnect.
7. Share a small photo/document in both directions. Verify the desktop never automatically executes it.
8. Revoke the phone from desktop; confirm its next API request fails. Pair again explicitly. Test notifications, voice interruption and widget launch on the target phone.
