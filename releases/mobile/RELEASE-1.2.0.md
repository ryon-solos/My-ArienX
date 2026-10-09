# ArienX Mobile 1.2.0 (build 3)

APK: `<ArienX checkout>/releases/mobile/ArienX-Mobile-1.2.0.apk`
Public download: https://myarienx.netlify.app/download/ArienX-Mobile-1.2.0.apk

## Live Talk repair

- Dedicated animated call interface, with mute, hang up and speaker controls; no keyboard or Send button.
- Native 16 kHz microphone audio streams to Gemini Live; automatic speech detection produces native 24 kHz spoken replies and supports interruptions.
- Charon voice and gemini-3.1-flash-live-preview match the currently configured Desktop voice/model. Chat read-aloud uses the same voice rather than Android TTS.
- Backend `/api/mobile/live` requires the existing ArienX account session and an owned conversation. One-use, setup-constrained tokens keep provider keys off the phone. Existing browser Origin protection remains. Default voice can be configured using ARIENX_LIVE_VOICE on Cloud Core.
- Shared cloud-safe memory and recent conversation provide context. Transcripts append through existing revision-protected conversations. Unsynced call transcripts remain encrypted locally; conflicts require review and can be saved into a separate conversation without overwriting another device's history.
- Existing Device Bridge commands require explicit phone confirmation and use the existing offline queue. No direct unconfirmed Desktop execution was added.
- Calls stop on hang up, navigation away, account change and app backgrounding. Mute remains physical during reconnection. No wake word or background microphone.
- Existing pairing, account system, memory sync, normal cloud chat, update checks and other features remain.

## Validation and limits

24 mobile tests, 13 Cloud Core tests and Dart analysis pass. Native Kotlin code compiles in a signed release APK. The exact constrained setup was accepted by the real Gemini provider; streamed PCM speech generated automatic spoken responses without Send. These checks do not prove phone microphone acoustics, echo cancellation, headset routing or actual signed-in physical-device behavior. A physical Android device was unavailable.

## Verify on your phone

1. Install this APK over the existing app, or use Settings -> Check updates. Do not uninstall; confirm the existing account, pairing and memory remain.
2. Open Live Talk and allow microphone access. Speak normally, pause, and wait for the reply without pressing Send. Continue for a second turn. Verify the animated call screen has no composer and the voice matches Desktop Charon.
3. Interrupt a spoken reply; verify playback stops and ArienX listens. Test mute/unmute, speaker toggle, hang up and backgrounding; Android's microphone indicator must disappear after hang up/backgrounding.
4. Keep Desktop offline. Ask a question using a harmless synced memory fact. Verify a cloud reply, then open the conversation in chat and confirm completed call transcripts are saved. Refresh Desktop when it reconnects and check the conversation there.
5. Ask to open Chrome on Desktop, confirm the phone dialog, and verify the queued activity executes once after Desktop reconnects.
6. If a transcript save fails, reopen the call to retry the revision-matching save. If another device changed the conversation, open Chat -> Refresh -> Review saved call and save a separate conversation. Never overwrite the newer shared history.

Keep the existing signing key; never overwrite older versioned APK files. SHA256SUMS and latest.json contain the exact release checksum.
