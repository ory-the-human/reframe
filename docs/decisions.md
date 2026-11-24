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

**Decision.** Turn cozysync into a free, open-source product:

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
Frames live in `/data/frames.json`, managed from the dashboard or `cozysync frames`.

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
  it once in Google Photos, and cozysync assigns them to frames. Albums are
  listed per album and once per run however many frames show them, so quota
  grows with album size, not frame count. Separate devices per frame would only
  move album choice into Google Photos, split it across two apps, and multiply
  sign-in steps.
- The old single-frame settings (`FRAME`, `FRAME_ADDRESS`, `FRAME_FOLDER`) only
  create frames on first start (`FRAME_ADDRESS` may list several, comma-separated);
  existing slideshow links keep working.

