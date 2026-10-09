# ArienX Mobile 1.1.0

APK: `<ArienX checkout>/releases/mobile/ArienX-Mobile-1.1.0.apk`
Download: https://myarienx.netlify.app/download/

## What changed

- Chat drawer and bottom-left menu: Chat, Profile, Explore, Settings, Clock.
- Home/Remote tabs removed; device status and activity remain in Profile.
- Live Talk alternates speech recognition, cloud chat and spoken replies. It stops on app backgrounding. It is not full-duplex Gemini Live audio.
- Desktop commands remain authenticated, confirmed and queued through the existing bridge. In chat say `open Chrome on my desktop`, `lock my desktop`, or `check desktop status`. The Desktop commands button also exposes existing controls.
- Automatic official update checks and manual Settings checks. APK origin, checksum, package, version and signing certificate are checked before Android asks to install. First install this release manually; subsequent releases can use this updater.
- Optional daily personal responses generate through existing cloud chat while the app is open, at most once per day after the chosen time. They persist as a shared ArienX check-ins conversation. No background AI push service was added.
- Optional scheduled memory reminders use explicit daily HH:mm facts and daily check-in time. Seven days are scheduled and renewed on subsequent sync. Force-stop and Android battery policies can delay/block them.
- Pomodoro (25/5/15 minutes) and next-occurrence alarm notifications. These are notification alerts, not looping full-screen ringing alarms. Exact delivery requires Android exact-alarm permission.
- Existing account authentication, safe memory projection, conversation storage, bridge, pairing and offline queue retained.

## Phone acceptance

1. Install 1.1.0 over the existing app without uninstalling. Confirm saved login, drafts and memory remain.
2. Open side panel and continue an existing chat. Use bottom-left menu; confirm Profile/Explore/Settings/Clock and no Home/Remote tabs.
3. In Profile verify the paired desktop and memory revision. Add a harmless memory fact in Explore, sync Desktop, then confirm it appears there.
4. Turn Desktop off. Send a cloud chat message and use Live Talk with microphone permission: speak, hear reply, speak again. Background the app and confirm recording stops.
5. With Desktop off say `open Chrome on my desktop`, confirm the command, then check Profile activity. Turn Desktop on and verify execution exactly once.
6. Settings: grant notifications, enable reminders, choose a check-in time a few minutes ahead, enable automatic personal responses. Leave app open past that time; verify one saved check-in and notification. Refresh repeatedly and verify no duplicate response that day. Disable this opt-in to stop generation.
7. Clock: set an alarm a few minutes ahead, allow exact alarms, close app normally and verify its notification. Start/cancel Pomodoro and confirm a cancelled timer does not notify.
8. Settings -> Check updates should say latest release. A future signed higher version is needed to physically test the in-app upgrade installer; automated tests validate version/origin rules. Android must confirm installation.

## Maintainer release process

Keep the existing release signing key. Increase pubspec versionCode and core/updates.dart mobileBuild together, build signed release, verify certificate, publish versioned APK and matching SHA256 in latest.json. Never overwrite an already-published versioned APK.

## Verified release evidence

- Flutter analysis: no issues. Mobile tests: 16 passed. Cloud Core tests: 12 passed.
- Signed universal APK: app.arienx.arienx_mobile, version 1.1.0, versionCode 2, minimum Android API 24.
- APK certificate matches 1.0.0: SHA256 746494fecfda00e10e5f8be06ad21370ae2f433318d4e2c58b1365751df00d02.
- APK file SHA256: de9180966cf36540b50333c0327522848c3d56ebf73791dede3464ce16306142.
- Production deploy: 6ac4d16042169f0c8dd6bd1a, existing myarienx.netlify.app.
- Anonymous production download page, latest.json and APK: HTTP 200. Full downloaded 75,466,799-byte APK matches the manifest checksum.
- Anonymous production memory, conversations and bridge status: HTTP 401.
- No connected Android device or signed-in production account was available. Microphone/TTS, notification timing, physical installation and real cross-device pairing/sync acceptance must be checked on the phone using the steps above. Future native upgrade installation has not been tested on hardware.

- Additional live anonymous checks: mobile chat, mobile pairing and desktop registration return HTTP 401. Task POST with no device returns HTTP 404 with `device not found`, matching its existing pre-authentication resource validation; no command is accepted.
