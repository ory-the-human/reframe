# The Cozyla frame: discovery and security

What we found on two Cozyla frames on 2026-11-24, how to find them on a
network, and what it means for anyone running cozysync with a Cozyla.

## The frames we checked

Two Cozyla CD-3X341F frames on a home network, with identical firmware and apps.
Their addresses come from DHCP and changed over time, so give each frame a fixed
lease in the router (or, later, let cozysync find them: see below). The owner's
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
4. **A place for an attacker to stay.** An always-on, never-updated root device is
   a good place for malware from another device to persist.

Fixing this on the frame (turning on adb auth) means changing `/default.prop`
in the boot image, which risks bricking it. We don't.

## Recommendations

1. **Isolate the frames** on an IoT network or VLAN, with a firewall rule that
   lets only the cozysync host reach port 5555. This removes most of the risk;
   cozysync needs nothing else.
2. **Try blocking their internet.** If cozysync pushes the photos, the frames may
   not need the Cozyla cloud; check the clock (NTP) and the app still work.
3. **A firewall rule on the frame** as a backup: as root, cozysync can add an
   `iptables` rule letting only its own address reach 5555, on every connect.
   It's lost on reboot, so it complements 1 and doesn't replace it.
4. Treat a working adb connection as proof of nothing: anyone can connect.
