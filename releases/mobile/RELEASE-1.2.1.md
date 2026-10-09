# ArienX Mobile 1.2.1 (build 4)

This release upgrades the existing auto-check feature into automatic update download and Android self-installation. The native Live Talk improvements from 1.2.0 remain unchanged.

## Get this updater upgrade without a website

On an installed 1.1.0 or 1.2.0 app, use Settings -> Check updates, or accept its existing startup update offer. The older updater downloads this release itself and opens Android installation. If Android asks to allow installation from ArienX, allow it once, return to ArienX and tap Check updates again. This one-time bootstrap is necessary because an already-installed older binary cannot gain new updater code without being updated.

First-time installations only: https://myarienx.netlify.app/download/ArienX-Mobile-1.2.1.apk
Local APK: `<ArienX checkout>/releases/mobile/ArienX-Mobile-1.2.1.apk`

## Subsequent updates

- Automatic app updates are enabled by default and can be disabled in Settings. Existing disabled preferences are preserved.
- The app checks the official public release feed when opening and schedules Android JobScheduler checks approximately every 12 hours, subject to Android scheduling, Wi-Fi and idle constraints. Jobs persist across reboot. No login is required for the public update feed.
- APK updates download internally on unmetered Wi-Fi. Updates are checked for SHA-256, exact package, newer version and the existing signing certificate. Downloads are reused after verification; no website or repeated manual APK management.
- Installation uses PackageInstaller sessions. Android 12+ is asked to install without user action using its supported self-update API and the UPDATE_PACKAGES_WITHOUT_USER_ACTION permission. Silent installation depends on Android's eligibility/security checks, not an ArienX guarantee.
- Automated installation is deferred while ArienX is foregrounded, to avoid interrupting chat or Live Talk. Installation is handed to Android when idle. Manual Check updates can install immediately because it is an explicit user action.
- When Android requires approval, the verified update produces a notification and Settings -> Finish update action. The OS confirmation cannot be bypassed. Notification permission is needed for the notification; the Settings action remains available without it.
- Android 7-11 generally requires installation approval. Battery restrictions, no Wi-Fi, and force-stop can defer background jobs. A force-stopped app must be reopened to resume its jobs. This is not Google Play distribution or a privileged system installer.
- A one-time Allow updates button opens Android's install-from-ArienX permission where needed. Enabling automatic updates does not weaken account auth or grant permission to install other apps.
- The existing account, encrypted local data, memory sync, Device Bridge, pairing, queued commands and Live Talk are preserved. No backend authentication changes.

## Validation

28 mobile tests pass; Dart analysis is clean; signed native APK compiles. The APK uses the existing release certificate. Tests cover automatic update enable/disable delegation, newer-version/official-origin rules, one-time permission UI and Android-required approval UI, plus mobile regressions. No physical Android device was connected, so actual idle background installation and OS-specific silent-install eligibility are not yet physically verified.

Phone verification: install this release through the existing in-app updater; open Settings and confirm Automatic app updates is enabled and Allow updates is granted. A later higher signed version is needed to test automatic idle installation on the phone. Keep notifications enabled if Android requires approval. Do not uninstall the app to update it.

Platform contract: https://developer.android.com/reference/android/content/pm/PackageInstaller.SessionParams#setRequireUserAction(int)
