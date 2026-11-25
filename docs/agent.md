# The frame agent

A small Android app of ours that runs on each kiosk frame, so the frame looks
after itself. adb is only for setting the frame up and for recovery: the frames'
adb hangs now and then until the frame is power-cycled.

**Status:** the server side is done (settings, the two endpoints below, the health
pages and CLI). The app (`agent/`) runs the screen schedule, dark-room sleep, the
clock and health reports, and kiosk setup installs it. The watchdog is next.

## Building and releasing

`agent/build.sh` builds the APK without Gradle (JDK 17, Android SDK platform 34 and
build-tools 34.0.0). `agent/release.sh` builds a release and prints the version,
URL placeholder and checksum to pin in `src/cozysync/frames/kiosk.py`; the server
downloads that release when it sets up a kiosk, like it does Webview Kiosk. Until a
release is pinned, or to try a local build, set `AGENT_APK` to the APK's path.

Sign every release with the same key (`private/agent.keystore`, git-ignored, or
`$AGENT_KEYSTORE`): Android only installs an update signed like the installed app.
Keep a backup of it.

## What it does

- **Screen:** turns the screen off and on following the frame's schedule and the
  room light (below), on the frame itself, also while the server is off.
- **Watchdog:** keeps the kiosk app in front (restarts it if it crashes or something
  else takes the screen), keeps the packages in `keep_disabled` turned off, keeps
  the kiosk's settings file matching `kiosk.settings`, and restarts adb when it
  stops answering. Each fix goes into the next report.
- **Clock:** sets the frame's clock from the server's, since nothing else does on
  a kiosk frame.
- **Health:** reports temperatures, CPU load, memory, storage, uptime and the
  screen's state once a minute.

It runs as a foreground service. The firmware drops `BOOT_COMPLETED` for apps it
doesn't list, so the agent also starts when Wi-Fi connects (about 15 s after boot)
and after it's updated. Kiosk setup installs it, adds it and the kiosk to the
firmware's keep-alive list (below), sets the clock and starts it; restoring removes
it and puts the list back.

## Talking to the server

The agent calls the server; the server never calls the frame. Every request
carries the frame's agent key: `Authorization: Bearer <key>`. The key is written
onto the frame at setup (`cozysync agent FRAME` shows it, and the config, for
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
  "fixes": [{"at": 1795000000, "what": "Turned the Cozyla app off again"}]
}
```

- `temperatures` in °C, by sensor name. `screen.why`: what decided the screen's
  state (`schedule`, `dark`, `touch`, `on`). `fixes` are only those since the last
  report; `at` is a Unix time.
- The reply is `{"ok": true, "config_version": "…"}`. When that differs from the
  agent's, it fetches the config again, so settings changes arrive within a minute.

The server keeps the latest report, 12 hours of history (temperature, load, memory,
screen) and the last 50 fixes per frame in `/data/agents/<frame id>.json`. A frame
counts as offline after 3 minutes without a report.

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
