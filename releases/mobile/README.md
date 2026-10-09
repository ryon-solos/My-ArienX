# ArienX Mobile 1.0.0 — initial Android release

## Install on your phone

Copy `ArienX-Mobile-1.0.0.apk` to your Android phone and open it. Allow installation from the app opening the APK when Android asks. Requires Android 7.0 (API 24) or later. The universal APK supports ARM32, ARM64, and x86_64.

Desktop pairing: Settings → Cloud Core → Connect phone / Mobile APK → Generate pairing QR. In the phone app, choose Scan desktop QR. The desktop must first be connected to the same configured Cloud Core account. Pairing codes expire after two minutes.

## Live Cloud Core

Cloud Core 1.0.0 is live at `https://myarienx.netlify.app`. The APK download works over HTTPS and native requests reach the protected APIs directly. Pairing, synchronization, and chat continue to require the ArienX account session; unauthenticated calls correctly receive JSON `401` responses.

Cloud chat can run with the desktop powered off once Cloud Core is deployed and its provider credentials are configured. Desktop commands queue until the desktop returns; a powered-off computer cannot execute them immediately.

## Distribution

The APK is signed using the local release key at `../../config/mobile-release.jks`. Preserve that private key and its password securely for future updates. Do not distribute them. Only the APK, checksums, and public release notes belong in a download.

The prepared download page is `../../cloud/public/download/index.html`. Its APK link works after deploying that folder with the Cloud Core site. An Android App Bundle, if included, is intended for store upload and cannot be installed directly like an APK. Store publication still requires your publisher account, listing, privacy disclosures, and store review.

## Validation and remaining work

25 new automated tests passed: 10 cloud tests, 9 Flutter tests, and 6 desktop diagnostics. Flutter analysis, TypeScript compilation, Python syntax checks, and the existing bridge diagnostic also passed. These checks do not replace physical-phone or deployed end-to-end testing. No Android device was connected during the build.

This is an initial companion release, not completion of every item in the original ecosystem specification. Foreground notifications and speech recognition/TTS are implemented; background push, wake word, native real-time voice, and other outstanding surfaces are listed in `../../docs/MOBILE.md`. Follow that document's physical-device acceptance checklist before publishing broadly.
