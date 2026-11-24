# Hosted auth broker for Google Photos

Status: proposal, waiting on the Google Photos partner program
(see [decisions.md](decisions.md), entries 2 and 3).

## Shape

```
                         ┌──────────────────────────────┐
                         │ auth broker (we host)        │
                         │ - Google OAuth client+secret │
                         │ - encrypted refresh tokens   │
                         └──────────────┬───────────────┘
                     sign-in, token refresh │ (short-lived access tokens)
                                        │
Google Photos ──(Ambient API, bearer)──> worker (user hosts) ──> frames
                                         - sync state only        Cozyla (adb), folder,
                                         - photos pass through    TV, …
```

- **The broker only does auth.** It runs the device-code sign-in, registers the
  Ambient device, and returns short-lived access tokens (about 1 hour) to the
  user's worker. It never calls `mediaItems.list` and never sees photos.
- **The worker does everything else.** It calls the Ambient API with the access
  token, downloads photos straight from Google, pushes them to the user's frames,
  and keeps only sync state.
- **The client secret never leaves the broker.** Workers are open source and run
  on user hardware, so they can't hold it.

## The broker can't use the tokens on its own

The broker stores each user's Google refresh token encrypted with a key that only
that user's worker holds:

1. At pairing, the worker generates a random key and a worker id, and keeps both
   in its `/data`.
2. After sign-in, the broker encrypts the refresh token with the worker's key,
   stores the ciphertext, and forgets the key.
3. To refresh, the worker sends its id and key. The broker decrypts in memory,
   calls Google, and returns the new access token. The key is never written down.

A stolen broker database is then useless without each worker's key. Logs must
never contain request bodies. This is worth stating in the OAuth and integration
reviews.

## Broker API (sketch)

| Call | Purpose |
|---|---|
| `POST /v1/pair` | Worker sends its public id; broker starts the device-code flow (with the Ambient `state`) and returns the user code and verification URL. |
| `GET /v1/pair/{id}` | Worker polls; once the user has signed in, the broker registers the Ambient device and returns its id and `settingsUri`. |
| `POST /v1/token` | Worker sends id and key; broker returns a fresh access token. |
| `DELETE /v1/pair/{id}` | Unlink: revoke the Google token, delete the Ambient device, drop the row. |

Small enough for one container or a serverless function plus a tiny database.

## What it would take

1. **Partner program acceptance.** Submit the
   [interest form](https://services.google.com/fb/forms/photospartnerprogram/)
   (draft below). No published criteria or timeline; it may be declined or go
   unanswered.
2. **OAuth app verification** for the broker's Google Cloud project: an owned
   domain, a homepage, a privacy policy on that domain, domain verification in
   Search Console, and likely a demo video of the consent flow. If Google treats
   the Ambient scope as *restricted*, add a yearly third-party security
   assessment (CASA), which can cost money. Find out the scope's class before
   committing.
3. **Google Photos APIs integration review.** Details only after acceptance. It
   may have rules about tokens being used on machines other than the broker;
   ask early.
4. **Running it.** Uptime matters: if the broker is down, every worker stops
   syncing when its access token expires. Plus hosting, abuse limits (100 Ambient
   devices per user, 240 list calls per device per day), support, and keeping
   up with policy changes. Google can withdraw access at any time.

## Running cost (estimate, 2026-11-23)

**Load.** A worker refreshes its access token about once an hour, so each frame
makes ~24 calls a day, about 720 a month. Pairing and unlinking are rare. Each call
is one small database read, a decrypt, and one call to Google's token endpoint. The
database holds ~1 KB per frame.

| Frames | Calls / month | Average load | Database |
|---|---|---|---|
| 100 | 72k | ~0.03/s | ~100 KB |
| 1,000 | 720k | ~0.3/s | ~1 MB |
| 10,000 | 7.2M | ~3/s | ~10 MB |

A Go service with SQLite handles 10,000 frames on the smallest machine any host
sells.

**If the broker goes down,** photos already on frames keep showing. Only new syncs
pause, once each worker's current access token (about 1 hour) runs out. So one
machine with quick restarts and good backups is enough; no multi-region setup is
needed.

| Option | Setup | Monthly cost |
|---|---|---|
| **Fly.io** (recommended) | One `shared-cpu-1x` 256 MB machine, SQLite on a 1 GB volume, [Litestream](https://litestream.io) backups to object storage | ~$2.19 machine ([Fly pricing](https://fly.io/pricing), after the [Oct 2026 update](https://fly.io/pricing-update/)) + ~$0.15 volume + cents for backups and egress ≈ **$2.50–3**, flat up to 10k frames |
| AWS serverless | Lambda (Go, arm64) behind a Function URL, DynamoDB on demand | 1k frames: within the always-free Lambda and DynamoDB tiers, so **~$0–1**. 10k: **~$2–3**. Putting API Gateway in front instead of a Function URL adds $1 per million calls. |
| Small VPS (Hetzner or similar) | Go binary + SQLite + backups, managed by us | **~$4–6**, plus patching the OS ourselves |
| Cloudflare Workers + D1 | Free tier covers 100k calls/day (about 4k frames) | **$0**, or $5 paid. Go only works there through WebAssembly, so this effectively means TypeScript. |

**Costs that don't depend on the host**

- Domain: ~$10–15 a year. It's needed for OAuth verification anyway.
- Google OAuth verification: free.
- **A CASA security assessment, only if the Ambient scope is *restricted*:** about
  **$540 a year** for Tier 2 through Google's discounted assessor, going by a
  [2024 report](https://groups.google.com/g/google-apps-script-community/c/XgsHT2Fr7u8)
  that I couldn't confirm against an official source. Google requires the
  assessment at least every 12 months
  ([Google's help page](https://support.google.com/cloud/answer/13465431)).
  Photos scopes have been classed as *sensitive* (no assessment), but nothing
  published confirms that for `photosambient.mediaitems`. If it is restricted, this
  one item costs more than all the hosting together.
- Uptime checks and alerting: the free tiers of Fly's metrics or a monitoring
  service are enough.

**Bottom line.** About **$3 a month on Fly.io plus ~$1 a month for the domain**, or
roughly $50 a year, from the first user up to about 10,000 frames. Add ~$540 a year
if Google requires CASA. The real cost is people's time: the reviews, support, and
keeping up with Google's policies.

## Open questions

- Is `photosambient.mediaitems` a sensitive or a restricted scope?
- Does the integration review allow access tokens issued by the broker to be
  used from the user's own machine?
- Does Google expect one Ambient device per physical screen? We register one
  per Google account and drive several frames from it (decisions.md, entry 5);
  ask during the integration review.
- Where to host the broker, and who pays for it.
- The name: "cozysync" is tied to Cozyla, but the worker supports any frame.

## Partner program application (draft)

> **Product.** cozysync, a free, open-source service that shows a user's
> chosen Google Photos albums on photo frames and TVs they already own, such as
> Cozyla frames, Android-based frames, and any display that shows images from a
> folder.
>
> **How it uses Google Photos.** Users sign in once with the
> limited-input-device flow and pick albums inside Google Photos, exactly as
> with Ambient-integrated frames. Each user runs a small sync worker at home
> that lists the picked albums and shows them on their frames. We host only the
> sign-in and token-refresh service; photos go straight from Google to the
> user's own device and never pass through or get stored on our servers.
>
> **Why the Ambient API.** Many frames have lost Google Photos support or rely on
> public share links, which expose private albums. The Ambient API lets users
> keep albums private and control what's shown from Google Photos settings.
>
> **Scale.** Open source, self-hosted. We expect hobbyist and home users at
> first. Each frame is one Ambient device, and a sync every few hours stays well
> within the per-device list quota.
>
> **Security and privacy.** Refresh tokens are stored encrypted with a key only
> the user's worker holds, so our servers can't use them alone. No photo
> content or metadata is stored by us. Users can unlink at any time, which
> revokes the token and deletes the device.
>
> **Contact / repository.** *(fill in)*
