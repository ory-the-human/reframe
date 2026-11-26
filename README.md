# reframe

A self-hosted worker that syncs photo albums to photo frames. Photo **sources**
and **frames** are plug-ins:

| Sources | Frames |
|---|---|
| `share_link` (default): Google Photos shared albums, added by their share links | `cozyla_adb`: a Cozyla (or rooted Android) frame over adb on Wi-Fi |
| `google_ambient`: Google Photos via the [Photos Ambient API](https://developers.google.com/photos/ambient/guides/about) (needs the Google Photos partner program) | `folder`: any folder or mounted share a display reads from |
| next: Immich | `web`: a full-screen browser slideshow served by reframe, for any kiosk browser, tablet, TV browser or web view |

One worker drives any number of frames, each with its own type, albums and
display settings. Pick the source in `.env` or the web UI's settings, and add
frames on the dashboard or with `reframe frames add`. It runs as a Docker
container, for example on a NAS.

See [PLAN.md](PLAN.md) for the plan, [docs/decisions.md](docs/decisions.md) for why
it looks like this, [docs/hosted-auth.md](docs/hosted-auth.md) for the planned
hosted Google sign-in, [docs/cozyla-frame.md](docs/cozyla-frame.md) for Cozyla
discovery and security, and [docs/roadmap.md](docs/roadmap.md) for where it's going
(including Home Assistant).

**Status:** shared albums sync to every frame type. The Ambient API returns 403
for projects outside Google's partner program, so that source only works for an
accepted project.

## 1. Share an album

In Google Photos, open the album, then **Share → Create link**, and copy the
link (`https://photos.app.goo.gl/…`). Anyone with the link can see the album, so
treat it as a password; turning link sharing off for the album (or deleting the
link) revokes it. reframe keeps the links in `data/albums.json`.

## 2. Start it on the NAS

```sh
cp .env.example .env   # set WEB_PASSWORD; everything else can be done in the UI
docker compose up -d --build
```

On NixOS, use the published image instead: add
[deploy/nixos/reframe.nix](deploy/nixos/reframe.nix) to `imports`, write the
settings to `/var/lib/reframe/reframe.env` (the same lines as `.env.example`) and
run `nixos-rebuild switch`. To update, change `version` in that file and rebuild.
The CLI commands below run there as `sudo reframe …` instead of
`docker compose run --rm reframe …`.

Open `http://<nas-ip>:1992`. Log in with any username and your `WEB_PASSWORD`.

1. **Settings → Albums**: paste each album's share link, with a name if you want
   another than the album's title (or `reframe albums add LINK --name …`). Or
   create a **local album** by name: it lives on the server in
   `data/albums/<album id>/`, whatever the source. On its page, upload photos and
   videos (also from a phone's browser) and delete them; files copied into its
   folder by hand show up too. A file deleted from a local album leaves the frames
   at the next sync, and removing the album deletes its files. Rename an album by
   editing its name in the list.
2. **Frames → Add a frame** for each screen: its type, the albums it shows (none
   ticked means all) and, for slideshows, its display settings. **Check**
   confirms the worker can reach it. For `cozyla_adb`, a Cozyla connects without
   asking: its adb has no authentication and runs as root, so keep it on an
   isolated network ([docs/cozyla-frame.md](docs/cozyla-frame.md)). Other Android
   frames show an "Allow USB debugging?" prompt the first time; tick "Always
   allow" and check again (the key is kept in `data/.android/`). For `folder`,
   mount the folder in `docker-compose.yml`. For `web`, the dashboard shows the
   frame's slideshow link and a QR code.
3. **Sync**: the server syncs every `SYNC_MINUTES` (Settings → "Minutes between
   syncs", default 60, 0 for off), starting 30 seconds after it starts. **Sync now**
   on the dashboard runs one at once; the dashboard shows what the last run did
   per frame and how many photos each frame holds. `reframe sync --dry-run` shows
   what a sync would change without changing anything. New photos are copied to
   the frames; a photo that leaves an album is removed after it's missing from two
   complete listings in a row (longer if many leave at once), and an album a frame
   stops showing is removed from it at once. New videos are re-encoded for each
   frame's screen (H.264, sides a multiple of 32, which the Cozyla frame needs);
   turn this off in Settings → Videos or per frame.
4. **Listing test** (optional): lists every album in full and saves a snapshot to
   `data/`. Set "Download samples" above 0 to fetch a few files sized for the
   frame. Run it again after adding or removing a photo; the dashboard shows the
   changes between the last two snapshots.

### Google Photos Ambient source (needs partner approval)

Only for a Google Cloud project accepted into the
[Google Photos partner program](https://developers.google.com/photos/partner-program/overview);
otherwise registering the frame fails with 403 `PERMISSION_DENIED`.

1. Go to <https://console.cloud.google.com/> and create a project, e.g. `reframe`.
2. **APIs & Services → Library**: search for **Photos Ambient API** and click **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External**, app name `reframe`, with your email as support and
     developer contact.
   - **Data access / Scopes**: add `https://www.googleapis.com/auth/photosambient.mediaitems`.
   - **Audience / Test users**: add your own Google account.
4. **Credentials → Create credentials → OAuth client ID**, application type
   **TVs and Limited Input devices**. Copy the client ID and secret.
5. In **Settings**, pick the Ambient source and paste the client ID and secret.
6. **Connect Google Photos**: the dashboard shows a code and a QR code. Sign in
   on your phone with the test-user account and pick the albums for the frame.
   The page updates by itself as each step finishes.

### Browser slideshow (`web` frames)

Open a slideshow frame's link from the dashboard on any screen with a browser. It plays
full screen, fades between photos, picks up new photos and setting changes within
10 seconds, and plays videos muted.

- **Display settings**: seconds per photo, one photo or two, orientation,
  shuffle, what fills the space around a photo (a blurred copy, or black), and the
  date each photo was taken, shown at its bottom. Defaults are in Settings → Display; each frame can override them on
  its page, and each screen again in its link.
- **Two photos:** when a photo's orientation doesn't match the screen (portrait
  photos on a landscape screen), two of them share it, side by side or stacked.
- **Orientation:** "Follow the screen" uses what the browser reports. If a screen
  is mounted sideways and its browser doesn't rotate, pick the orientation and the
  slideshow rotates itself.
- **Swipe** left or right (or press the arrow keys) for the next or previous photo.
- **Per-screen overrides:** add them to the frame's link, e.g.
  `?interval=10&layout=pair&orientation=portrait&shuffle=no&fill=black&date=no`.
- The link contains a secret instead of the UI password, because kiosk browsers
  can't answer a password prompt. Only give it to your screens.

### Cozyla frames as kiosks

On a Cozyla frame's page, **Set up as a reframe kiosk** replaces the Cozyla app
with the reframe slideshow, in about a minute:

1. Installs [Webview Kiosk](https://f-droid.org/en/packages/uk.nktnet.webviewkiosk/)
   (open source, from F-Droid, checked against a pinned checksum).
2. Points it at the frame's slideshow link, hides its toolbars, and locks its
   settings with a generated password (shown on the frame's page). On the frame,
   tap the top-left quarter of the screen 10 times to open its settings.
3. Turns off the Cozyla app, so the kiosk is the home screen, also after a reboot.

reframe records how the frame was first. **Restore the Cozyla app** puts it back
exactly: the Cozyla app on and the kiosk app removed. The Cozyla app keeps its
photos while it's off. A frame set up as a kiosk can't be removed from reframe
until it's restored. The link uses Settings → "Address frames use to reach this
server" (`PUBLIC_URL`), or the address you opened the web UI with.

Kiosk frames also get a **Screen** section (a schedule and turning off in a dark
room, with defaults in Settings) and a **Health** section (temperatures, load,
memory, storage, uptime). Both come from the reframe agent, a small app that kiosk
setup installs on the frame; see [docs/agent.md](docs/agent.md). The agent also
keeps the frame as setup left it, and closes the frame's adb (root, no password,
open to the network) 2 minutes after it powers on. To update or restore a kiosk,
unplug the frame, plug it back in, and start within those 2 minutes; adb stays
open while reframe is connected. From agent 0.3.0 the frame plays the slideshow
from its own copy of the photos, so it keeps going while the server is off, and the
frame's page can take a screenshot of it. To try a local agent build, set `AGENT_APK` to it
(`agent/build.sh`).

### CLI

The same steps are available on the command line, which is handy when testing
from a laptop:

```sh
docker compose run --rm reframe albums                          # shared and local albums: id and name
docker compose run --rm reframe albums add https://photos.app.goo.gl/… --name "Holidays"
docker compose run --rm reframe albums rename Holidays "Summer"
docker compose run --rm reframe albums remove Summer            # its photos leave the frames at the next sync
docker compose run --rm reframe sync --dry-run                  # what a sync would change
docker compose run --rm reframe sync                            # sync all frames now
docker compose run --rm reframe login         # google_ambient: sign in, pick albums
docker compose run --rm reframe device        # google_ambient: device, albums, token age
docker compose run --rm reframe list --download 3
docker compose run --rm reframe diff          # compare the last two snapshots
docker compose run --rm reframe frames                          # list frames
docker compose run --rm reframe frames add "Kitchen" --type web
docker compose run --rm reframe frames add "Living room" --type cozyla_adb --address 192.168.1.50:5555
docker compose run --rm reframe frames show Kitchen             # details and slideshow link
docker compose run --rm reframe frames edit Kitchen --interval 10 --fill black --album ID
docker compose run --rm reframe frames edit Kitchen --interval ""   # back to the default
docker compose run --rm reframe frames edit Kitchen --convert-videos no  # copy videos as downloaded
docker compose run --rm reframe settings                        # list settings
docker compose run --rm reframe settings set public_url http://192.168.1.10:1992
docker compose run --rm reframe settings unset display_layout   # back to .env or the default
docker compose run --rm reframe signout [--forget-device]       # google_ambient
docker compose run --rm reframe frame-check                     # all frames, or name one
docker compose run --rm reframe kiosk setup "Living room" --url http://192.168.1.10:1992
docker compose run --rm reframe kiosk show "Living room"       # settings password
docker compose run --rm reframe kiosk restore "Living room"    # back to the Cozyla app
docker compose run --rm reframe frames edit "Living room" --schedule 23:00-07:00 --dark yes
docker compose run --rm reframe health                          # what the frame agents report
```

Running locally without Docker (Python 3.9+):

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
DATA_DIR=./data .venv/bin/reframe web --port 1992
```

## Notes

- Photos are pass-through: the sync streams each one to the frame and keeps
  only state on the NAS, except for local albums (`data/albums/<id>/`) and `web`
  and kiosk frames, which play from the server and keep their photos in
  `data/frames/<id>/`. `data/samples/` is only for
  the listing test.
- `data/` holds the album share links, OAuth tokens and the OAuth client secret. It's git-ignored and
  written with `0600` permissions.
- The web UI is for the LAN only; don't expose port 1992 to the internet. Set
  `WEB_PASSWORD`, because without it anyone on your network can use the UI.
  Cross-site form posts are rejected.
- Adding a source: implement `Source` from `src/reframe/sources/base.py` and
  register it in `sources/__init__.py`. Adding a frame: implement `Frame` from
  `src/reframe/frames/base.py` and register it in `frames/__init__.py`.
- Releasing the server: set `version` in `pyproject.toml`, merge it, and push the
  tag `v<version>`. The `Server release` workflow publishes
  `ghcr.io/ory-the-human/reframe:<version>` (and `:latest`) for amd64 and arm64.
- The Ambient API allows 240 `mediaItems.list` calls per device per day, with up
  to 100 items per call.
- While the OAuth app is in **Testing** status, Google's refresh tokens expire
  after 7 days. `reframe device` prints the token's age so we can confirm this.
