# Encrypted upstream DNS: what it would change

Written 2026-11-20. This is a proposal; nothing is deployed.

**Bottom line:** today Pi-hole sends every lookup it can't answer from cache to Google (8.8.8.8) as plain, unencrypted UDP. Encrypting that one leg hides your household's lookups from Claro and protects the answers from tampering. It may also reduce the bursts of unanswered queries behind the "150 concurrent" warnings. The cost is one more small component in the DNS path. Recommended: a small local forwarder ([dnsproxy](https://github.com/AdguardTeam/dnsproxy)) sending DNS-over-TLS to Cloudflare and Quad9.

## What changes, and what doesn't

```
phone/laptop ──plain DNS──► Pi-hole ──plain UDP to 8.8.8.8──► Google        (today)
phone/laptop ──plain DNS──► Pi-hole ──► forwarder ══TLS══► Cloudflare/Quad9 (proposal)
```

- **Inside the house nothing changes.** Devices still ask Pi-hole in plain DNS, which never leaves the LAN, so blocking, local names and the TV allowlist all work as before.
- **Only the Pi-to-resolver leg becomes encrypted.** Claro can still see which IP addresses you connect to, and usually the site name in the TLS handshake. Encrypted DNS removes the cleanest list of everything looked up, but not all traffic metadata.
- **The resolver still sees your queries,** just as Google does today. The choice of resolver is a choice of whom to trust.

## Tests on this connection (2026-11-20)

| Test | Result | Meaning |
| --- | --- | --- |
| Who answers 8.8.8.8 (`o-o.myaddr.l.google.com`) | A Google address, plus `edns0-client-subnet <your public /24>` | No interception by Claro. But **Google forwards part of your public IP (the /24 network) to every website's DNS servers.** |
| Who answers 1.1.1.1 (`id.server`) | Cloudflare, Rio de Janeiro edge (`gig11`) | No interception |
| Who answers 9.9.9.9 (`id.server`) | Quad9, Fortaleza (`res701.qfor1`) | No interception |
| Lookup of a non-existent name via 8.8.8.8 | Empty answer | No NXDOMAIN hijacking |
| Full lookup of a new name (netq, first readings) | Cloudflare 38 ms, Google 144 ms | Cloudflare is about 4× faster here |
| Ping (10 samples) | Cloudflare 31-35 ms steady; Google 31-99 ms | Cloudflare is the steadier path |

## Advantages for you

1. **Privacy from the ISP.** Claro currently sees, in plain text, every name your devices look up, and can log it.
2. **Integrity.** Nobody on the path can alter answers, for example by injecting ads or redirecting typos.
3. **Possibly fewer DNS stalls.** Today every forwarded query is a new UDP flow through Claro's carrier-grade NAT. During ad-heavy page loads about a third got no reply, which filled dnsmasq's 150 slots ([02-pihole.md](02-pihole.md)). An encrypted forwarder keeps one or a few long-lived TCP connections instead. That should help, but it is a hypothesis: netq and the Pi-hole warnings will show whether it does.
4. **No more leaking your /24 to websites,** if the resolver is Cloudflare or a Quad9 variant without ECS.
5. **It fits the DNS-lockdown work.** The router already blocks port 853 for every device except the Pi, so the Pi would be the only encrypted path out.

## Disadvantages

1. **One more link in the chain.** If the forwarder stops, Pi-hole cannot resolve new names. Mitigations: a container with `restart: unless-stopped`; the forwarder's own fallback servers; and netq's "DNS via Pi-hole" probe, which shows failures within a minute.
2. **Slightly slower first lookups after idle.** A new TLS connection costs one extra round trip plus the TLS work on a 600 MHz CPU. Reused connections cost about the same as today.
3. **More RAM and CPU:** 10-30 MB of RAM. CPU is small at home query rates.
4. **Harder debugging.** You can no longer read upstream traffic with tcpdump, though Pi-hole's own log still shows everything.
5. **TLS needs the right time.** The Pi syncs its clock using its own DNS setting (8.8.8.8, outside Pi-hole), so the clock is available before Pi-hole needs it. Keep it that way.

## Options compared

| Option | Encryption | What it is | RAM | Verdict |
| --- | --- | --- | --- | --- |
| **dnsproxy** (AdGuard) | DoT, DoH, DoQ, DNSCrypt | One small binary or container. Several upstreams in `parallel` or `fastest_addr` mode, fallback servers, bootstrap. | about 15-25 MB | **Recommended:** simplest setup, keeps a fallback, Pi-hole keeps doing the caching |
| **unbound** as a forwarder | DoT | Mature C resolver that validates DNSSEC and caches | about 10-20 MB | Good alternative if you prefer unbound. Note that Pi-hole's own unbound guide sets up something different (next row). |
| unbound as a full **recursive** resolver (the [Pi-hole guide](https://docs.pi-hole.net/guides/dns/unbound/)) | **None** | Asks the root, TLD and website DNS servers directly, in plain text | about 10-20 MB | Not here. It is plain text to many servers, slow first lookups ("up to a second"), and the guide warns that ISP interception and carrier-NAT TCP issues can break it. |
| dnscrypt-proxy | DoH (TLS 1.3 / QUIC), DNSCrypt, Anonymized DNS, ODoH | Feature-rich: can hide your IP from the resolver through relays | about 20-30 MB | Strongest privacy, most configuration |
| cloudflared `proxy-dns` | DoH | The old Pi-hole guide method | | **Avoid.** Deprecated by Cloudflare in Nov 2025, and stops working once updated after 2 Feb 2026 ([Pi-hole docs](https://docs.pi-hole.net/guides/dns/cloudflared/)) |

### Which resolver

| Resolver | From here | Privacy notes | Extras |
| --- | --- | --- | --- |
| Cloudflare 1.1.1.1 (DoT host `one.one.one.one`) | fastest and steadiest | Logs deleted within 25 hours, source IPs truncated, "will not sell or share" personal data, audited ([policy](https://developers.cloudflare.com/1.1.1.1/privacy/public-dns-resolver/)) | none |
| Quad9 9.9.9.9 (DoT host `dns.quad9.net`) | 63 ms ping (one sample, Fortaleza) | Non-profit. `9.9.9.9` blocks known-malware domains; `9.9.9.10` is unfiltered; `9.9.9.11` adds ECS ([service list](https://www.quad9.net/service/service-addresses-and-features)) | malware blocking, DNSSEC on every address |
| Google 8.8.8.8 (today) | slower full lookups here | Sends your /24 to website DNS servers (seen in the test above) | none |

Using Cloudflare as primary and Quad9 as a second source in `parallel` mode gives speed plus a second operator if one has trouble.

## How it would be deployed (draft, not applied)

`~/dnsproxy/docker-compose.yml`:

```yaml
services:
  dnsproxy:
    container_name: dnsproxy
    image: adguard/dnsproxy:latest
    network_mode: host
    command:
      - --listen=127.0.0.1
      - --port=5053
      - --upstream=tls://1.1.1.1
      - --upstream=tls://dns.quad9.net
      - --bootstrap=9.9.9.9:53          # only to find dns.quad9.net's address
      - --fallback=tls://1.0.0.1
      - --upstream-mode=parallel
    read_only: true
    mem_limit: 64m
    restart: unless-stopped
```

Then point Pi-hole at it and only at it, because a plain fallback in Pi-hole would leak queries:

```bash
cd ~/dnsproxy && docker compose up -d
docker exec pihole dig @127.0.0.1 -p 5053 example.com +short        # must answer before the switch
docker exec pihole pihole-FTL --config dns.upstreams '["127.0.0.1#5053"]'
# rollback: docker exec pihole pihole-FTL --config dns.upstreams '["8.8.8.8","8.8.4.4"]'
```

Check the flag names against the image's `--help` before the first run. Then compare netq's "DNS via Pi-hole" chart and Pi-hole's concurrency warnings for a week before and after.
