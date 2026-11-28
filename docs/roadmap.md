# Roadmap

Where reframe could go, and the decisions each path needs. Settled decisions move
to [decisions.md](decisions.md); the step-by-step plan for the current work is in
[PLAN.md](../PLAN.md).

Status: **now** (in progress or next), **next** (after now), **later** (wanted, not
scheduled), **idea** (worth recording, undecided).

## Get the owner's frame working

| Item | Status | Notes |
|---|---|---|
| Second source: Google Photos share link | done | `share_link`, the default: shared albums added by their links in Settings → Albums or with `reframe albums`, kept in `/data/albums.json`. The link is a secret and Google can change the page, so anything unexpected makes a listing incomplete and nothing is removed. Immich stays an option for later. |
| Local albums | done | Albums kept on the server in `/data/albums/<id>/`, created in Settings → Albums and filled by uploading on their page (or copying files into the folder), with any source. Photos are fitted and written as JPEG with Pillow; a folder listing is trusted, so deleted files leave frames at the next sync. |
| Sync engine (pass-through, safe removal) | done | PLAN.md phase 2. Uses only the `Source` and `Frame` interfaces. The web server runs it every `SYNC_MINUTES`; Sync now and the last run's results are on the dashboard and each frame's page, and `reframe sync [--dry-run]` runs it by hand. |
| Remove `reframe frame-put` | done | A testing tool from before the sync engine, which calls the same `Frame.put` itself. `frame-check` stays. |
| Show synced photos in the Cozyla app | idea | Works only with the frame kept offline ([cozyla-frame.md](cozyla-frame.md)); the kiosk below replaced it. |
| Cozyla frames as kiosks | done | One-click setup and exact restore from the frame's page ([decisions.md](decisions.md), entry 7). |
| Store kiosk photos on the frame | done | The agent keeps a copy of the page and files and serves the slideshow on 127.0.0.1, so it keeps playing with the server off; also a screenshot button ([agent.md](agent.md#local-slideshow), agent 0.3.0). |
| On-frame agent | done | Our own small Android app on the frame, so it manages itself and adb is only for setup and recovery. It calls the server, never the other way round, and keeps working with the server off. Screen schedule and light-sensor sleep, clock, a watchdog that keeps the kiosk in front and its settings as written, adb closed 2 minutes after power-on, health reports, the local slideshow, screenshot and reboot buttons, and self-update. Releases are built by a GitHub Action from `agent-v*` tags (0.3.0) ([agent.md](agent.md)). |
| Settings on the frame | done | Tap the slideshow, then the gear: seconds per photo, videos' sound and volume, and joining a nearby Wi-Fi network. Saved on the server as the frame's settings, also when it was off at the time ([agent.md](agent.md#settings-on-the-frame), agent 0.4.0). |
| Frame health in the admin app | done | Temperatures, CPU load, memory, storage, uptime, adb and the watchdog's last fixes per frame, on its page, the dashboard and `reframe health`. |
| Video conversion in the sync engine | done | New videos are re-encoded with ffmpeg: H.264 8-bit, fitted to the frame's `max_px` and cropped to sides a multiple of 32 ([cozyla-frame.md](cozyla-frame.md#video-on-the-frame-tested-2026-11-24)). `convert_videos`, default on, per frame. Kiosk frames now download at 1280. No tone mapping. |
| Video conversion extras | later | Cap at 30 fps if the frame stutters at 60; HDR tone mapping (`zscale` + `tonemap`); stop re-downloading files ffmpeg can't read (now retried every sync). Share links don't need these: Google's copy is SDR and at most 30 fps. |
| MQTT for frames | later | When a broker is configured, every change reaches frames over MQTT at once: config, display settings, page and agent updates, screenshot and reboot requests, reports back. Without one, the polling we have now stays. Webview Kiosk speaks MQTT too (with Home Assistant discovery), so it could reload or navigate without the agent. |
| Unattended on the NAS | done | Published image (`v*` tags, amd64 and arm64) and a NixOS module ([deploy/nixos/reframe.nix](../deploy/nixos/reframe.nix)); the server syncs every `SYNC_MINUTES`. |
| Alerts | later | Sync failures, an album link that stops working, a frame that stops reporting, runs hot or fills up. Leaning ntfy (one URL setting); email the alternative. |
| Several frames per worker | done | Each with its own type, albums and display settings ([decisions.md](decisions.md), entry 5). |
| Browser slideshow frame (`web`) | done | Full-screen slideshow for any browser, with shared display settings and per-screen overrides. |
| Date taken caption | done | Each photo's date at its bottom (`display_date`). Shown in the time zone where it was taken. |

## Google Photos for everyone (hosted auth)

| Item | Status | Notes |
|---|---|---|
| Apply to the Google Photos partner program | now | Draft in [hosted-auth.md](hosted-auth.md). Gates everything below. |
| Find out whether the Ambient scope is sensitive or restricted | now | Decides whether a ~$540/year security assessment is needed. |
| Auth broker in Go | after acceptance | Tiny: about 4 endpoints and one table. Running cost about $3 a month on Fly.io ([hosted-auth.md](hosted-auth.md#running-cost-estimate-2026-11-23)). |
| OAuth verification + integration review | after acceptance | Domain, homepage, privacy policy, demo video. |
| `google_ambient` source uses the broker | after acceptance | Instead of the user's own OAuth client. |

## Home Assistant

Goal: people who run Home Assistant can install, set up and manage reframe
from there, without touching Docker or a terminal.

| Item | Status | Notes |
|---|---|---|
| **Home Assistant add-on** | next | The worker is already a container, so packaging it as an add-on is mostly a `config.yaml` and a repository. The web UI opens inside HA's sidebar through Ingress, so no extra port or password is needed. Add-on options map to `SOURCE`, `FRAME`, etc. Covers HA OS and Supervised installs. |
| **Entities over MQTT discovery** | next | The worker publishes to HA's MQTT broker: last sync, photos on frame, errors, a "Sync now" button, a pause switch. Works for any HA install, including Docker-only ones, without writing a Python integration. Needs the Mosquitto add-on or another broker. |
| Worker JSON API | next | Status and actions as JSON, beside the HTML UI. Needed by the custom integration below and useful for scripts. |
| Custom integration (HACS) | later | Proper HA config flow, entities and diagnostics, talking to the worker API. Nicer than MQTT, but more code to maintain and follows HA release changes. Do it once there are users asking. |
| Slideshow on HA dashboards and wall tablets | next | The `web` frame's link works in a dashboard iframe card or a kiosk tablet; the add-on can surface it. |
| **HA as a frame**: `media_player` adapter | idea | A `Frame` that shows photos on any HA `media_player` (Chromecast, smart TVs, Fire TV) through HA's API. Reaches every display HA already knows. |
| HA as a source: Media Source / local media | idea | Use photos HA can already see (`/media`, Immich via HA) as a source. |
| Automations | idea | Comes free with the entities: pause at night, sync when someone arrives home, a different album per season. |

Suggested order: add-on first (easiest install), then MQTT entities (control from
dashboards and automations), then the JSON API and a proper integration if demand
shows up.

## More frames and sources

| Item | Status | Notes |
|---|---|---|
| Discover frames on the network | later | Instead of fixed IPs. Probe TCP 5555, identify Cozylas by `getprop` model and remember each by serial, so a new DHCP address is found again. mDNS doesn't help (see [cozyla-frame.md](cozyla-frame.md#finding-frames-on-a-network)). Could offer "Add this frame" on the dashboard. |
| Lock down Cozyla adb from the worker | idea | On each connect, add an `iptables` rule on the frame allowing only the worker. A backup to network isolation ([cozyla-frame.md](cozyla-frame.md#recommendations)). |
| Generic Android frames over adb | later | Most of `cozyla_adb` already applies; the folder and the app's library differ. |
| Slideshow extras | idea | Clock or date overlay, quiet hours (dim or blank screen), place captions (shared albums strip GPS), transition choices. |
| DLNA/UPnP media server | idea | Many TVs and frames can browse a DLNA server. The worker would serve the synced folder. |
| Chromecast / Google TV slideshow | idea | Directly, or through the HA `media_player` adapter above. |
| Immich, Nextcloud, plain folders as sources | later | Each is one `Source` implementation. |

## Decisions to make

| Question | Options | Leaning |
|---|---|---|
| Language for the auth broker | Go, Python | Go: one small binary, cheap to run. |
| Rewrite the worker in Go too? | Keep Python; rewrite in Go | Keep Python until the worker settles. Revisit if the HA add-on image size or memory on small devices becomes a problem. |
| Where to host the broker | Fly.io, AWS serverless, VPS | Fly.io: simplest and about $3/month. |
| License | MIT, Apache-2.0, AGPL | Undecided. AGPL keeps hosted forks of the broker open. |
| First non-Google source | Share link, Immich | Share link, built first. Immich stays an option for later. |
