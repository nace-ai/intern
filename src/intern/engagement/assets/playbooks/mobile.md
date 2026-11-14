---
name: mobile
title: Mobile apps (Android / iOS) — static analysis and API extraction
triggers: Load when the operator provides an APK/AAB/IPA, or the target publishes mobile apps whose backend is in scope. Mobile apps are the best map of the real API and frequently ship secrets.
attack: reconnaissance, credential-access
owasp: A04, A07, API9
payloads: API Key Leaks; Insecure Randomness
---

Treat the app package as a leaked copy of the client: decompile it, pull out the API it talks to and the secrets it carries, check how it protects data and traffic, then hand the API to API testing. Static first; dynamic (emulator + proxy) only if the operator provides a test device setup.

## 1. Unpack and decompile
- Android: `apktool d app.apk` (resources, manifest, smali) and `jadx -d out app.apk` (Java/Kotlin source). AAB → convert with `bundletool` first.
- iOS: unzip the IPA (`Payload/App.app`), `plutil -p Info.plist`, `strings`/`class-dump` on the binary (encrypted App Store binaries need a decrypted copy from the operator).
- Cross-platform shells: React Native (`assets/index.android.bundle` — plain JS), Flutter (`libapp.so` — use `strings`/blutter), Xamarin (`.dll` → ILSpy).

## 2. Extract the API (feeds `api-surface`)
- Base URLs, hostnames, routes, GraphQL operations, websocket endpoints: grep decompiled sources and bundles for `https?://`, `/api/`, `graphql`, `wss://`.
- Environment switches (staging/dev hosts left in the build) → often weaker backends; record them as hosts.
- Record every route with `state_update action=endpoint owasp=[API9]` (inventory) so the api specialist can test them.

## 3. Secrets and keys
- Hard-coded API keys, cloud keys, OAuth client secrets, Firebase config (`google-services.json`: test the Realtime DB/Firestore rules with an unauthenticated read), Sentry/analytics tokens, signing/encryption keys.
- Verify each with one read-only call; live secrets → `action=cred` and chain into the matching playbook.

## 4. Data and transport protection
- Android manifest: `android:debuggable`, `allowBackup`, `usesCleartextTraffic`, network-security-config (user CAs trusted? pinning?), exported activities/services/receivers/providers without permissions, deep-link schemes.
- iOS: `NSAppTransportSecurity` exceptions (`NSAllowsArbitraryLoads`), URL schemes, keychain vs `UserDefaults` for tokens.
- Local storage in code: tokens/PII written to shared prefs, SQLite, files on external storage.
- Certificate pinning: present? Bypassable with a standard hook (only on the operator's test device)? Absent pinning is low on its own — the API findings it enables matter more.

## 5. Auth logic in the client
- Client-side checks the server doesn't repeat (premium flags, role checks, OTP validated in-app) → test the API directly without the client.
- OAuth flows: custom-scheme redirect URIs without PKCE (→ `auth-session`), tokens in logs.

## Record & validate
- `state_update action=vuln` with `owasp=[A04]` (storage/transport crypto), `A07` (client-side auth), `API9` (undocumented/staging APIs); evidence = the decompiled snippet (file + line) and, for secrets, the validity check.
- Hand-off: the endpoint list and any live credentials are the input for the `api` specialist — record them before you finish.
