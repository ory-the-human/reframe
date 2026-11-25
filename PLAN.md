# Cozyla frame sync — plan

Goal: keep the owner's two Cozyla photo frames showing a Google Photos album
privately, without a public share link, using a small Docker service on the NAS.

**Direction as of 2026-11-23** (details in [docs/decisions.md](docs/decisions.md)):
the Ambient API needs Google's partner program, so reframe becomes an open-source,
self-hosted worker with pluggable **sources** (where photos come from) and
**frames** (where they go), plus a hosted auth broker for Google if we're accepted
([docs/hosted-auth.md](docs/hosted-auth.md)). Phases below are updated for that.
Longer-term paths (Home Assistant, more frames) are in [docs/roadmap.md](docs/roadmap.md).

## What we know about the frame

- Model Cozyla CD-3X341F, Android 6.0.1 (API 23), 800x1280 portrait panel,
  ~24 GB free on `/sdcard`.
- ADB over Wi-Fi on port 5555 is enabled and the shell runs as **root**.
- The display app is `com.cozyla.frame` (v1.0.0.2025012014). Its library lives in
  a Room database: `/data/user/0/com.cozyla.frame/databases/CzlItemDataBase.db`,
  table `ItemEntry`.
- Google Photos rows (the synced album's photos and videos) store only a
  `google_media_id`; the app fetches the bytes from Google at play time, which is
  what broke in 2025. The newest Google row was inserted April 2025.
- The built-in "Art gallery" rows point at local files under
  `/sdcard/Android/obb/com.cozyla.frame/media/IMAGE/...`, so the app can play
  local files that are listed in its database.
- Security: anyone on the LAN gets root on the frame via port 5555. Keep it off
  guest or shared networks.

## Approach

```
source ──────────────> worker (NAS container) ──────────> frame
Google Ambient,         lists albums, fetches              Cozyla (adb), folder,
share link, Immich      resized, tracks state in /data     TVs later
```

Code: `src/reframe/sources/` (the `Source` interface in `base.py`, one package or
module per source) and `src/reframe/frames/` (the `Frame` interface, one module per
frame type). The configured pair is chosen with `SOURCE` and `FRAME`.

The original Google Ambient design:

```
Google Photos ──(Ambient API, OAuth)──> NAS container ──(adb push)──> frame
  you pick albums            downloads + resizes           shows the photos
  in Google Photos           tracks state in /data
```

The Ambient API is Google's official API for photo frames. The user signs in once
using the TV-style device-code flow, then picks albums inside Google Photos. The
service can only see what was picked, so no public link is needed.

## Phase 1: prove the Ambient API works for a personal project

1. Create a Google Cloud project, enable **Photos Ambient API**, configure the
   OAuth consent screen (External, add yourself as a test user), and create an
   OAuth client of type **TVs and Limited Input devices**.
2. Put the client ID and secret in `.env` and run
   `docker compose run --rm reframe login`. This runs the device-code flow with
   the streamlined `state`, creates the Ambient device, and waits until albums
   are picked.
3. Run `docker compose run --rm reframe list --download 5`, then check the
   results.

Questions phase 1 must answer:

- [x] Can a personal, unverified project enable the API and complete consent?
      **No (2026-11-23).** Consent and the token exchange succeed, but
      `POST /devices` returns 403 `PERMISSION_DENIED`, pointing to the
      [partner program](https://developers.google.com/photos/partner-program/overview).
      The API only works for approved partners, so the remaining questions
      can't be tested.
- [ ] Does `mediaItems.list` with `mediaSourceId` return the **whole album**, or
      only a curated or rotating subset like the default "ambient" mode?
- [ ] Do photos added to the album later show up, and how quickly?
- [ ] Does a photo **removed** from the album disappear from the listing, and
      how quickly? Test: run `list --all-sources`, remove a photo in Google
      Photos, run it again, then run `diff`.
- [ ] Are full listings stable? Two runs back to back with no changes should
      give `diff` = no changes. If items come and go, the listing is a sample,
      and removal detection needs a different approach.
- [ ] Do `baseUrl` downloads need the bearer token? How long do they last?
- [ ] Are videos returned, and does `=dv` work?
- [ ] **Refresh-token lifetime.** Apps in "Testing" status get 7-day refresh
      tokens. Check whether the app can be moved to "In production" without
      verification for personal use. If not, the service will need a re-login
      every 7 days, which may make this route impractical.
- [ ] Is 240 list requests per device per day enough? At 100 items per page,
      it should be fine for a sync every few hours.

**Result: phase 1 failed on the first question** (403 at device registration).
Next steps:

- Apply to the partner program with the draft in
  [docs/hosted-auth.md](docs/hosted-auth.md). If accepted, re-run this checklist.
- Meanwhile, add another source so the frame works now: (a) an unlisted share link
  that only the NAS knows, which can be revoked; (b) a self-hosted Immich album.

## Phase 2: sync engine (pass-through)

The engine only uses the `Source` and `Frame` interfaces: `albums()`,
`list_album()` (with a `complete` flag), `fetch()`, and `put()` / `remove()`.
The Ambient-specific details below (media ids, quota) apply when that source is used.

The NAS doesn't keep a copy of the photos. Each one is downloaded to a temp
file, pushed to the frame, and deleted. The frame holds the only copy. The NAS
keeps just the state.

The frames list moves from `/data/frames.json` into the same database, so frame
ids can key the sync state (the web UI keeps managing frames as today).

State lives in `/data/state.sqlite`, one row per (frame, album, media id). Each run
lists every album shown by at least one frame once, then updates each frame:

| column        | purpose                                                    |
|---------------|------------------------------------------------------------|
| `frame_id`    | which frame has it (see `/data/frames.json`)               |
| `source_id`   | album (Ambient media source) it belongs to                 |
| `media_id`    | Ambient item id (persistent across sessions, per the docs) |
| `frame_path`  | where it lives on the frame                                |
| `first_seen`  | when it was first synced                                   |
| `last_seen`   | last complete listing that contained it                    |
| `missing_runs`| consecutive complete listings it was absent from           |

Each run:

1. Read the device's selected albums.
2. For each album, list **every** item. Use `mediaSourceId` per album, because
   the default mode is a curated mix and can't be used to detect removals.
3. **New items** (in the listing, not in state): download them resized to fit
   1280px (`=w1280-h1280` for photos, `=dv` for videos), push to the frame,
   delete the temp file, and insert a state row.
4. **Seen items**: update `last_seen` and reset `missing_runs` to 0.
5. **Removed items** (in state, not in the listing): increment `missing_runs`.
   Once it reaches `REMOVE_AFTER_RUNS` (default 2), delete the file from the
   frame and drop the row.
6. **Deselected albums** (a source no longer in `mediaSources`) get the same
   treatment for all their items.

**As built:** the frames list stays in `/data/frames.json`; the sync state is keyed
by frame id, so moving it wasn't needed. Albums a frame stops showing are removed
from it at once rather than after missed runs: that's the owner's choice, not an
uncertain listing.

### Removal safeguards

A wrong "removed" deletes photos from the frame. They come back on the next
sync, but that means a needless re-download. So removal is deliberately
conservative:

- **Only complete listings count.** The docs say a missing `nextPageToken`
  "is not necessarily an indication that no more content is available." A run
  that errors, hits the page cap, or returns zero items for a non-empty album
  doesn't increment `missing_runs`.
- **Grace period.** An item has to be missing from `REMOVE_AFTER_RUNS`
  consecutive complete listings, which absorbs API hiccups.
- **Mass-removal brake.** If one run would remove more than
  `MAX_REMOVE_FRACTION` (default 25%) of an album, skip the deletions, log a
  warning, and wait for the next run to confirm.
- The same photo in two albums is two state rows that share one file on the
  frame. The file is deleted only when no row for that frame references it.
- The mass-removal brake and grace period apply per frame and album.

### Quota

Each page is one of the 240 `mediaItems.list` calls per device per day, at 100
items per page. Syncing every 2 hours uses 12 runs × pages per album. That
comfortably covers several albums of a few hundred photos each.

## Phase 3: show the photos on the frame (two options, test A first)

- **A: inside the Cozyla app.** Push files to
  `/sdcard/Android/obb/com.cozyla.frame/media/IMAGE/<album>/`, then insert
  `ItemEntry` rows with the `media_path_local_*` columns set. Do this by stopping
  the app, editing the DB over adb (`sqlite3` on the device, or pull, edit,
  push), and restarting it.
  When an item is removed, delete its `ItemEntry` row as well as the file;
  otherwise the app keeps a dangling entry.
  Risks: Cozyla's cloud sync may delete or overwrite the rows, and app updates
  can change the schema. Back up the DB first, and check the schema hash in
  `room_master_table` before each write.
- **B: bypass the Cozyla app.** Push files to `/sdcard/DCIM/Frame/` and show them
  with the built-in Android Photo Frame screensaver, or a sideloaded slideshow
  app set as the launcher.

## Phase 4: run unattended on the NAS

- `restart: unless-stopped`, with a loop that runs every N hours (`SYNC_INTERVAL`).
- Health and alerting: log the last successful sync, and warn when the refresh
  token fails.
- The ADB key is stored in `/data/.android`, so the frame prompts "Allow USB
  debugging" only once, from the NAS.

## Phase 5: hosted auth broker (only if the partner program accepts us)

Build the broker described in [docs/hosted-auth.md](docs/hosted-auth.md), go
through OAuth verification and the Google Photos integration review, and switch
the `google_ambient` source to get its access tokens from the broker instead of
a local OAuth client.
