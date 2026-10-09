# ArienX Mobile

Current Android release: **1.2.1 (build 4)**. See [release notes and phone verification](../releases/mobile/RELEASE-1.2.1.md). Download at https://myarienx.netlify.app/download/.

Flutter companion for the existing ArienX Netlify Cloud Core and desktop Device Bridge. Android 7.0 (API 24) or newer.

## Install

Use a signed APK from `releases/mobile/`. Allow installation from the app used to open the APK, install ArienX, and choose **Scan desktop QR**.

Desktop: **Settings → Cloud Core → Connect phone / Mobile APK → Generate pairing QR**. The desktop must already be paired with Cloud Core. Codes expire in two minutes and can be redeemed once. Each phone has its own revocable 30-day credential; no desktop private key is transferred.

Cloud Core 1.0.0 is deployed at `https://myarienx.netlify.app`. Native calls now reach the APIs directly; account authentication remains enforced inside every private API.

## Build and test

Install Flutter 3.47.5, Java 21, and the Android SDK. Run:

```sh
flutter pub get
flutter analyze
flutter test
flutter build apk --release
```

Release signing reads ignored `android/key.properties` (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`). The generated local key is in `../config/mobile-release.jks`; preserve it securely for future compatible updates. Never upload the key or password. Debug builds do not require release signing material.

See `../docs/MOBILE.md` for architecture, endpoints, privacy boundaries, tested behavior, and release limitations.

For the official updater, publish the universal APK. Increase both pubspec versionCode and core/updates.dart mobileBuild; keep the original signing key and publish matching latest.json metadata.
