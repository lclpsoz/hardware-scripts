# Pi-hole: the 150-query limit, a missing blocklist and other findings

> **Reading the query database:** since `database.DBinterval` is 300, `pihole-FTL.db` lags the live log by up to 5 minutes. For the last few minutes use `docker exec pihole grep "from <ip>" /var/log/pihole/pihole.log` or the web Query Log, which reads memory. Querying the database too early can wrongly show a device as silent.

Checked 2026-11-20, read-only. Pi-hole Core v6.5, Web v6.6.1, FTL v6.8 (Core v6.6 and FTL v6.9 are available). Docker host networking, listening mode `LOCAL`, upstreams 8.8.8.8 and 8.8.4.4.

**Bottom line:** the "150 concurrent queries" warning is not a resource limit. RAM and CPU were fine. It is a count of upstream queries waiting for an answer. It fills up when one device loads ad-heavy pages and some of those forwards never get a reply.

The same investigation found three more problems:

- **Nobody but the TV gets ad blocking.** The only blocklist is assigned only to the TV's group.
- **The TV makes about a third of all queries,** retrying blocked time servers.
- **The router nearly tripped Pi-hole's rate limit** for every device that uses it for DNS.

> **Status on 2026-11-20, 21:15:**
> - You re-assigned the blocklist to `Default`, so all devices are blocked again ([10-ad-blocking.md](10-ad-blocking.md)).
> - The TV is unplugged for now, which pauses its retry storm until it reconnects.
> - `database.DBinterval` is now 300.
> - The web UI moved to port 8080 behind the `.lan` proxy ([07-services.md](07-services.md)).
> - The rate limit and `dns-forward-max` are unchanged, and section 8 recommends values.

## 1. The "Maximum number of concurrent DNS queries reached (max: 150)" warning

### When it happened

| Date | Warnings | Main client at that moment |
| --- | --- | --- |
| 20 Nov | 14:40:46 and 14:40:52 | 192.168.0.9 |
| 19 Nov | 09:24:37 and 09:24:43 | 192.168.0.9 |
| 18 Nov | 12:25:40 and 12:25:46 | 192.168.0.1 (the router, relaying other devices) |
| 18 Nov | 10:06:48 | 192.168.0.9 |

Pi-hole prints this warning at most once every 5 seconds per upstream, so a pair means at least 5-10 seconds at the limit.

### What the limit is

The 150 is dnsmasq's `dns-forward-max`: the maximum number of queries forwarded upstream and still waiting for a reply. Pi-hole's generated `/etc/pihole/dnsmasq.conf` does not set it, so the default of 150 applies ([dnsmasq man page](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html)).

Pi-hole's [explanation of this warning](https://docs.pi-hole.net/ftldns/dnsmasq_warn/) lists two matching causes:

- "being spammed with an excessive amount of duplicates"
- "the upstream server never replies for specific domains"

Both are visible here.

### What filled it

These numbers come from Pi-hole's long-term database, for a 40-50 s window around each warning. "Stale" means answered from expired cache while a refresh is sent upstream. "Duplicate" means asked again while the first copy was still waiting.

| Window | Top client | Queries | Forwarded | Stale | Duplicate | Reply time of answered forwards |
| --- | --- | --- | --- | --- | --- | --- |
| 20 Nov 14:40:20-14:41:00 | .9 | 699 | 206 | 203 | 124 | average 63 ms, max 242 ms |
| 19 Nov 09:24:10-09:25:00 | .9 | 809 | 458 | 87 | 106 | average 73 ms, max 261 ms |
| 18 Nov 12:25:10-12:26:00 | .1 | 949 | 470 | 31 | 1 | average 77 ms, max 279 ms |
| 18 Nov 10:06:20-10:07:00 | .9 | 470 | 77 | 65 | 74 | average 70 ms, max 155 ms |

Answered forwards came back quickly. At about 70 ms each, 150 queries in flight would need more than 2,000 forwards a second, and the bursts are far below that. **What fills the slots is forwards that never get a reply.** They hold their slot until dnsmasq gives up on them.

- **About a third of the forwards in the burst windows got no reply at all:** 160 of about 534 on 19 Nov, and 71 of about 218 on 20 Nov.
- **In the raw log for 14:40:40-14:40:46 on 20 Nov,** 21 of the 134 distinct names forwarded never got any reply line within the next 45 s. Examples: `ad.360yield.com`, `ap.lijit.com`, `beacon-iad6.rubiconproject.com`, `owv2.pubmnet.com`, `playwire-d.openx.net`.
- Where those replies were lost is not proven. Either Google's resolver was slow to answer those ad-tech names, or packets were lost on the path through Claro's carrier-grade NAT (100.66.64.1) during the burst.

### What triggered it

The trigger was one device loading ad-heavy pages:

- **How many:** at 14:40, 192.168.0.9 sent 596 queries in 10 seconds, almost all for header-bidding ad domains (rubiconproject, seedtag, openx, smartadserver, flashtalking, playwire, pubmatic).
- **Why so many:** browsers ask for A, AAAA and HTTPS records for every name, which triples the load.
- **Why they reached the upstream:** they weren't blocked, because this device has no blocklist (next section).

The raw log of that burst isn't kept in this repo, because it is browsing history. The counts above are taken from it.

### Fixes, most effective first

1. **Give the Default group the blocklist** (section 2). Of the 164 distinct names forwarded in 14:40:30-14:40:59, **78 (48%) are on the StevenBlack list** you already have. They would be answered locally and never forwarded.
2. **Add a list that blocks whole subdomains.** For example HaGeZi "Pro" in adblock format, which Pi-hole v6 supports. It catches most of the remaining ad-tech names. Gravity updates get heavier, so do this after the power fix.
3. **Raise `dns-forward-max` to 512.** This is cheap, a few KB of memory, but it only hides the symptom.
4. **Switch the upstreams to Cloudflare (1.1.1.1, 1.0.0.1) or Quad9.** In my samples Cloudflare was steady at 31-35 ms, and Google varied between 31 and 147 ms (section 5).
5. **Later, optionally: encrypted upstream DNS** (DNS-over-TLS or HTTPS through a small local forwarder). That sends everything over one long-lived connection instead of hundreds of separate UDP flows through the ISP's NAT. Claro also stops seeing your queries. Cost: about 20-40 MB of RAM. See [06-ideas.md](06-ideas.md).

## 2. Only the TV gets a blocklist

The only adlist is assigned to just one group:

| List | Domains | Groups |
| --- | --- | --- |
| StevenBlack hosts | 77,310 | `YouTubeAndPrimeOnly` only (the TV's group) |

The `Default` group, which every other device uses, has no adlist. Its only entries are 4 exact Netflix denies.

The evidence:

- **Blocked queries in the last 24 h:**
  - 36,732 blocked by regex, all from the TV's deny-all rule;
  - **502** blocked by the list;
  - 8,458 forwarded upstream.
- **Live test at 20:09:** `doubleclick.net` from this laptop was forwarded to 8.8.4.4 and returned 142.250.219.142. The DNS runbook recorded `0.0.0.0` for the same test on 11 Nov at 01:3x.
- **When it changed:** the TV group's deny-all regex was added at 02:39 that day. So the list was probably moved to the TV group, rather than added to it, while setting up the TV.

**Fix (UI):** Lists page, StevenBlack row, Group assignment: tick **Default** as well as `YouTubeAndPrimeOnly`, then save. Check from the laptop: `dig @192.168.0.250 doubleclick.net +short` should print `0.0.0.0`.

**Question for you:** was this intentional? I assume not.

## 3. The TV retries blocked time servers: about a third of all queries

192.168.0.3 is the Samsung TV: client "SmartTV" (matched by MAC), group `YouTubeAndPrimeOnly`. That group has the deny-all regex `.*` plus allow regexes for YouTube and Prime Video. The addresses have changed since the 11 Nov runbook, when the TV was .4 and the phone .3.

| Domain (last 24 h) | Queries | Status |
| --- | --- | --- |
| `time.samsungcloudsolution.com` | 32,245 (21,520 A + 10,725 AAAA) | Blocked by `.*` |
| `pool.ntp.org` | 3,575 | Blocked by `.*` |
| `lcprd1.samsungcloudsolution.net` | 492 | On the blocklist (Samsung logging) |
| `connectivitycheck.gstatic.com` | 244 | Blocked by `.*` |

The TV cannot get the time, so it retries every 2-3 seconds. That is 37,582 of the 108,350 queries in the last 24 h (35%). It inflates the database by about 35,000 rows a day and `pihole.log` by millions of lines a month.

**Fix:** allow the time names for the TV's group only. On the Domains page, add exact allow entries for `time.samsungcloudsolution.com` and `pool.ntp.org`, plus a regex allow `(\.|^)pool\.ntp\.org$` for numbered pool servers. Assign them to `YouTubeAndPrimeOnly`.

Adding `connectivitycheck.gstatic.com` is optional: it lets the TV see that it is online. A correct clock matters because TLS (and so streaming apps) fails with a wrong clock. Keep `lcprd1` blocked.

## 4. The router hides devices and nearly hit the rate limit

192.168.0.1 is the router relaying DNS for devices that use its IPv6 address `fe80::1` as a DNS server. The router advertises that address. This laptop, for example, lists both `192.168.0.250` and `fe80::1`.

To Pi-hole, all of them look like one client:

| Measure | Value |
| --- | --- |
| Queries in 24 h | 27,888 |
| Busiest minute (last 3 days) | **953 queries**, 18 Nov 12:25, the same minute as a concurrency warning |
| Pi-hole rate limit | 1000 queries per 60 s **per client** (default) |
| What happens at the limit | Pi-hole answers REFUSED to that client for the rest of the interval: every device behind the router loses DNS at once |
| Who is behind it (sample of names) | Work web consoles and chat apps (the work laptop), plus Samsung account and cloud services (a phone) |

No rate-limit message has appeared in any log yet, but 953 is 95% of the limit. The rate limit is per client ([Pi-hole config docs](https://docs.pi-hole.net/ftldns/configfile/)).

**Fixes:**

1. **Now:** raise the limit to 5000 per 60 s. With about 10 devices there is no flood risk to protect against.
2. **Later:** stop the router from offering itself as an IPv6 DNS server (the ZTE's DHCPv6 and RA DNS settings). Then Pi-hole sees real devices and per-device rules apply to them too. Pointing the router at the Pi's IPv6 address works, but Claro can change the prefix, which would break it. So check what the router allows first.

## 5. Upstream latency samples

These are single DNS lookups from inside the Pi-hole container to each resolver, taken on 20 Nov around 19:50. Random uncached names force a full lookup; `google.com` measures the round trip only.

| Resolver | Random name (1 sample) | `google.com`, 5 samples (ms) |
| --- | --- | --- |
| 8.8.8.8 (current) | 147 ms | 31, 31, 95, 99, 39 |
| 8.8.4.4 (current) | 139 ms | |
| 2001:4860:4860::8888 | 39 ms | 91, 87, 87, 87, 87 |
| 1.1.1.1 | 35 ms | 31, 31, 31, 35, 35 |
| 2606:4700:4700::1111 | 31 ms | 31, 31, 31, 31, 31 |
| 9.9.9.9 | 63 ms | |

Cloudflare was consistently fast over both IPv4 and IPv6, and Google varied. There are too few samples to call it final, but Cloudflare is the better default from this connection. Keep the upstreams on IPv4: the router firewall's permit rule for the Pi's IPv6 address breaks if Claro changes the prefix. IPv4 goes through Claro's carrier-grade NAT (traceroute hop 2 is 100.66.64.1).

## 6. Database and log growth

| Date (03:39 daily check) | `pihole-FTL.db` | Rows |
| --- | --- | --- |
| 17 Nov | 54.3 MB | 902k |
| 18 Nov | 63.2 MB | 1.05M |
| 19 Nov | 73.6 MB | 1.22M |
| 20 Nov | 80.6 MB | 1.34M |
| 20 Nov 20:00 | 89.1 MB | 1.42M (oldest query: 11 Nov 01:00) |

- **Growth:** about 9 MB and 140k rows a day. With the default 91-day retention, that projects to about 12-13M rows, **around 800 MB**. A large database makes the long-term pages slow and costs CPU at startup and during the daily cleanup.
- **Suggestion:** set `database.maxDBdays` to 30, which caps it near 270 MB at today's rate, or less once the TV storm is fixed.
- **`pihole.log`:** 33-37 MB a day. Keep `dns.queryLogging` on for now: this whole investigation depended on it. Once things are fixed, it can be turned off to save writes. The web Query Log reads from FTL's database, not from this file.

## 7. Other Pi-hole notes

- **Updates:** Core v6.6 and FTL v6.9 are available. Update with `docker compose pull && docker compose up -d` in `~/pihole`. Pulling and extracting an image is heavy work on this Pi, so do it after the power fix, and expect about a minute without DNS.
- **`misc.nice = -10` has no effect:** the container lacks `CAP_SYS_NICE`, and FTL logs a warning at every start. Harmless. To make it work, add `cap_add: [SYS_NICE]` to the compose file.
- **The `CAP_SYS_TIME` warning is harmless.** The host keeps time with systemd-timesyncd, which is synchronised. Pi-hole's NTP server on port 123 still serves time.
- **Healthcheck:** the image's healthcheck runs `dig` through `docker exec` every 30 s. It can be lengthened (see [README.md](README.md#proposed-commands-not-run)).
- **The FTL process shows up as user `lclpsoz` on the host.** That is because the container's `pihole` user is UID 1000. Cosmetic.

## Who the clients are (last 24 h)

MAC addresses are left out because this repo is public. The vendor comes from each MAC's prefix.

| IP | MAC vendor | What it is | Queries |
| --- | --- | --- | --- |
| .3 | Samsung | Smart TV | 37,582 |
| .1 | ZTE | The router, relaying IPv6-DNS devices | 27,888 |
| .9 | (random MAC) | Work laptop: VS Code, Microsoft telemetry and the ad-heavy browsing | 22,919 |
| .7 | Intel | This laptop on Wi-Fi (Firefox) | 16,224 |
| .5 | Samsung | Google and YouTube traffic, probably the phone | 3,375 |
| .4 | (random MAC) | ? | 75 |
| .2 | Samsung | ? (the runbook listed an IoT device at .2) | 56 |
| .6 | Samsung | ? | 28 |

## 8. Can the limits go up? (follow-up, 2026-11-20 evening)

Yes. Neither limit is about the Pi's resources: memory and CPU are nowhere near their limits. Both are safety valves set for much bigger or more exposed servers, and both can be raised safely on a home LAN with `listeningMode = LOCAL`, which already refuses queries from outside your subnet.

### Rate limit (per device, default 1000 queries per 60 s)

Busiest minute per device over the last 3 days:

| Device | Busiest minute | When | What it was |
| --- | --- | --- | --- |
| This laptop | **1,097** | 20 Nov 20:22 | Ad-heavy pages right after blocking came back: 527 of those queries were blocked, and blocked queries count too |
| 192.168.0.1 (router, all IPv6-DNS devices) | 953 and 721 | 18 Nov 12:25 and 20 Nov 20:17 | Several devices added together |
| 192.168.0.9 (work laptop) | 818 | 19 Nov 09:24 | Ad-heavy page loads, on top of a work API name it asks for about 180 times a minute |

The 1,097 minute went over the limit. No rate-limit message survived to confirm whether Pi-hole refused queries: the diagnosis list was empty, and the container's text logs were reset when it was recreated (next section). It was close enough either way.

**Recommendation:** raise it to 5000 per 60 s, which keeps protection against a truly broken device:

```bash
docker exec pihole pihole-FTL --config dns.rateLimit.count 5000
```

**About the work laptop:** a domain looked up 180 times a minute means a client resolves the name for every request. That is typical of scripts and some runtimes, such as Node.js, that don't cache DNS. Reusing HTTP connections (keep-alive), or an OS-level DNS cache, cuts this to a few lookups an hour. Pi-hole answers them from its cache in about 1 ms anyway, so this is about counting toward the limit, not speed.

### Concurrent forwards (dnsmasq `dns-forward-max`, default 150)

These are queries sent upstream that are still waiting for a reply. Each slot costs a few hundred bytes, so 500 slots is well under 1 MB. Raising it doesn't fix unanswered queries, but it stops a few seconds of upstream trouble from stalling every device.

**Recommendation:** raise it to 512 and keep the real fixes running:

```bash
docker exec pihole pihole-FTL --config misc.dnsmasq_lines '["dns-forward-max=512"]'
docker exec pihole grep dns-forward-max /etc/pihole/dnsmasq.conf      # confirm it was written
```

### Blocking helps the most

A blocked name is answered locally and never uses a slot. With StevenBlack alone, 48% of the forwarded names in the 20 Nov 14:40 burst would have been answered locally, and HaGeZi Pro covers more of that ad-tech ([10-ad-blocking.md](10-ad-blocking.md)). Faster, steadier upstreams help next: Cloudflare took 38 ms for a full lookup here, against Google's 144 ms. An encrypted forwarder may cut the unanswered bursts ([09-encrypted-dns.md](09-encrypted-dns.md)).

### Pi-hole's text logs reset when the container is recreated

`/var/log/pihole` (pihole.log, FTL.log) lives inside the container, not on the `etc-pihole` volume. Recreating the container (for an image update or a compose change) starts the logs empty. The long-term query database is on the volume and is kept. That's acceptable, but if you want the logs kept, mount `./var-log-pihole:/var/log/pihole` in the compose file. It costs about 35 MB a day of SD writes.
