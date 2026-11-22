# Focus blocking for the work laptop

Added 2026-11-22. Blocks Reddit, Globo (G1 and related), UOL and BBC for the work laptop (192.168.0.9) only, and lets you open any of them for 5, 10 or 30 minutes from **http://focus.lan**.

## Why these sites

From 12 days of Pi-hole queries (11-22 Nov, about 440,000 from the laptop), the biggest non-work traffic was Reddit (about 9,200 lookups, 10 of 11 days, mostly 11:00-18:00), Globo (about 2,100), TikTok (about 7,500, almost all on 12-13 Nov) and BBC (about 700). Numbers are DNS lookups, not page views: header-bidding ads inflate them. UOL was in the request but barely shows in the data (a handful of lookups).

Not blocked, on purpose: TikTok (one-off), Twitter/X and YouTube (links and embeds you may need), WhatsApp and Facebook (WhatsApp Web may be work), Netflix, shopping. Candidates to add are listed at the end.

## How it works

- Pi-hole has one group per site: `focus-reddit`, `focus-globo`, `focus-uol`, `focus-bbc`. Each owns one regex deny rule and is **enabled = blocked**.
- 192.168.0.9 is a client in `Default` (keeps the ad lists) plus the four focus groups. No other device is affected.
- The `focus` container (`~/focus`, port 8095, proxied as `focus.lan`) calls the Pi-hole API to **disable** a site's group for N minutes and enables it again when time is up. The Pi's own "disable blocking" is global, so it can't do this.
- State is kept in `~/focus/data/state.json`, so a restart does not forget an open window. Every open and close is appended to `~/focus/data/log.csv`, so you can later see how often you opened what.
- Fail-safe: the container re-checks Pi-hole every 5 minutes and re-blocks anything whose time ran out, even if a call failed.
- Maximum open time is 120 minutes (`FOCUS_MAX_MINUTES`).

The deny rules (exact text in [pi/focus/setup-groups.sh](pi/focus/setup-groups.sh)) match the domain and all subdomains:

| Group | Domains |
| --- | --- |
| focus-reddit | reddit.com, redd.it, redditmedia.com, redditstatic.com, redditinc.com |
| focus-globo | globo.com, globo.tv, glbimg.com, globoi.com, globovideos.com, contem1g.com.br, the `.globo` TLD |
| focus-uol | uol.com.br, uol.com, bol.com.br, imguol.com, imguol.com.br |
| focus-bbc | bbc.com, bbc.co.uk, bbci.co.uk, bbcimg.co.uk |

## Using it

- Open http://focus.lan (also a card on home.lan), press 5, 10 or 30 min next to a site. It shows the time left and a "Block now" button.
- From a script or the phone: `curl -X POST http://focus.lan/api/open -d '{"site":"reddit","minutes":10}'` and `curl -X POST http://focus.lan/api/close -d '{"site":"all"}'`.
- Blocked lookups answer `0.0.0.0`. The reply is cached only for a couple of seconds, so a site works right after you open it. If a page still fails, close and reopen the tab (the browser may hold its own cache).

## Tested (2026-11-22)

With a temporary test client (since removed): the Reddit rule answered `0.0.0.0`, BBC (not in that group) resolved, "open reddit 1 min" made Reddit resolve, "close all" blocked it again, and a 1-minute window of UOL expired by itself and the group was enabled again. The laptop itself was idle at the time, so its first real blocked lookups still have to be seen in the query log.

## Known gaps

1. **The IPv6 path.** The router relays some of the laptop's DNS over IPv6, and Pi-hole then logs it as 192.168.0.1 (shared with phones), so a per-IP rule does not see it. Over the last 3 days about 45% of Reddit and Globo lookups came from .1 (not all of it the laptop). Until that is closed, Reddit may still load when the laptop uses the IPv6 resolver. Fix options, from least to most invasive: (a) on the laptop, ignore the IPv6 DNS from the router (`nmcli connection modify <wifi> ipv6.ignore-auto-dns yes`, then reconnect); (b) turn off the router's IPv6 DNS advertisement (see [02](02-pihole.md)); (c) block these names for .1 as well, which would also block them on phones.
2. **The laptop's address.** The rule is tied to 192.168.0.9. If the router hands the laptop another address, the rules stop applying. Reserve .9 for the laptop's MAC in the router (the laptop uses a private, random MAC per network, so reserve the one it uses at home).
3. **Away from home** the laptop does not use Pi-hole, so nothing is blocked.
4. **DNS-over-HTTPS in the browser** would bypass all of it. Keep it off on the work laptop.

## Undo

- Remove the whole thing: `cd ~/focus && docker compose down`, then in Pi-hole delete the four `focus-*` groups, the four regex rules with comment `focus-*`, and the client 192.168.0.9 (`docker exec pihole pihole reloadlists` afterwards). Remove the `focus.lan` block from `~/proxy/nginx.conf` (backup `nginx.conf.bak-focus`) and the name from `dns.hosts`.
- Open everything for good: disable the four groups in Pi-hole → Groups.
- Add a site: add an entry to `RX` in [pi/focus/setup-groups.sh](pi/focus/setup-groups.sh), run it on the Pi, and add the site name to `FOCUS_SITES` in the compose file.

## Candidate sites to add (ask first)

TikTok, Twitter/X (`twitter.com`, `x.com`, `twimg.com`, `t.co`), YouTube, Netflix, Mercado Livre, Temu, OLX, Wikipedia/Medium/NPR-style reading. These were seen in small amounts and some may be work.
