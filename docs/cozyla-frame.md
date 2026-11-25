# The Cozyla frame: discovery and security

What we found on two Cozyla frames on 2026-11-24, how to find them on a
network, and what it means for anyone running reframe with a Cozyla.

## The frames we checked

Two Cozyla CD-3X341F frames on a home network, with identical firmware and apps.
Their addresses come from DHCP and changed over time, so give each frame a fixed
lease in the router (or, later, let reframe find them: see below). The owner's
own addresses and serials are kept out of Git, in `private/` (git-ignored).

## Finding frames on a network

The frames don't advertise adb over mDNS (`_adb-tls-connect._tcp` shows
nothing). What works:

1. Ping the subnet so the ARP table fills, then probe TCP 5555 on every host.
   A quick parallel sweep without pinging first missed both frames.
2. `adb connect <ip>:5555`, then `adb -s <ip>:5555 shell getprop`: the
   model is `Cozyla_CD_3X341F`, the product `astar_ococci`, and `ro.serialno`
   identifies the frame.
3. The frames we checked share the MAC prefix `40:9c:a7`, which narrows the probe.

The roadmap item "Discover frames on the network" builds on this: identify each
frame by serial so a changed address is found again automatically.

## Firmware

| Property | Value |
|---|---|
| Android | 6.0.1 (`MOB30R`), security patch 2016-07-05 |
| Build | `Cozyla/astar_ococci/astar-ococci:6.0.1/MOB30R/20231220:user/test-keys` |
| Board | `exdroid` (Allwinner) |
| Signing | test-keys |
| SELinux | Permissive |
| adb | `ro.adb.secure=0`, `service.adb.tcp.port=5555`, on after every boot |
| Root | adb shell runs as `uid=0(root)`; `/system/xbin/su` present |
| Vendor apps | `com.cozyla.frame` 1.0.0.2023121918, `com.joyhong.test` 1.0.0.2025012014 (factory test app from the manufacturer) |
| Other apps | AOSP only, no user-installed apps; `com.google.android.webview` |
| Tools on the device | `iptables`; no `awk` |
| Hardware | no `/dev/video*` (no camera found, though `dumpsys media.camera` reports 2); an `audiocodec` sound card (whether it has a mic wasn't checked) |

## Network exposure

- Listening to the network: only TCP 5555 (adb). adb's own 5037 is on
  localhost; UDP is only the DHCP client.
- Outgoing: each frame keeps one HTTPS connection to AWS (us-east-1),
  probably the Cozyla cloud.

## Security assessment

1. **Unauthenticated root over Wi-Fi.** adb doesn't ask for authorization
   (`ro.adb.secure=0`) and its shell is root. Any device on the same network,
   including a compromised one, can take full control without anything showing
   on screen.
2. **No updates since 2016.** Nine years of Android, kernel and WebView
   vulnerabilities, on test-keys firmware with SELinux permissive. Content shown
   in a browser on the frame (such as the `web` slideshow) runs on that stack.
3. **The Wi-Fi password is readable.** `/data/misc/wifi/wpa_supplicant.conf`
   holds it in plain text, and root can read it.
4. **Any app can become root.** `/system/xbin/su` gives root to app user ids
   too, not only to adb, so any installed app can take over the frame.
5. **A place for an attacker to stay.** An always-on, never-updated root device is
   a good place for malware from another device to persist.

Fixing this on the frame (turning on adb auth) means changing `/default.prop`
in the boot image, which risks bricking it. We don't.

## Recommendations

1. **Isolate the frames** on an IoT network or VLAN, with a firewall rule that
   lets only the reframe host reach port 5555. This removes most of the risk;
   reframe needs nothing else.
2. **Try blocking their internet.** If reframe pushes the photos, the frames may
   not need the Cozyla cloud; check the clock (NTP) and the app still work.
3. **A firewall rule on the frame** as a backup: as root, reframe can add an
   `iptables` rule letting only its own address reach 5555, on every connect.
   It's lost on reboot, so it complements 1 and doesn't replace it.
4. Treat a working adb connection as proof of nothing: anyone can connect.

## How the Cozyla app shows photos (tested 2026-11-24)

From the decompiled app (`com.cozyla.frame` 1.0.0.2025012014) and two tests on a
frame:

- The library is the Room DB `databases/CzlItemDataBase.db`, table `ItemEntry`.
  Only rows whose `album_id` is an active channel play. Channels come from
  Cozyla's cloud (`channel_data` in `shared_prefs/cozyla.xml`): Google album
  (`photo`), Talking Photo (`story`), Shared album (`FamilyShare`), Art gallery
  (`gallery`), clock, recommendation.
- Pictures are displayed from the app's disk cache (`cache/<key>.0` plus a
  `journal`), never from `media_path_local_*`. The key is the lowercased
  `google_media_id` for `photo` rows and `media_id` otherwise (when it matches
  `[a-z0-9_-]{1,120}`, else a SHA-256). On a cache miss, `photo` rows are
  fetched from Google by id; other types download `media_path_web_*`.
- Test 1: a `photo` row with a local path and no Google id in the Google album.
  The app announced a new photo but showed a black screen (nothing to fetch).
  The Google sync left the row alone.
- Test 2: a `FamilyShare` row in the Shared album with a web URL on the LAN. The
  next shared-album sync deleted it before it was downloaded: channel syncs
  reconcile rows against Cozyla's cloud.
- Test 3: a copy of a real phone-added `localPhoto` row with a LAN URL. Gone
  within 3 seconds of the app starting; the only syncs run by then were the
  Shared album's `SyncTotalLocalWorker` and the incremental sync. The image was
  never requested.
- Test 4: the same row with the frame's internet blocked (an `iptables` OUTPUT
  rule allowing only the LAN). The album syncs never ran and the row stayed.
  Opening it ran `DownloadMediaWorker`, which fetched the LAN URL into
  `cache/<media_id>.0` but showed black: the row still had `down_status` 0.
  With `down_status` 1 (after a restart) the photo **displayed**, alongside
  the cloud photos already cached.
- Editing the DB: the app is the home screen, so `am force-stop` restarts it
  at once. Replacing the DB under the running app made SQLite report corruption
  and the app **deleted the database**. Safe method: `kill -STOP` the app,
  copy the DB (with `-wal`/`-shm`), edit and checkpoint, put it back without
  `-wal`/`-shm`, then `kill -9` the app so it restarts on the new file.

- A photo added to the Shared album from the Cozyla phone app goes through
  Cozyla's cloud: the phone uploads it to their S3 bucket
  (`album-s3-device.prod.mycozyla.com/local/<uuid>/vertical/<file>.jpg`), the
  cloud updates the frame's AWS IoT shadow over MQTT, and the frame runs
  `IncrementSyncDeviceMediaWorker`. That adds a `localPhoto` row (web paths to
  S3, a local path that is never written, `down_status` 1, location and sender
  name filled in) and downloads the file straight into `cache/<media_id>.0`.

Conclusion: online, rows we add survive only in the Google album, where they
can't load, and rows in cloud-synced channels are removed. With the frame's
internet blocked, a `localPhoto` row in the Shared album with a LAN URL,
`down_status` 1 and the file in the cache plays in the Cozyla app. Long-term
offline behaviour (clock, retries) is untested.

## Video on the frame (tested 2026-11-24)

The Allwinner A33 decodes H.264 in hardware, up to about 1080p. Phone videos
need converting before they reach the frame:

- **HEVC (H.265) and 4K** don't play. Re-encode to H.264 (Main), 8-bit
  `yuv420p`, AAC audio, `+faststart`.
- **Width and height must be multiples of 32.** At 720×1280 the decoder pads to
  736 and the browser shows the padding as a green bar (on the side for portrait
  video, at the bottom for landscape). 576×1024 and 1024×576 are clean.
- **HDR (10-bit HLG, BT.2020) tags** make the video silently fail in the
  browser. Convert to 8-bit and tag BT.709; tone-mapping (`zscale` +
  `tonemap`) would give truer colours.

Tested with ffmpeg:
`-vf "scale='if(gt(iw,ih),1024,576)':'if(gt(iw,ih),576,1024)',format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709" -c:v libx264 -profile:v main -crf 23 -c:a aac -movflags +faststart`
(this scale fixes 16:9; other aspect ratios need rounding each side to 32).

## Running a kiosk app instead (tested 2026-11-24)

- Disabling the Cozyla app (`pm disable com.cozyla.frame`, as root) makes another
  home-screen app the default, and survives reboots once Android has written
  `/data/system/users/0/package-restrictions.xml` (about 10 s later). A reboot
  before that brings the Cozyla app back.
- With two home-screen apps enabled, pressing home shows Android's "Select a Home
  app" chooser.
- The Cozyla app's light-sensor sleep is its own: it puts the screen to sleep and
  stops itself, so with nothing else watching the sensor only a touch wakes the
  frame.
- Webview Kiosk 0.26.28 runs here (`Android6KioskActivity` as home). Its settings
  are `shared_prefs/user_settings.xml`, keyed like its managed-configuration schema
  (`res/xml/app_restrictions.xml`); writing the file while the app is stopped works.
- Fully Kiosk Browser 1.61.4 also runs, but remote admin, kiosk mode and
  screen-off-in-darkness are paid (watermark without a licence).
- The manufacturer's factory test app `com.joyhong.test` was disabled after one
  reboot and enabled after another; nothing we did touched it.
