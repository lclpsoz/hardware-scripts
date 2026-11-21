# NetAlertX: why every page takes 14-26 seconds

Checked 2026-11-20, read-only. NetAlertX v26.10.28 (the latest release, 28 Oct), Docker host networking, limited to half a CPU.

**Bottom line: the slowness comes from NetAlertX's own code, and the CPU limits make it worse.** Every translated word on a page calls `lang()`. Each call reads and decodes all 24 language files, 1.4 MB of JSON, from scratch.

- **Cost per page:** the Devices page makes 52 such calls, at 421 ms each on this Pi. That is about 22 s.
- **Patched version:** a 15-line change caches the result and does the same 52 calls in 0.3 ms. I tested it against the original, and it returns identical strings.

Device count and database size have nothing to do with it: the database is only 3.9 MB.

## Update 2026-11-20 20:51: patch applied

The patched `lang.php` is mounted read-only over the original (`~/netalertx/docker-compose.yml`, option A below). The CPU limit and the 600 MHz clock are unchanged.

| Page | Before | After |
| --- | --- | --- |
| Devices | 16.5-17.3 s | **1.1-1.4 s**, 0.34 s through `netalertx.lan` once warm |
| Network | 13.8 s | 1.1 s |
| Events | 25.8 s | 1.2 s |
| Presence | 23.1 s | 1.4 s |
| Settings | 20.5 s | 1.3-1.7 s |
| Maintenance | over 40 s | 1.4 s |

**Risks of this optimization, and how they are covered:**

| Risk | Likelihood | Cover |
| --- | --- | --- |
| The patch breaks a page | Very low | Lint passed. Output identical for the keys tested. Every page returned 200 after the switch. Rollback is one command. |
| An image update changes upstream `lang.php`, and the override hides that change | Low, but certain to need a look at each update | `pi-checkup.sh` warns when the image ID differs from `ebce55cdbccc` (v26.10.28), the image the patch was made for. Compare upstream with [pi/netalertx/lang.php.v26.10.28.orig](pi/netalertx/lang.php.v26.10.28.orig) before keeping the override. |
| Recreating the container clears its RAM logs and restarts its schedule | Certain, harmless | Device history lives in the `netalertx_data` volume and is kept |

Rollback: `cd ~/netalertx && cp docker-compose.yml.bak-2026-11-20 docker-compose.yml && docker compose up -d`.

**Settings showed a 500 error right after the restart. That was not the patch.** When the container starts, the backend has to write `/tmp/api/table_settings.json` before the Settings page can render. Under the half-core limit, start-up also runs `update_vendors.sh` (download and sort of the IEEE vendor list), so this took a few minutes. The same page returned 200 in 1.3 s once the backend had finished. Expect a slow first minute or two after every NetAlertX restart until the CPU limit is raised.

## Measurements

Page timings, measured with `curl` on the Pi itself, so no network is involved:

| Request | Time | Size |
| --- | --- | --- |
| `/devices.php` | 16.5 s, then 17.3 s | 50 KB |
| `/network.php` | 13.8 s | 32 KB |
| `/events.php` | 25.8 s | 54 KB |
| `/presence.php` | 23.1 s | 55 KB |
| `/settings.php` | 20.5 s | 484 KB |
| `/maintenance.php` | over 40 s (stopped) | |
| `query_json.php?file=table_devices.json` (the device data) | 0.52 s | 32 KB |
| `query_json.php?file=table_settings.json` | 0.31 s | 327 KB |
| `query_json.php?file=plugins.json` | 0.31 s | 171 KB |

The data itself arrives in half a second, so every page's HTML is slow for a reason shared by all pages: the header template.

## Root cause: `lang()` decodes every language file on every call

From `/app/front/php/templates/language/lang.php`, the same in the image and in upstream `main` today:

```php
function lang($key)
{
    global $pia_lang_selected, $strings;
    $languageData = getLanguageDataFromJson();        // reads + json_decode()s all 24 *.json files
    $sqlLanguageData = $strings;
    $mergedLanguageData = mergeLanguageData($languageData, $sqlLanguageData);
    ...
```

| Measure | Value |
| --- | --- |
| Language files | 24 files, 1.4 MB total |
| `lang()` calls per Devices page | 52 (`header.php` 47, `devices.php` 4, `footer.php` 1) |
| Decoding all 24 files once, inside the container | 310-465 ms |
| One original `lang()` call, measured | **421 ms**, so about **21.9 s** for 52 calls |
| 52 calls with the patch | **0.3 ms** (plus one decode of `en_us.json`, about 20 ms, once per page) |
| Output, original vs patched | Identical for the keys tested |

Upstream check: the subagent read upstream `main` on 20 Nov, and `lang()` has no cache there either (no `static`, no global, no APCu). Updating the image will not fix it.

## Things that make it worse

| Factor | Evidence | Effect |
| --- | --- | --- |
| CPU limit of half a core (`cpus: "0.5"`, added for the power problem) | Throttled in **63%** of scheduler periods since start (433,873 of 694,212). In a quiet 30-s sample, 66%. | Page rendering and the backend share half a core. A burst uses up the quota in a few ms, then everything in the container waits up to 87 ms. |
| CPU pinned at 600 MHz (powersave governor) | | Everything runs at half speed |
| Python startup under the limit | ARP scan plugin started at 20:00:50, its script began at 20:01:01 (11 s). Internet check took 8 s to start. | Each scheduled cycle takes about 40 s |
| `LOG_LEVEL='verbose'` | `app.log` is 3.2 MB in RAM | Extra CPU for log lines nobody reads |
| No PHP OPcache | `php83 -m` lists no `opcache` | PHP recompiles each script on every request (small compared with `lang()`) |

Background load is modest. In a 2-minute sample that included the 20:00 scan, the container used 22.4 s of CPU (18% of one core). The backend loop wakes every 65 s and is otherwise idle.

## Fix options

### A. Patch `lang()` locally (recommended, independent of the power fix)

The patch is in [pi/netalertx/lang-cache.patch](pi/netalertx/lang-cache.patch). The full patched file is [pi/netalertx/lang.php.patched](pi/netalertx/lang.php.patched), and the original is kept as [pi/netalertx/lang.php.v26.10.28.orig](pi/netalertx/lang.php.v26.10.28.orig).

What the patch changes:

- It builds the merged strings once per request, in a `static` variable.
- It decodes only the selected language and the `en_us` fallback, which are the only two `lang()` ever reads.

It passed `php -l` in the container, and the benchmark above.

The container is read-only, so the patched file is mounted over the original:

```bash
# from the laptop
scp raspberry-pi/pi/netalertx/lang.php.patched pi:~/netalertx/lang.php.patched
# on the Pi
chmod 644 ~/netalertx/lang.php.patched
cd ~/netalertx && cp docker-compose.yml docker-compose.yml.bak
# add under services.netalertx.volumes:
#   - type: bind
#     source: ./lang.php.patched
#     target: /app/front/php/templates/language/lang.php
#     read_only: true
docker compose config >/dev/null && docker compose up -d
curl -s -o /dev/null -w '%{time_total}s\n' http://127.0.0.1:20211/devices.php   # expect ~1 s instead of ~17 s
```

- **Undo:** `cp docker-compose.yml.bak docker-compose.yml && docker compose up -d`.
- **Before each future NetAlertX image update,** compare the new upstream `lang.php` with `lang.php.v26.10.28.orig`. If it changed, re-make the patch, or drop the override if upstream fixed it.
- **Recreating the container clears its logs** (kept in RAM) and restarts the scan schedule.

### B. Report it upstream

Here is a draft for [the NetAlertX issue tracker](https://github.com/netalertx/NetAlertX/issues). It contains no personal data. Posting it is your call.

> **lang() re-decodes all language JSON files on every call (pages take 15-25 s on a Raspberry Pi 3)**
>
> `front/php/templates/language/lang.php`: `lang()` calls `getLanguageDataFromJson()` and `mergeLanguageData()` on every call, so each translated string re-reads and json-decodes all 24 language files (1.4 MB). `header.php` alone calls `lang()` 47 times. On a Raspberry Pi 3B one call costs ~420 ms, so every page takes 15-25 s to render. Caching the merged data in a `static` variable (and only decoding the selected language plus `en_us`) brings 52 calls down to well under 1 ms with identical output. Patch: [attach netalertx-lang-cache.patch]. Version: v26.10.28.

### C. After the power fix: give it more CPU

- Raise `cpus: "0.5"` to `"1.0"`, or remove the line.
- Switch the governor back to `ondemand` ([04-resource-limits.md](04-resource-limits.md)).

Without patch A, this alone would only bring pages to roughly 4-6 s (a 2× gain from the clock and a 2× gain from the limit, estimated). With patch A it mostly speeds up the scans.

### D. Small settings

- **`LOG_LEVEL`:** set it to `minimal` in Settings. Switch back to `verbose` only while debugging.
- **Vendor list updates:** the image's internal cron runs `update_vendors.sh` at 00:00, 06:00, 12:00 and 18:00. Each run downloads the IEEE vendor list and sorts it twice. The weekly `VNDRPDT` plugin already updates vendors. It is in the image's crontab, so changing it means overriding `/services/config/cron/crontab` with a mount. That is only worth doing if the midnight events continue after the power fix.

## Other NetAlertX notes

- **No UI password** (`SETPWD_enable_password=False`). Anyone on the LAN can open NetAlertX, change settings and read its API token. That includes the TV and IoT devices, which share the LAN. Recommend turning it on: Settings, SETPWD plugin.
- **What the scans see:** the ARP scan found 4 devices at 20:00 (6 counting the internet check and the Pi itself). Pi-hole saw 8 clients in 24 h. Phones with random MACs and sleeping devices often miss single scans, so the list fills in over time.
- **`psutil sampling failed, using zeros: (pid=205)`** appears every cycle. It only affects NetAlertX's own resource-history chart. Harmless.
- **SD writes:** the container wrote 559 MiB to the SD card in 36 h, about 370 MiB a day, mostly SQLite WAL churn. The database files are small: 3.9 MB `app.db` plus a 4 MB WAL.

## Device naming pattern (set 2026-11-20)

The Devices list shows Name, Owner and Type but not Group, so the usage goes in the name as a suffix.

| Field | Rule | Example |
| --- | --- | --- |
| Name | `Owner Model (Usage)`, Title Case. The usage is a short tag in parentheses, left out when there's nothing to tell apart. Shared devices have no owner. | `Lucas M21s (Money)`, `Lucas Tab A9`, `Samsung TV`, `Smart bulb 1` |
| Owner | `Lucas`, `Ray` or `Home` | |
| Type | Phone, Tablet, Laptop, TV, Smart Light, IoT, Gateway (never "Smartphone") | |
| Group | The same usage word as the suffix: Main, Money, Street, Work, Personal, Home, IoT, Infra. Left empty when there is no usage to tell apart (Ray's devices) | `Money` |
| Icon | One per type: mobile, tablet-screen-button, laptop, tv, lightbulb, microchip, globe | |
| Comments | Model, whether the MAC is real or private (random), reservations, anything unusual | |

Notes:

- **Name source:** every renamed device has its name source set to `USER`, so scans don't overwrite the name.
- **Private MACs:** a phone that uses a random MAC per network creates a new record whenever the MAC changes. Setting Android's Wi-Fi option "Use device MAC" keeps one record. Older random-MAC duplicates were archived (archived, not deleted; unarchive in the device's page).
- **The TV** had been named as a phone; it is the device in Pi-hole's `YouTubeAndPrimeOnly` group.
- **Editing the database directly** needs `docker exec -u netalertx` (the container has no capability to write files owned by another user, even as root).
