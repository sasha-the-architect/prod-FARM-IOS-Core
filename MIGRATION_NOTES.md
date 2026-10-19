# Migration Notes — mac-farm-controller → Phone Farm iOS (prod-FARM-IOS-Core)

**Date:** 2026-10-19
**Decision:** The hand-built Python/FastAPI controller has been replaced wholesale
by the open-source **Phone Farm iOS** stack
([Git-Agni/prod-FARM-IOS-Core](https://github.com/Git-Agni/prod-FARM-IOS-Core)),
forked to [sasha-the-architect/prod-FARM-IOS-Core](https://github.com/sasha-the-architect/prod-FARM-IOS-Core).
The old codebase is preserved verbatim at `~/mac-farm-controller-legacy`.

## Why replace instead of continue

The legacy controller had several core pieces still stubbed or uncalibrated
(placeholder coordinates, `post_video` file-picker stub, account-switcher
"RESEARCH TODO" coordinates). Phone Farm iOS ships all of that working, on real
devices, with a plugin architecture:

| Capability | Legacy (Python) | Phone Farm iOS (TypeScript) |
| --- | --- | --- |
| Device control | facebook-wda / go-ios + VoiceControl fallback | Appium 3 + XCUITest + patched WDA (USB-forwarded, supervised) |
| Scheduling | Poll `pending_commands` table (Railway PG) | pg-boss per-device queues + drizzle schema + recurrence, run windows, retry/stop |
| Posting | Coordinate taps, file-picker stubbed | Full flow: WDA media import, Photos picker w/ red-checkbox pixel detection, caption, draft **and** publish, music deep-link |
| Engagement | Fixed coords, AX-source scraping | Template-matched like/save detection (sharp), 3 doomscroll personalities |
| Account switch | Placeholder coords (RESEARCH TODO) | OCR (node-native-ocr) located switcher + verified post-switch confirmation |
| Dashboard | none on this host (Railway dashboard) | Built-in Fastify+HTMX dashboard, live MJPEG screen, remote control, registration wizard |
| Tests | Unit tests, no integration w/ device | 29 tests: API, scheduler guards, coordinates, device lifecycle, TikTok plugin |

## What was migrated

| Item | Source (legacy) | Destination (new) |
| --- | --- | --- |
| Device registry | `config/devices.json` (spare SE1, iOS 15) | `devices.json` (0600, git-ignored) — final registry: **iPhone sash** `00008101-001670AA3669001E` (iOS 26.7.1) + `coordinateProfile: iphone8` |
| Apple Development Team ID `M4TFAR3S8T` | `scripts/renew_wda.sh` | `.env` `XCODE_ORG_ID` |
| WDA bundle id `com.sashapaskal.wda.xctrunner.xctrunner` | `scripts/renew_wda.sh` | `.env` `WDA_BUNDLE_ID` |
| TikTok bundle id | hardcoded | `.env` `TIKTOK_BUNDLE_ID=com.zhiliaoapp.musically` (unchanged) |
| Content calendar template | `config/content_calendar_template.csv` | `migrated/content_calendar_template.csv` |
| iPad calibration coordinates | `config/tiktok_coords.json`, `config/post_coords.json` | `migrated/*_legacy.json` (reference only — new stack uses point-space profiles) |
| VoiceControl command map (iOS 15 fallback) | `config/devices.json` strategy_args | **Not portable** — see below |
| launchd deployment | `deploy/com.sashapaskal.farm-worker.plist` | `deploy/com.sashapaskal.phonefarm.{appium,wda-service,worker,web}.plist` + `install.sh` |

## Local infrastructure changes

- **PostgreSQL 14** (Homebrew) now runs locally via `brew services start postgresql@14`
  — no Docker needed. Database `phone_farm`, user `phone_farm`, password in `.env`
  (git-ignored). Both drizzle scheduler migrations applied (`npm run db:migrate`).
- **Appium XCUITest driver**: the upstream repo pins `xcuitest@7.26.3`, which is
  incompatible with the Appium 3.7.0 server it ships (requires ^2.5.4). Fixed by
  installing `xcuitest@12.19.1` and patching `package.json` accordingly.

## Known gaps / follow-ups

1. **Device inventory (2026-10-19, via `xcrun xctrace list devices`)**: the actual
   hardware is **iPhone sash** (`00008101-001670AA3669001E`, iOS 26.7.1 — now the
   registered device) and an **iPad** (`00008020-000525E23C45402E`, iOS 26.7.1,
   not yet registered). The legacy spare SE1 (`3a12427b…`, iOS 15.8.8) is
   **permanently incompatible** — SE1 hardware is capped at iOS 15 and Phone Farm
   requires iOS 16+; it stays retired with the legacy tree (VoiceControl path).
2. **Coordinate profile gap for iPhone sash**: the only shipped tap layout is
   `iphone8` (375×667). A modern iPhone has a different geometry, and dashboard
   "Touch points" overrides are bounds-checked against the *profile's* screen
   size — so a taller screen cannot be fully calibrated per-device. Doomscroll
   is largely unaffected (like/save are template-matched on the live screen at
   runtime and the feed swipe is geometry-tolerant); **post** and
   **account-switch** need a proper profile for the exact model. Plan: once the
   phone is USB-attached and WDA is up, read `productType` + screen size, measure
   every target off the live screen (`GET /api/devices/:udid/remote/screenshot`),
   verify with test taps, then add the profile to `src/devices/coordinates.ts` +
   `src/tiktok/coordinates.ts` (docs/coordinates.md workflow).
3. **TikTok accounts**: the legacy repo had no committed account credentials file.
   Register handles per device in the dashboard (device page → TikTok panel) or
   `PATCH /api/devices/:udid/accounts`. Accounts must already be logged in to
   the TikTok app on the phone — the plugin switches between logged-in sessions.
4. **Device passcode**: not carried over. Set it in the dashboard registration
   wizard or `PATCH /api/devices/:udid` (`{"passcode":"…"}`) so scheduled tasks
   can unlock the phone.
5. **WDA signing**: run `npm run wda:prepare` from a graphical Terminal session
   (keychain must be unlocked) with the phone plugged in; it patches and signs
   the Appium-bundled WebDriverAgent with team `M4TFAR3S8T`.
   `xcode-select -p` verified → `/Applications/Xcode.app/Contents/Developer`.
6. **Legacy launchd agents retired (2026-10-19)**: `com.sashapaskal.farm-worker`
   (old Python uvicorn, was crash-looping on :5002) and
   `com.sashapaskal.wda-supervisor` (was polling for the unplugged iPad) were
   unloaded and their plists removed from `~/Library/LaunchAgents`. Ports
   3000/4725/5002/8100/9100 verified free.

## Verified working (2026-10-19)

- `npm run check` — typecheck clean, **29/29 tests pass**
- `npm run appium:install-driver` — XCUITest 12.19.1 installed
- `npm run db:migrate` — scheduler + pgboss schemas created
- `npm run web` — dashboard on :3000, `GET /health` → `{"ok":true,"plugins":[{"id":"com.git-agni.tiktok","version":"0.1.0"}]}`
- `npm run worker` — `Worker listening on ios-device-b4549fa2a8befb1af083 for iPhone-Spare-01`
- `GET /api/devices` — migrated device registered; `/api/devices/:udid/coordinates` resolves the iphone8 profile

## Runbook (always-on host)

```sh
brew services start postgresql@14
cd ~/mac-farm-controller
bash deploy/install.sh        # starts appium, wda-service, worker, web
# dashboard: http://127.0.0.1:3000
bash deploy/install.sh stop    # stop everything
```
