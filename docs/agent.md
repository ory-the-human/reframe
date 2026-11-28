# The frame agent

A small Android app of ours that runs on each kiosk frame, so the frame looks
after itself. adb is only for setting the frame up and for recovery: the frames'
adb hangs now and then until the frame is power-cycled.

**Status:** the server side is done (settings, the two endpoints below, the health
pages and CLI). The app (`agent/`) runs the screen schedule, dark-room sleep, the
clock, the watchdog and health reports, and kiosk setup installs it.

## Building and releasing

`agent/build.sh` builds the APK without Gradle (JDK 17, Android SDK platform 34 and
build-tools 34.0.0). The server downloads the release pinned in
`src/reframe/frames/kiosk.py` when it sets up a kiosk, like it does Webview Kiosk. To
try a local build, set `AGENT_APK` to the APK's path.

To release:

1. Bump `versionName` and `versionCode` in `agent/AndroidManifest.xml` and merge.
2. Push the tag `agent-v<versionName>` from main. The `Agent release` workflow checks
   the tag matches `versionName`, builds and signs the APK, publishes the GitHub
   release with `reframe-agent-<version>.apk` and opens the PR "Pin agent release
   <version>", which updates the three `AGENT_*` pins in `kiosk.py`.
3. Review and merge that PR. That is what makes frames update.

Fallback without CI: `agent/release.sh` builds the release and writes the pins into
`kiosk.py`; upload the APK to a release tagged `agent-v<version>` under the same name,
then commit `kiosk.py`.

Sign every release with the same key (`private/agent.keystore`, git-ignored, or
`$AGENT_KEYSTORE`): Android only installs an update signed like the installed app.
Keep a backup of it. CI gets it from the repository secrets `AGENT_KEYSTORE_B64`
(base64 of the keystore) and `AGENT_KEYSTORE_PASSWORD`. `build.sh` only creates a key
when `$AGENT_KEYSTORE` is unset, so a missing key in CI fails the build.

## What it does

- **Screen:** turns the screen off and on following the frame's schedule and the
  room light (below), on the frame itself, also while the server is off.
- **Watchdog:** checks every 30 s, on its own thread, and puts back:
  - the packages in `keep_disabled` (the Cozyla app), if they're turned on;
  - the agent and the kiosk on the firmware's keep-alive list;
  - the kiosk's settings file, when a value differs from `kiosk.settings` for
    2 minutes (setup rewrites it too). It stops the kiosk, rewrites only those
    values (the password and anything else stay) and opens it again;
  - the kiosk in front, when another app has had the screen for 5 minutes while
    it's on. Going back to the kiosk sooner ends the wait;
  - adb, by restarting adbd when it's stuck (below);
  - adb closed: 2 minutes after power-on, once nothing is connected, it turns
    network adb off (`setprop service.adb.tcp.port -1` and restarts adbd).
    The frame's `/init.sun8i.rc` sets the port to 5555 at every boot, so
    power-cycling opens it again for the next 2 minutes, whatever the agent does;
    it stays open while someone is connected. The server disconnects after each
    adb job so it doesn't hold it open. Reports say whether adb is on, in use or
    off.

  Each fix goes into the next report. Tested by breaking each one on the frame.
- **Clock:** sets the frame's clock from the server's, since nothing else does on
  a kiosk frame.
- **Health:** reports temperatures, CPU load, memory, storage, uptime and the
  screen's state once a minute.
- **Local slideshow:** keeps a copy of the frame's photos and plays the slideshow
  from the frame itself, so it keeps going while the server is off (below).
- **Settings on the frame:** a small panel on the slideshow for the interval, sound
  and Wi-Fi (below).
- **Screenshots:** sends a picture of the screen when asked from the frame's page (below).
- **Reboot:** reboots the frame when asked from the frame's page (below).

It runs as a foreground service. The firmware drops `BOOT_COMPLETED` for apps it
doesn't list, so the agent also starts when Wi-Fi connects (about 15 s after boot)
and after it's updated. Kiosk setup installs it, adds it and the kiosk to the
firmware's keep-alive list (below), sets the clock and starts it; restoring removes
it and puts the list back.

## Talking to the server

The agent calls the server; the server never calls the frame. Every request
carries the frame's agent key: `Authorization: Bearer <key>`. The key is written
onto the frame at setup (`reframe agent FRAME` shows it, and the config, for
testing by hand). A wrong or missing key gets `401`.

### `GET /agent/v1/config`

```json
{
  "api": 1,
  "frame": {"id": "f-369523", "name": "Living room"},
  "screen": {
    "schedule": {"off": "23:00", "on": "07:00"},
    "dark": true, "dark_lux": 5, "dark_seconds": 30
  },
  "report_seconds": 60,
  "kiosk": {
    "package": "uk.nktnet.webviewkiosk",
    "home_url": "http://192.168.1.10:1992/show/…",
    "settings": {"web_content.home_url": "…", "device.keep_screen_on": true}
  },
  "keep_disabled": ["com.cozyla.frame"],
  "version": "e92e139b02e6"
}
```

- `schedule` is `null` for always on. Times are the frame's local time; an `off`
  later than `on` spans midnight.
- `dark`: turn the screen off after the room has been below `dark_lux` for
  `dark_seconds`, and back on when it's lit again. Outside the schedule's hours the
  screen stays off whatever the light. A touch turns it on for 5 minutes at any time.
- `kiosk.settings` are the kiosk's `shared_prefs/user_settings.xml` keys and values
  (without its password); `kiosk` is `null` when the frame isn't a kiosk.
- The agent keeps the last config it got and uses it while the server is down.

### `POST /agent/v1/report`

Once every `report_seconds`, as JSON, at most 64 KB. Every field is optional.

```json
{
  "agent_version": "0.1.0",
  "config_version": "e92e139b02e6",
  "uptime_seconds": 86400,
  "time": "2026-11-24T21:03:00-05:00",
  "timezone": "America/New_York",
  "temperatures": {"cpu": 52.1},
  "load": [0.42, 0.35, 0.30],
  "cpus": 4,
  "memory": {"total_kb": 1015808, "available_kb": 420000},
  "storage": {"total_kb": 5200000, "free_kb": 3100000},
  "screen": {"on": true, "why": "schedule", "lux": 40, "brightness": 180},
  "foreground": "uk.nktnet.webviewkiosk",
  "fixes": [{"at": 1795000000, "what": "Turned the Cozyla app off again"}],
  "local_port": 1992
}
```

- `temperatures` in °C, by sensor name. `screen.why`: what decided the screen's
  state (`schedule`, `dark`, `touch`, `on`). `fixes` are only those since the last
  report; `at` is a Unix time.
- `local_port`: sent once the agent can serve the slideshow itself (below).
- The reply is `{"ok": true, "config_version": "…"}`. When that differs from the
  agent's, it fetches the config again, so settings changes arrive within a minute.
  It has `"screenshot": true` when a screenshot was asked for, and `"reboot": true`
  when a reboot was (below).

The server keeps the latest report, 12 hours of history (temperature, load, memory,
screen) and the last 50 fixes per frame in `/data/agents/<frame id>.json`. A frame
counts as offline after 3 minutes without a report.

## Local slideshow

From agent 0.3.0 the kiosk plays the slideshow from the agent on
`http://127.0.0.1:1992/show/<key>`, with the server's URL layout, so the page is the
same `slideshow.html`. Photos and videos keep playing while the server is off, with
the last display settings and dates.

- **Copy:** once a minute, on its own thread, the agent fetches
  `GET /agent/v1/slideshow`: the playlist `/show/<key>/playlist` gives
  (`display`, `items` with `name`, `type`, `taken`) plus the rendered page as `page`.
  It saves that as `files/slideshow.json`, deletes files no longer listed and downloads
  missing ones from `GET /agent/v1/media/<name>`, each to `<name>.part` and then renamed,
  so a half file is never shown. Files are in `/data/data/app.reframe.agent/files/media/`.
  A failed download ends the round; the next one carries on.
- **Serving:** a small HTTP server on 127.0.0.1 only: `/show/<key>` (the page),
  `/show/<key>/playlist` (only files already on the frame, so new photos appear as
  they arrive) and `/show/<key>/media/<name>`, with byte ranges (`206`), which the
  WebView's video needs. The key isn't checked: only apps on the frame can connect.
- **Switch-over:** once the agent has a copy, its reports carry `"local_port": 1992`.
  The server then gives `http://127.0.0.1:1992/show/<key>` as the kiosk's home URL in
  the config, and the watchdog rewrites the kiosk's settings (2 minutes later) and
  reopens it. Agents that don't send `local_port` keep the server's URL. The frame's
  page shows which one the kiosk uses. "Update kiosk settings" writes the server's URL;
  the watchdog moves it back 2 minutes later.
- **Page updates:** the playlist carries `page_version`, a hash of `slideshow.html`.
  An open page whose version differs reloads at its next photo (once per version), so
  a new page reaches frames and browsers within a minute or two, without a reboot.
  Agents before 0.4.0 don't pass it on, and pages from before it don't check it.
- **Screen off:** the page asks the agent every 2 s (`GET /show/<key>/screen`,
  `{"on": false}`) and pauses while the screen is off. A playing video stops and a
  photo takes its place, so no sound plays in the dark or at once when the screen
  comes back on; the slideshow carries on from that photo. From agent 0.4.1; older
  agents answer 404 and the page keeps going as before.

## Settings on the frame

From agent 0.4.0, tapping the slideshow shows a gear for 15 s; it opens a panel with
seconds per photo (− / + in steps of 5, from 5 to 300), videos' sound on or off, the
volume, and **Wi-Fi**. Anyone at the frame can use it. It closes after 45 s
without a touch. The gear only shows when the agent serves the page (its playlist has
`"agent": true`), not in a browser or on older agents.

- **Applying:** the page posts the change to the agent (`POST /show/<key>/settings`,
  e.g. `{"interval": 20}`). The agent puts it in its copy of the display settings, so
  the page and the playlist have it at once, and sets Android's media volume
  (`AudioManager`, `STREAM_MUSIC`) when sound is on. With sound off the page mutes
  videos and the volume is left alone. Webview Kiosk plays sound without a tap since
  setup sets `web_engine.media_playback_requires_user_gesture` to false.
- **To the server:** unsent changes are kept in the agent's prefs. At the start of each
  copy round (once a minute) the agent sends them to `POST /agent/v1/display`
  (`{"interval": 20, "sound": true, "volume": 60}`, any of them), which saves them as
  the frame's `display_interval`, `display_sound` and `display_volume`, then fetches
  the slideshow with them. While the server is off they stay on the frame and are
  sent when it's back. The frame's page shows them as Seconds per photo, Sound and
  Volume; the agent applies a volume set there within a minute.
- **Wi-Fi:** the panel lists the nearby networks (`GET /show/<key>/wifi`, refreshed
  every 5 s while open), strongest first, marking the connected and saved ones. Tapping
  one asks for its password (left empty, a saved network keeps its own) and joins it
  (`POST /show/<key>/wifi`, `{"ssid": …, "password": …}`); open networks join at
  once. It stays in the panel, and has no switch to turn Wi-Fi off. WPA and open
  networks only. Android 6 shows scan results only to apps with location, so the agent
  grants itself `ACCESS_COARSE_LOCATION` as root.
- **Who can call it:** like the rest of the local server, only apps on the frame, which
  can all be root anyway. The most one could do is change those three settings for this
  frame, or move it to another nearby network.

## Screenshots

"Take a screenshot" on the frame's page flags the frame; the reply to its next report
has `"screenshot": true` (once). The agent runs `screencap -p` as root and sends the
PNG to `POST /agent/v1/screenshot` (`image/png`, at most 8 MB). The server keeps the
latest in `/data/agents/<frame id>-screenshot.png` and shows it on the frame's page
with its time. It arrives within a minute; a screen that's off comes out black.

## Reboot

"Reboot" on the frame's page flags the frame; the reply to its next report has
`"reboot": true` (once), and the agent runs `reboot` as root. The frame restarts
within a minute and is back in about 30 s. Like any boot, it opens adb for 2 minutes,
which is a way to get adb back for setup or restore. The agent never reboots on its own.

## Updates

The agent updates itself from the server, without adb or internet. The config
names the APK frames should run:

```json
"agent": {"version": "0.3.0", "sha256": "…", "url": "/agent/v1/apk"}
```

That's the release pinned in `kiosk.py`, or the `AGENT_APK` build (version
`"local"`). When its sha256 differs from the agent's own installed APK, the agent
downloads it from `GET /agent/v1/apk` (same key; the server fetches the pinned
release once into `/data/cache`), checks the sha256 and installs it as root with
`pm install -r -d`. Android then restarts the agent. A failed version is tried
again after an hour at the earliest. Until it works, the report's `update_error`
(`"Couldn't update to 0.3.0: …"`) is shown on the frame's page. To roll back, pin
the previous release.

- 0.2.0 frames can't update themselves: they get the first self-updating version
  through kiosk setup.
- Android only installs an update signed with the same key, so sign every release
  with `private/agent.keystore`. A build signed with another key fails with
  `INSTALL_FAILED_UPDATE_INCOMPATIBLE`.

## What the frame offers (checked 2026-11-24)

- **Root for apps:** the frame's `/system/xbin/su` gives root to any app's user id,
  not only to adb (tested as the kiosk app's uid). The agent uses it for the
  watchdog. It's also a security finding: any app on the frame can become root.
- **Screen:** `input keyevent 26` puts it to sleep and `input keyevent 224` wakes
  it; the kiosk is back in front after waking. There's no backlight file in `/sys`.
  Brightness is Android's own setting (auto-brightness is on).
- **Light sensor:** `pt3r850`, in lux, about one reading a second (122 lux in a lit
  room). Android stops listening to it when the screen is off, but it keeps
  reporting to the agent (which holds a wake lock), so a lit room can wake the frame. It reads 0–200 lux, so all bright rooms look
  the same. Tested: covered, the screen went off after `dark_seconds`; uncovered
  (without touching), it came back about 3 seconds after the light did.
- **Touch:** a tap wakes a screen that's fully off (Android's own wake). The agent
  takes any wake it didn't cause as a touch and keeps the screen on for 5 minutes.
- **Auto-brightness:** Android's adaptive brightness is on and dims the screen as
  soon as the room darkens, before the agent turns it off.
- **Temperatures:** `/sys/class/thermal/thermal_zone0` (`sunxi-therm`, the chip, in
  °C, 47–50 idle) and an ambient temperature sensor (`AW`).
- **Load and memory:** 2 cores, 1 GB RAM. `/proc/meminfo` has no `MemAvailable`, so
  available memory is `MemFree + Cached + Buffers`. `/data` is 25.7 GB.
- **Clock:** after a reboot the frame's clock was at 1969: network time is off
  (`auto_time` 0) and the Cozyla app, which is off on a kiosk, seems to have been
  setting it. The agent sets the clock from the server's time (the `Date` header of
  its replies), so schedules work without internet. The timezone
  (`persist.sys.timezone`) survives reboots.
- **Background manager:** Allwinner's `BackgroundManagerService` force-stops any
  app that isn't on its allow list about 2 minutes after the screen goes off, and
  refuses to start a service for an app that hasn't opened an activity. Its list
  of allowed apps is the system setting `kill_background_services_list`
  (comma-separated, read live; the setting `kill_background_services` turns the
  manager on, read at boot). Setup adds the agent and the kiosk to it.
- **adb hangs:** adbd serves one connection at a time. Any other connection is
  accepted and then left waiting, its first message unread, even from a real adb
  client. So when the connection it serves dies without closing (Wi-Fi drops, the
  host sleeps), every new one waits forever and adb says "device offline" until a
  power cycle. The watchdog reads `/proc/net/tcp`: a connection to the adb port
  still waiting with the same unread bytes 30 s later is stuck, and it restarts
  adbd (`stop adbd; start adbd`); clients reconnect on their own. With no
  connection at all it sends adb's opening message (`CNXN`) itself and restarts
  adbd after two unanswered tries. Tested with a held, unanswered connection: adbd
  restarted within a minute and the host's adb reconnected. A side effect: two
  hosts using one frame's adb at once take turns, since the second one's wait
  ends in a restart.
- **Shell tools:** the frame's `grep` crashes as it exits and its `sed` mangles the
  lines it edits, so the agent filters and edits in Java.
