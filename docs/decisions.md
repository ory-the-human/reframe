# Decisions

Newest last. Each entry records what we decided, why, and what it changes.

## 1. Use the Google Photos Ambient API (2026-10-30)

**Decision.** Sync through Google's Photos Ambient API, the official API for photo
frames, instead of a public share link.

**Why.** It keeps albums private: the user signs in once with the TV-style
device-code flow and picks albums inside Google Photos, and the service only sees
what was picked.

**Status.** Blocked for personal use, see 2.

## 2. The Ambient API needs the partner program (2026-11-23)

**What happened.** With a personal Google Cloud project (consent screen in
Testing, the owner as test user, a "TVs and Limited Input devices" client), sign-in
and the token exchange succeeded, but registering the frame failed:

```
POST /v1/devices -> 403 PERMISSION_DENIED
"You do not have permission to perform the requested operation. Please refer to
the partner program at https://developers.google.com/photos/partner-program/overview."
```

**What we learned about the partner program**
([overview](https://developers.google.com/photos/partner-program/overview),
[Ambient API](https://developers.google.com/photos/ambient/guides/about)):

- "To get access to the Ambient API, you must first be accepted into the partner
  program." Applying is an
  [expression-of-interest form](https://services.google.com/fb/forms/photospartnerprogram/).
  Re-apply with new information if the last application is more than 6 months old.
- After acceptance, an app must pass two reviews before launch: the OAuth
  verification review and a Google Photos APIs integration review (details given
  only on acceptance).
- No published eligibility criteria, user thresholds or timeline. No test mode:
  even the project owner as a test user gets 403.
- The Library and Picker APIs don't need the program, but neither can follow a
  user's existing album (Library only reads app-created items since March 2025;
  Picker needs a manual pick per session).

**Decision.** A single-user personal project can't use the Ambient API. Don't
wait on approval for the owner's own frame.

## 3. Build an open-source, self-hosted worker with a hosted auth broker (2026-11-23)

**Decision.** Turn reframe into a free, open-source product:

- A **self-hosted worker** that each user runs at home (NAS, Raspberry Pi,
  laptop). It lists their albums, streams photos to their frames, and keeps only
  sync state.
- A small **auth broker** that we host. It owns the Google OAuth client (and
  therefore the partner-program approval), runs sign-in, and refreshes tokens.
  Photos go straight from Google to the user's worker and never pass through it.

Design, requirements and the partner-program application draft are in
[hosted-auth.md](hosted-auth.md).

**Why.** A product with many users is what the partner program is for; one
person's frame isn't. Keeping the worker self-hosted means we never store or see
anyone's photos, which keeps the hosted part small and easy to defend in Google's
reviews.

**Consequences.**

- Applying to the partner program is the first step and gates the Google
  source. Approval may never come.
- Running the broker is an ongoing commitment: uptime (if it's down, syncs stop
  within about an hour), a domain, a privacy policy, OAuth verification, and
  possibly a yearly security assessment.
- Until (and unless) Google approves us, the worker must be useful with other
  photo sources. See 4.

## 4. Sources and frames are plug-ins (2026-11-23)

**Decision.** The worker has two kinds of plug-in:

- **Sources** say where photos come from: list albums, list an album's items
  completely (or say the listing is incomplete), and fetch one item resized for
  the frame. First: Google Photos Ambient (blocked, kept for when approval
  comes). Next: a Google Photos share link and Immich.
- **Frames** say where photos go: check the connection, put a file, remove a
  file. First: Cozyla over adb, and a plain folder (covers anything that reads a
  directory or network share). Later: TVs and other frames.

The sync engine (PLAN.md phase 2) only talks to these interfaces, so adding a
source or a frame doesn't touch it.

**Why.** It gets the owner's frame working now with a non-Google-API source, and
none of the work is lost if the partner program says yes or no.

## 5. One worker drives several frames (2026-11-23)

**Decision.** A worker manages a list of frames, not one. Each frame has a name,
a type and target (adb address, folder, or its own slideshow link), the albums it
shows (none ticked means all), and display settings that override the defaults.
Frames live in `/data/frames.json`, managed from the dashboard or `reframe frames`.

**Why.** Households have more than one screen (the owner already does), and
running a worker per frame would mean one sign-in and one container per screen.

**Consequences.**

- The sync engine (PLAN.md phase 2) keeps state per frame: what each frame has
  and when it was last seen in a listing. An album shown on two frames is listed
  once per run and pushed to both.
- Display settings resolve in three layers: defaults in settings, the frame's
  overrides, then the screen's URL parameters.
- With the Google Ambient source, the worker registers **one** Ambient device
  per Google account, whatever the number of frames. The user picks albums for
  it once in Google Photos, and reframe assigns them to frames. Albums are
  listed per album and once per run however many frames show them, so quota
  grows with album size, not frame count. Separate devices per frame would only
  move album choice into Google Photos, split it across two apps, and multiply
  sign-in steps.
- The old single-frame settings (`FRAME`, `FRAME_ADDRESS`, `FRAME_FOLDER`) only
  create frames on first start (`FRAME_ADDRESS` may list several, comma-separated);
  existing slideshow links keep working.

## 6. Home Assistant is an add-on, never a requirement (2026-11-24)

**Decision.** A frame works with reframe alone. reframe runs and controls it:
photos, the slideshow, display settings, and screen on/off and brightness where
the frame allows it. Home Assistant, when present, is an extra way in: reframe as
an HA add-on, its status and controls as HA entities, optionally HA automations
driving the screen or an HA dashboard as a second page on the frame.

**Why.** Most people with a photo frame don't run Home Assistant, and a frame that
goes blank when HA is down or missing isn't a photo frame.

**Consequences.**

- Nothing on the frame may depend on HA being installed or reachable. Features
  that HA offers (schedules, brightness) need a reframe path too.
- The HA items in [roadmap.md](roadmap.md#home-assistant) are packaging and
  integration, not prerequisites.


## 7. Cozyla frames run the slideshow in an open-source kiosk app (2026-11-24)

**Decision.** reframe provisions a Cozyla frame as a kiosk: it installs
[Webview Kiosk](https://f-droid.org/en/packages/uk.nktnet.webviewkiosk/)
(AGPL-3.0, F-Droid, pinned version and checksum), writes its settings over adb
(slideshow link, no toolbars, settings locked by a generated password), and turns
off the Cozyla app so the kiosk is the home screen. It records the frame's original
state first, and restoring puts it back exactly.

**Why.** Inside the Cozyla app our photos only stay while the frame is offline
(its syncs remove anything Cozyla's cloud doesn't know; see
[cozyla-frame.md](cozyla-frame.md)). Fully Kiosk Browser worked, but its remote
admin, PIN lock and light-sensor sleep are paid features that put a watermark on
the screen without a licence, and it asks for camera, microphone and location
access. FreeKiosk needs Android 8 and these frames run Android 6; WallPanel is
archived. Webview Kiosk runs on Android 5+, is maintained, and has MQTT for later.

**Consequences.**

- reframe, not the kiosk app, manages the frame (over root adb), so schedules,
  light-sensor sleep and updates are reframe features.
- The Cozyla app's features (Talking Photo, phone uploads, its sleep schedule)
  stop on a kiosk frame until it's restored.
- Android saves an app being turned on or off about 10 seconds later; provisioning
  waits for that, or a reboot would bring the Cozyla app back.

## 8. The project is called reframe (2026-11-25)

**Decision.** The project, its Python package and command, the Docker image and the
frame agent (`app.reframe.agent`) are called reframe, replacing the working name
cozysync.

**Why.** It goes public soon and isn't Cozyla-only. The name says what it does:
it gives frames you bought a new picture and a new owner. It's short to type.

**Consequences.**

- The agent's package id changed before its first release, so no installed
  agent needs migrating.
