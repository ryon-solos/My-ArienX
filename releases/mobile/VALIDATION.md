# Release validation — 2026-10-02

- Universal release APK built successfully with Flutter 3.47.5 / Dart 3.13.4.
- Package: `app.arienx.arienx_mobile`; version: `1.0.0`; version code: `1`.
- Minimum Android API: 24. Target/compile API: 36.
- Architectures: armeabi-v7a, arm64-v8a, x86_64.
- APK size: 73,352,243 bytes.
- Android apksigner verification passed using APK Signature Scheme v2, RSA 3072.
- Public signing certificate SHA-256: `746494fecfda00e10e5f8be06ad21370ae2f433318d4e2c58b1365751df00d02`.
- ZIP alignment checked with `zipalign -c -P 16 4`: passed.
- All 64-bit native ELF load segments checked for 16 KB alignment: passed (18 total native libraries inspected).
- Signed release Android App Bundle built and JAR signature verified. Its ZIP entries were reordered to place the manifest/signature files first, resolving the stream-reader warning without changing signed file contents. Self-signed certificate and absent timestamp warnings are expected for this local Android signing key; store acceptance has not been tested.
- Flutter analysis: no issues. Flutter tests: 9 passed after final dependency correction.
- Cloud tests: 10 passed. TypeScript compilation passed.
- Mobile desktop diagnostics: 6 passed. Existing bridge diagnostics and Python syntax compilation passed.
- SHA256SUMS covers the distributed APK and AAB. The public download folder contains only the APK and its checksum, not signing secrets.
- Cloud Core 1.0.0 was deployed to `https://myarienx.netlify.app` as Netlify deploy `6abfc3c66be717278d5c3f45`. Production checks confirmed the APK download returns HTTP 200 (73,352,243 bytes) and protected pairing, memory, and chat endpoints return JSON 401 responses rather than Netlify SSO HTML.

The first release attempt exposed a generated registration for an unused integration-test plugin. Removing that unused dev dependency resolved compilation; release packaging and the final Flutter checks then passed.

No physical Android device was connected and no signed-in production pairing session was completed. These are build/source validation results, not a guarantee of store acceptance or completion of all requested ecosystem features. See README.md and ../../docs/MOBILE.md for remaining work.
