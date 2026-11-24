# cozysync

A self-hosted worker that syncs photo albums to photo frames. Photo **sources**
and **frames** are plug-ins:

| Sources | Frames |
|---|---|
| `google_ambient`: Google Photos via the [Photos Ambient API](https://developers.google.com/photos/ambient/guides/about) (needs the Google Photos partner program) | `cozyla_adb`: a Cozyla (or rooted Android) frame over adb on Wi-Fi |
| next: Google Photos share link, Immich | `folder`: any folder or mounted share a display reads from |
| | `web`: a full-screen browser slideshow served by cozysync, for any kiosk browser, tablet, TV browser or web view |

One worker drives any number of frames, each with its own type, albums and
display settings. Pick the source in `.env` or the web UI's settings, and add
frames on the dashboard or with `cozysync frames add`. It runs as a Docker
container, for example on a NAS.

See [PLAN.md](PLAN.md) for the plan, [docs/decisions.md](docs/decisions.md) for why
it looks like this, [docs/hosted-auth.md](docs/hosted-auth.md) for the planned
hosted Google sign-in, [docs/cozyla-frame.md](docs/cozyla-frame.md) for Cozyla
discovery and security, and [docs/roadmap.md](docs/roadmap.md) for where it's going
(including Home Assistant).

**Status:** the Ambient API returns 403 for projects outside Google's partner
program, so the Google setup below only works for an accepted project. A second
source is next.

## 1. Google Cloud setup (one time, `google_ambient` source only)

1. Go to <https://console.cloud.google.com/> and create a project, e.g. `cozysync`.
2. **APIs & Services → Library**: search for **Photos Ambient API** and click **Enable**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External**, app name `cozysync`, with your email as support and
     developer contact.
   - **Data access / Scopes**: add `https://www.googleapis.com/auth/photosambient.mediaitems`.
   - **Audience / Test users**: add your own Google account.
   - The project must be accepted into the
     [Google Photos partner program](https://developers.google.com/photos/partner-program/overview);
     otherwise registering the frame fails with 403 `PERMISSION_DENIED`.
4. **Credentials → Create credentials → OAuth client ID**, application type
   **TVs and Limited Input devices**. Copy the client ID and secret.

## 2. Start it on the NAS

```sh
cp .env.example .env   # set WEB_PASSWORD; everything else can be done in the UI
docker compose up -d --build
```

Open `http://<nas-ip>:1992`. Log in with any username and your `WEB_PASSWORD`.

1. **Settings**: pick the source and, for Google, paste the OAuth client ID and secret.
2. **Connect Google Photos**: the dashboard shows a code and a QR code. Sign in
   on your phone with the test-user account and pick the albums for the frame.
   The page updates by itself as each step finishes.
3. **Listing test**: lists every album in full and saves a snapshot to `data/`.
   Set "Download samples" above 0 to fetch a few files sized for the frame. Run it
   again after adding or removing a photo; the dashboard shows the changes between
   the last two snapshots.
4. **Frames → Add a frame** for each screen: its type, the albums it shows (none
   ticked means all) and, for slideshows, its display settings. **Check**
   confirms the worker can reach it. For `cozyla_adb`, a Cozyla connects without
   asking: its adb has no authentication and runs as root, so keep it on an
   isolated network ([docs/cozyla-frame.md](docs/cozyla-frame.md)). Other Android
   frames show an "Allow USB debugging?" prompt the first time; tick "Always
   allow" and check again (the key is kept in `data/.android/`). For `folder`,
   mount the folder in `docker-compose.yml`. For `web`, the dashboard shows the
   frame's slideshow link and a QR code.

### Browser slideshow (`web` frames)

Open a slideshow frame's link from the dashboard on any screen with a browser. It plays
full screen, fades between photos, picks up new photos and setting changes within
a minute, and plays videos muted.

- **Display settings**: seconds per photo, one photo or two, orientation,
  shuffle, and what fills the space around a photo (a blurred copy, or black). Defaults are in Settings → Display; each frame can override them on
  its page, and each screen again in its link.
- **Two photos:** when a photo's orientation doesn't match the screen (portrait
  photos on a landscape screen), two of them share it, side by side or stacked.
- **Orientation:** "Follow the screen" uses what the browser reports. If a screen
  is mounted sideways and its browser doesn't rotate, pick the orientation and the
  slideshow rotates itself.
- **Swipe** left or right (or press the arrow keys) for the next or previous photo.
- **Per-screen overrides:** add them to the frame's link, e.g.
  `?interval=10&layout=pair&orientation=portrait&shuffle=no&fill=black`.
- The link contains a secret instead of the UI password, because kiosk browsers
  can't answer a password prompt. Only give it to your screens.

### Cozyla frames as kiosks

On a Cozyla frame's page, **Set up as a cozysync kiosk** replaces the Cozyla app
with the cozysync slideshow, in about a minute:

1. Installs [Webview Kiosk](https://f-droid.org/en/packages/uk.nktnet.webviewkiosk/)
   (open source, from F-Droid, checked against a pinned checksum).
2. Points it at the frame's slideshow link, hides its toolbars, and locks its
   settings with a generated password (shown on the frame's page). On the frame,
   tap the top-left quarter of the screen 10 times to open its settings.
3. Turns off the Cozyla app, so the kiosk is the home screen, also after a reboot.

cozysync records how the frame was first. **Restore the Cozyla app** puts it back
exactly: the Cozyla app on and the kiosk app removed. The Cozyla app keeps its
photos while it's off. A frame set up as a kiosk can't be removed from cozysync
until it's restored. The link uses Settings → "Address frames use to reach this
server" (`PUBLIC_URL`), or the address you opened the web UI with.

`cozysync frame-put FRAME photo.jpg …` copies files to a frame by hand,
which is handy for trying the slideshow before the sync engine exists.

### CLI

The same steps are available on the command line, which is handy when testing
from a laptop:

```sh
docker compose run --rm cozysync login         # google_ambient: sign in, pick albums
docker compose run --rm cozysync device        # google_ambient: device, albums, token age
docker compose run --rm cozysync list --download 3
docker compose run --rm cozysync diff          # compare the last two snapshots
docker compose run --rm cozysync frames                          # list frames
docker compose run --rm cozysync frames add "Kitchen" --type web
docker compose run --rm cozysync frames add "Living room" --type cozyla_adb --address 192.168.1.50:5555
docker compose run --rm cozysync frames show Kitchen             # details and slideshow link
docker compose run --rm cozysync frames edit Kitchen --interval 10 --fill black --album ID
docker compose run --rm cozysync frames edit Kitchen --interval ""   # back to the default
docker compose run --rm cozysync settings                        # list settings
docker compose run --rm cozysync settings set public_url http://192.168.1.10:1992
docker compose run --rm cozysync settings unset display_layout   # back to .env or the default
docker compose run --rm cozysync signout [--forget-device]       # google_ambient
docker compose run --rm cozysync frame-check                     # all frames, or name one
docker compose run --rm cozysync frame-put Kitchen /data/samples/*.jpg
docker compose run --rm cozysync kiosk setup "Living room" --url http://192.168.1.10:1992
docker compose run --rm cozysync kiosk show "Living room"       # settings password
docker compose run --rm cozysync kiosk restore "Living room"    # back to the Cozyla app
```

Running locally without Docker (Python 3.9+):

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
DATA_DIR=./data .venv/bin/cozysync web --port 1992
```

## Notes

- Photos are pass-through: the sync streams each one to the frame and keeps
  only state on the NAS. `data/samples/` is only for the listing test.
- `data/` holds OAuth tokens and the OAuth client secret. It's git-ignored and
  written with `0600` permissions.
- The web UI is for the LAN only; don't expose port 1992 to the internet. Set
  `WEB_PASSWORD`, because without it anyone on your network can use the UI.
  Cross-site form posts are rejected.
- Adding a source: implement `Source` from `src/cozysync/sources/base.py` and
  register it in `sources/__init__.py`. Adding a frame: implement `Frame` from
  `src/cozysync/frames/base.py` and register it in `frames/__init__.py`.
- The Ambient API allows 240 `mediaItems.list` calls per device per day, with up
  to 100 items per call.
- While the OAuth app is in **Testing** status, Google's refresh tokens expire
  after 7 days. `cozysync device` prints the token's age so we can confirm this.
