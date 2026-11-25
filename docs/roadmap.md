# Roadmap

Where reframe could go, and the decisions each path needs. Settled decisions move
to [decisions.md](decisions.md); the step-by-step plan for the current work is in
[PLAN.md](../PLAN.md).

Status: **now** (in progress or next), **next** (after now), **later** (wanted, not
scheduled), **idea** (worth recording, undecided).

## Get the owner's frame working

| Item | Status | Notes |
|---|---|---|
| Second source: Google Photos share link or Immich | now | Pick one. Share link: keeps photos in Google Photos, but the link is a secret and the page can change. Immich: private and stable, but photos must be in Immich. |
| Sync engine (pass-through, safe removal) | now | PLAN.md phase 2. Uses only the `Source` and `Frame` interfaces. |
| Show synced photos in the Cozyla app | idea | Works only with the frame kept offline ([cozyla-frame.md](cozyla-frame.md)); the kiosk below replaced it. |
| Cozyla frames as kiosks | done | One-click setup and exact restore from the frame's page ([decisions.md](decisions.md), entry 7). |
| Store kiosk photos on the frame | next | Push the slideshow page and files to the frame, so it keeps playing with the server off. |
| On-frame agent | now | Our own small Android app on the frame, so it manages itself and adb is only for setup and recovery (the frames' adb hangs and needs a power cycle). It calls the server, never the other way round: fetches its settings, reports back, and keeps working with the server off. Jobs: screen schedule and light-sensor sleep; a watchdog that keeps the kiosk in front, the Cozyla app off and the kiosk's settings as written, and restarts adb when it hangs; health reports. reframe installs it with the kiosk and removes it on restore. Screen, clock and health work, and kiosk setup installs it; the watchdog and the first published release are next ([agent.md](agent.md)). |
| Frame health in the admin app | now | Temperatures, CPU load, memory, storage, uptime and the watchdog's last fixes per frame, on its page, the dashboard and `reframe health`. Pages done; needs the agent to report. |
| Video conversion in the sync engine | next | H.264 8-bit, sides a multiple of 32, sized per frame ([cozyla-frame.md](cozyla-frame.md#video-on-the-frame-tested-2026-11-24)). |
| Kiosk over MQTT | idea | Webview Kiosk speaks MQTT (with Home Assistant discovery); connect it when a broker is configured. |
| Unattended loop, health, alerts | next | PLAN.md phase 4. |
| Several frames per worker | done | Each with its own type, albums and display settings ([decisions.md](decisions.md), entry 5). |
| Browser slideshow frame (`web`) | done | Full-screen slideshow for any browser, with shared display settings and per-screen overrides. |

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
| Slideshow extras | idea | Clock or date overlay, quiet hours (dim or blank screen), photo date and place captions, transition choices. |
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
| First non-Google source | Share link, Immich | Undecided. |
