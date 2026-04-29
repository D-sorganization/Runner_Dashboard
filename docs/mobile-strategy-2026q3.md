# Mobile / Native Delivery Strategy — Issue #187 [M01]

**Status:** Research Complete  
**Version:** 1.0  
**Date:** 2026-04-29  
**Author:** runner-dashboard team

---

## 1. Background

Issue #187 requests a spike comparing three mobile delivery strategies for the runner-dashboard operator console. This document synthesizes findings from existing design docs (`mobile-native-shell.md`, `pwa-launcher-design.md`, `mobile-design-system.md`) and current codebase reality to make a recommendation for 2026 Q3.

### Current State

| Attribute | Value |
| --- | --- |
| Frontend | React SPA (Vite build, `frontend/src/main.tsx`) |
| Backend | FastAPI (`backend/`), Python 3.11+ |
| Build | `vite build` → `dist/` static assets served by FastAPI |
| PWA readiness | `manifest.webmanifest` present, service worker not yet implemented |
| Mobile readiness | Breakpoints defined, touch primitives scaffolded, no runtime mobile shell |
| Vite split | In progress (issue #173); `frontend/src/` modules are source-of-truth scaffolding |
| Native wrapper | None |

---

## 2. Comparison Table

| Dimension | PWA | Capacitor | React Native |
| --- | --- | --- | --- |
| **Architecture** | Web app served over HTTPS; installable via browser | Web app wrapped in native WebView; distributed via app store or sideload | Fully native UI; JavaScript bridge to platform APIs |
| **Code reuse** | 100% existing web codebase | ~95%+ same web build; thin native config layer | ~40-60% (new UI layer, separate navigation, re-implement screens) |
| **Build artifact** | Static files (`dist/`) | Static files + native bridge + platform configs | Native binary (iOS `.ipa`, Android `.apk`/`.aab`) |
| **Distribution** | Browser install prompt; no store gate | App Store / Play Store; or enterprise MDM sideload | App Store / Play Store; or enterprise MDM sideload |
| **Offline support** | Service Worker + Cache API | WebView cache + native filesystem APIs | Full control via native modules |
| **Push notifications** | Web Push (cross-platform, Apple support now mature) | Native push via Capacitor plugins (APNs + FCM) | Native push via React Native libraries |
| **Deep linking** | Web URLs (Universal Links / App Links) | Custom URL schemes + Universal Links | Custom URL schemes + Universal Links |
| **Native API access** | Limited to browser APIs | Full via Cordova/Capacitor plugins | Full via community modules and native modules |
| **Update mechanism** | Instant (deploy new build, refresh) | OTA via Capacitor Live Updates; or store update cycle | Store update cycle; CodePush deprecated |
| **Performance** | Good (browser engine) | Good (system WebView) | Best (native UI, no WebView overhead) |
| **Team expertise required** | Existing web stack | Existing web stack + mobile build tooling | React Native ecosystem + native build expertise (Xcode, Android Studio, CocoaPods) |
| **Rollback risk** | Zero (revert web deploy) | Low (OTA or store revert) | Medium (store review delay, binary re-build) |
| **Store review** | None | Apple / Google review required | Apple / Google review required |
| **Security model** | Standard browser sandbox | WebView sandbox + native bridge permissions | Native app permissions, code signing |
| **CI complexity** | Web CI only (GitHub Actions) | Add Fastlane / Xcode / Android build agents | Add Fastlane / Xcode / Android + CocoaPods + Metro bundling |
| **Long-term maintenance** | Web only | Web + thin native wrapper | Separate dependency tree (RN upgrades, module compatibility) |

---

## 3. Approach-Specific Pros and Cons for runner-dashboard

### 3.1 PWA

**Pros**
- ✅ **Immediate deployability** — `dist/` already builds; add service worker and offline cache, done.
- ✅ **Zero app-store friction** — operators install from browser; no review gates for urgent hotfixes.
- ✅ **Fleet UI is already web-first** — 17+ tabs (Fleet, History, Remediation, Maxwell, etc.) work in browser today.
- ✅ **Aligns with `pwa-launcher-design.md`** — custom protocol handler and recovery UI are designed for PWA context.
- ✅ **Fast rollback** — critical for a fleet operations tool where a broken build must be revertible in minutes.
- ✅ **No native build agents needed** — current d-sorg-fleet self-hosted runners are Linux-only; no macOS/Xcode agents required.
- ✅ **Web Push already planned** — issue M06 covers push endpoints; web push is sufficient for agent-dispatch notifications.

**Cons**
- ❌ **Limited native integration** — no true home-screen widget, no background sync with OS-level priority.
- ❌ **iOS restrictions** — Safari push historically lagged (now resolved in iOS 16.4+), but some APIs remain behind WebKit gates.
- ❌ **Discoverability** — operators may not know to use "Add to Home Screen" vs. expecting an App Store app.
- ❌ **No true offline mutations** — queuing mutations offline requires Background Sync API, which has patchy iOS support.

---

### 3.2 Capacitor

**Pros**
- ✅ **Same web build** — `vite build` output is dropped into `ios/` and `android/` web assets; no UI rewrite.
- ✅ **Native push + deep links** — Capacitor Push Notifications and App plugins provide APNs/FCM and URL routing with less boilerplate than RN.
- ✅ **App Store presence** — if operators or enterprise IT demand a store-listed app, this satisfies it.
- ✅ **Simpler than RN** — no JSX-to-native translation, no Hermes bundling, no bridge complexity.
- ✅ **OTA updates** — Capacitor Live Updates can push web-layer fixes without store resubmission.
- ✅ **Matches `mobile-native-shell.md` decision** — already identified as preferred packaging candidate post-Vite split.

**Cons**
- ⚠️ **Requires app-store build pipeline** — need macOS agents for iOS signing, Android SDK agents for `.aab` builds.
- ⚠️ **WebView quirks** — iOS WKWebView and Android WebView have subtle CSS/JS differences; requires device testing.
- ⚠️ **Native plugin dependency** — if a plugin is abandoned, Capacitor community is smaller than RN's.
- ⚠️ **Store review delay** — even with OTA, initial submission and critical native-layer fixes go through review.

---

### 3.3 React Native

**Pros**
- ✅ **True native performance** — native UI threads, no WebView jank on complex lists (e.g., paginated workflow history).
- ✅ **Deepest native API access** — can call HealthKit, Android system metrics, or custom native modules directly.
- ✅ **Ecosystem scale** — largest third-party module library; if a feature needs native bridging, someone likely built it.

**Cons**
- ❌ **Massive UI duplication** — every tab (Fleet, Queue, Machines, Remediation, Maxwell, etc.) needs a React Native screen equivalent.
- ❌ **API contract fork risk** — mobile may need different data shapes (flat lists vs. nested panels), risking drift from backend.
- ❌ **New dependency tree** — React Native versions, CocoaPods, Gradle, Hermes, Metro — all new failure modes.
- ❌ **CI/CD explosion** — need macOS runners, Android emulators, code signing, App Store Connect API credentials.
- ❌ **Team expertise gap** — current stack is web (React + Vite + TypeScript); no RN specialists on fleet team.
- ❌ **Slowest time-to-value** — estimated 6-9 months to parity with current web UI vs. 6-8 weeks for PWA enhancements.
- ❌ **Harder rollback** — binary redeploy via store review vs. instant web revert.

---

## 4. Recommendation

**Adopt a PWA-first + Capacitor-hybrid strategy for 2026 Q3.**

### 4.1 Rationale

1. **Risk minimization** — The dashboard is mission-critical fleet infrastructure. PWA-first keeps rollback instant and eliminates binary distribution risk.
2. **Code reuse maximization** — The Vite split (issue #173) is already creating modular frontend assets. PWA consumes them directly; Capacitor wraps the same `dist/` without a UI fork.
3. **Team velocity** — Zero context switch from existing React + TypeScript + Vite toolchain.
4. **Go/No-Go alignment** — The criteria in `mobile-native-shell.md` (Lighthouse 95+, viewport tests at 375x812 and 412x915, web push, offline snapshots) are achievable in Q3 for PWA. Capacitor packaging follows in Q4 or 2026 Q1 only if those gates pass.
5. **No React Native** — The cost of duplicating 17+ tabs and maintaining a separate native UI layer outweighs performance gains for an operations console that operators use intermittently, not as a consumer social app.

### 4.2 Recommended Phasing

| Quarter | Milestone | Deliverable |
| --- | --- | --- |
| **2026 Q3** | PWA Foundation | Service worker, offline cache, web push, Lighthouse 95+, mobile viewport tests passing |
| **2026 Q4** | PWA Polish | Deep links, auth gates, offline mutation queue, PWA install UX enhancements |
| **2027 Q1** | Capacitor Evaluation | Proof-of-package using production `dist/`; test push/deep-link on iOS/Android hardware |
| **2027 Q1** | Go/No-Go Decision | If PWA metrics and operator feedback are positive, skip Capacitor. If store presence is demanded, ship Capacitor wrapper. |

---

## 5. Migration Path

### 5.1 From Current State to PWA (Q3 Target)

| Step | Task | Owner | Effort |
| --- | --- | --- | --- |
| 1 | Implement service worker (Vite PWA plugin or custom Workbox config) | Frontend | 3d |
| 2 | Add offline cache strategy for static assets + API snapshots | Frontend | 3d |
| 3 | Build offline-state UI (mutating actions show "pending retry") | Frontend | 5d |
| 4 | Implement web push subscription + backend endpoints (M06) | Backend | 4d |
| 5 | Add mobile viewport Playwright tests (375x812, 412x915) | QA | 3d |
| 6 | Audit Lighthouse PWA score; iterate to 95+ | Frontend | 2d |
| 7 | Update `manifest.webmanifest` with screenshots, categories, shortcuts | Frontend | 1d |
| 8 | Document PWA install steps in operator guide | Docs | 1d |

**Estimated total:** ~3 weeks of focused frontend/backend work, parallelizable.

### 5.2 From PWA to Capacitor (Conditional, Q4/Q1)

| Step | Task | Owner | Effort |
| --- | --- | --- | --- |
| 1 | Add Capacitor CLI + iOS/Android platform targets | Mobile | 2d |
| 2 | Configure `capacitor.config.ts` to load `dist/` from server or local | Mobile | 1d |
| 3 | Integrate Capacitor Push Notification plugin (APNs/FCM) | Mobile | 3d |
| 4 | Integrate Capacitor App plugin for deep-link routing | Mobile | 2d |
| 5 | Test on physical iOS/Android devices (WebView behavior, CSS) | Mobile | 3d |
| 6 | Set up App Store / Play Store developer accounts and CI signing | DevOps | 5d |
| 7 | Submit to TestFlight / Internal Testing track | DevOps | 2d |

**Estimated total:** ~3 weeks if proceeding; can be deferred indefinitely.

---

## 6. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- |
| **iOS Web Push reliability gaps** | Low | Medium | Apple shipped web push in iOS 16.4 (2023); test on physical devices early. Fallback: use Capacitor push plugin if web push fails. |
| **Vite split delays** | Medium | High | Issue #173 is a hard dependency. If delayed, PWA work can still proceed on `frontend/index.html` but modular code-splitting benefits are lost. |
| **Capacitor WebView regressions** | Medium | Medium | Maintain PWA as primary channel; Capacitor is additive. If WebView issues arise, operators fall back to browser PWA. |
| **Operator resistance to non-App-Store install** | Medium | Low | Provide clear install guide; PWAs on Android show prominent install prompt. iOS requires Safari "Add to Home Screen" — document with screenshots. |
| **Offline cache bloat** | Low | Medium | Implement cache pruning and versioning in service worker; cap cached API snapshots to last 50 workflow runs / 24h fleet state. |
| **React Native advocacy from stakeholders** | Medium | Low | Reference this document and `mobile-native-shell.md` decision record. Emphasize time-to-value and maintenance burden. |
| **Security: service worker interception of `/api`** | Low | High | Scope service worker to `dist/` static assets only; do not cache authenticated API responses unless explicitly opted in. |

---

## 7. Decision Record

| Decision | Status | Rationale |
| --- | --- | --- |
| PWA first | ✅ Approved | Instant deployability, zero store friction, aligns with existing web-first architecture. |
| Capacitor second (conditional) | ✅ Approved | Same web build; adds store presence and native push only if PWA gates pass and business demands it. |
| React Native | ❌ Rejected for 2026 Q3 | UI duplication, new toolchain, slowest time-to-value. Re-evaluate in 2027 if Capacitor fails. |
| Tauri / Electron | ❌ Rejected | Desktop-only wrappers; issue #187 is explicitly mobile/native. |

---

## 8. Related Documents

- `docs/mobile-native-shell.md` — Prior art: native shell roadmap and go/no-go criteria
- `docs/pwa-launcher-design.md` — Prior art: PWA launcher/recovery UI design
- `docs/mobile-design-system.md` — Mobile tokens, breakpoints, touch primitives
- `docs/vite-migration-plan.md` — Vite split dependency (issue #173)
- `SPEC.md` — API contract that must remain uniform across all clients

---

## 9. Success Criteria

1. PWA Lighthouse score ≥ 95 by end of Q3.
2. All dashboard tabs usable at 375x812 and 412x915 viewports.
3. Web push delivers agent-dispatch notifications within 5 seconds.
4. Offline fleet snapshot visible when backend unreachable; mutating actions show retry state.
5. No separate mobile API contract; desktop, PWA, and future Capacitor use identical `/api/*` routes.
6. Document approved and linked from issue #187 before any implementation PRs merge.
