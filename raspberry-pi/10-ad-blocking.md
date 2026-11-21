# Ad blocking: current state and how to improve it

Checked 2026-11-20, 21:15.

**Update, 2026-11-20 22:08: the proposed change below is applied.** HaGeZi Pro is on the `Default` group only; `in.logs.betterstack.com` and `(\.|^)on\.aws$` are allowed; gravity grew from 76,105 to 320,161 domains. `i.clarity.ms` now answers `0.0.0.0`, and the BetterStack name still resolves. The gravity rebuild ran at low priority while the power supply is still marginal. Watch for a day and allow any name that breaks a site (Query Log → Allow). The owner is also trying other lists, so more changes may follow.


**Bottom line:** blocking works again for every device. The StevenBlack list is now assigned to `Default` as well as the TV group, and list-blocked queries went from 0 an hour to 562 in the 20:00 hour.

You still see ads for two reasons:

- **StevenBlack is a small, exact-match list.** It only blocks the exact names it lists, not their subdomains.
- **Many ads can't be blocked by DNS at all**, because they come from the same domain as the content.

Adding a wildcard list (HaGeZi Pro) with a short allowlist for your work tools would roughly triple the coverage. A browser blocker covers the rest.

## Live checks from this laptop

| Name | Answer | Meaning |
| --- | --- | --- |
| `doubleclick.net`, `googleads.g.doubleclick.net`, `securepubads.g.doubleclick.net` | `0.0.0.0` | Blocked |
| `cdn.taboola.com` | `0.0.0.0` | Blocked |
| `i.clarity.ms` (Microsoft Clarity session recording) | resolves | **Not on StevenBlack.** HaGeZi Pro blocks it. |
| `use-application-dns.net` | no answer | Pi-hole tells Firefox not to switch to its own DNS-over-HTTPS (`dns.specialDomains.mozillaCanary = true`) |
| `mask.icloud.com` | no answer | iCloud Private Relay is disabled on the LAN (`iCloudPrivateRelay = true`) |

## How much more a bigger list would block

Method: every name your devices resolved, not blocked, in the 24 h before 21:00 on 20 Nov (2,863 names, 74,865 queries), compared against each list. Wildcard lists (`||name^`) also block subdomains. StevenBlack (hosts format) only blocks exact names.

| List | Domains in the list | Names it would block | Share of queries | Not already on StevenBlack |
| --- | --- | --- | --- | --- |
| StevenBlack (current) | 76,106 | 350 | | |
| **HaGeZi Pro** | 244,056 | **957 (33%)** | 13.4% | 637 |
| HaGeZi Multi (Normal) | 194,449 | 700 (24%) | 9.3% | 418 |
| OISD big | 226,424 | 682 (24%) | 10.2% | 402 |

The busiest names HaGeZi Pro would add are Google ad-quality and beacon hosts, site error reporting, Microsoft Clarity, Sentry and Datadog error and telemetry intakes, Permutive, browser telemetry, and news-site trackers.

By device, HaGeZi Pro would hit 704 names behind the router (the IPv6 path), 308 on the work laptop, 359 on this laptop and 26 on the phone.

### What it would break, if nothing is allowlisted

Among the names HaGeZi Pro would block are some you may need for work:

| Name | What it is | Suggestion |
| --- | --- | --- |
| `in.logs.betterstack.com` | BetterStack log ingestion | **Allow**, since you use BetterStack |
| `*.on.aws` (e.g. `…ecs.us-west-2.on.aws`) | AWS service endpoints | **Allow** with regex `(\.\|^)on\.aws$` |
| `monorail-edge.shopifysvc.com`, `error-analytics-…shopifysvc.com` | Shopify analytics and error reporting | Allow if a Shopify admin page misbehaves |
| `collector.github.com`, Copilot telemetry, `bzr.openai.com` | Telemetry | Fine to block |
| `iadsdk.apple.com`, Samsung and Huawei metrics | Ad and analytics SDKs | Fine to block |

The work laptop also reaches Pi-hole through the router's IPv6 path, where it shows up as 192.168.0.1. A separate "work" group with a lighter list would not cover that path, so allowlisting the few work names is the safer approach.

## What DNS blocking can't do

- **Same-domain ads.** YouTube video ads, Instagram and Facebook feed ads, Google search "sponsored" results and Reddit promoted posts come from the same names as the content. Blocking them would break the site.
- **Apps with built-in addresses or their own encrypted DNS.** The router blocks port 853, and Pi-hole handles the Firefox and iCloud cases, but apps can still use DNS-over-HTTPS on port 443.
- **Answers cached before the change.** Browsers and phones keep answers for a few minutes. Restart the browser if an ad that should be blocked still shows.

For these, use a browser blocker: **uBlock Origin** in Firefox on the laptops (full version), uBlock Origin Lite in Chrome, and Firefox with uBlock Origin or Brave on Android.

## Proposed change (not applied)

1. **Allowlist first:** Pi-hole → Domains → add exact allow `in.logs.betterstack.com` and regex allow `(\.|^)on\.aws$`, group `Default`.
2. **Add the list:** Pi-hole → Lists → add `https://cdn.jsdelivr.net/gh/hagezi/dns-blocklists@latest/adblock/pro.txt`, group `Default` only. The TV group already blocks everything except YouTube and Prime.
3. **Rebuild:** Tools → Update Gravity, or `docker exec pihole pihole -g`. Downloading and parsing 5.6 MB takes a few minutes on this Pi. Do it after the power supply arrives, because it is a heavy job.
4. **Watch for a day.** If a site breaks, find the blocked name in the Query Log and allow it.

Cost: gravity.db grows from 4.6 MB to about 20-25 MB (estimate), and the weekly gravity update takes longer. DNS lookups stay fast because Pi-hole v6 checks lists through an indexed database.

Rollback: disable the list on the Lists page and run Update Gravity.

Side effect on the "150 concurrent" warning: every blocked name is answered locally and never forwarded. With StevenBlack alone, 48% of the forwarded names in the 20 Nov 14:40 burst would have been answered locally ([02-pihole.md](02-pihole.md)). HaGeZi Pro covers more of that ad-tech.
