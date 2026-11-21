# Services and their .lan names

Set up 2026-11-20. Every service on the Pi now has a short name. Open **http://home.lan** for a page with links to all of them and a live status dot for each.

| Name | Opens | Real port on 192.168.0.250 |
| --- | --- | --- |
| [home.lan](http://home.lan) (also `pi.lan`) | Links page | 80 (proxy) |
| [pihole.lan](http://pihole.lan) | Pi-hole admin (redirects to `/admin/`) | 8080 (HTTPS: 8443) |
| [netalertx.lan](http://netalertx.lan) | NetAlertX | 20211 (API: 20212) |
| [beszel.lan](http://beszel.lan) | Beszel | 8090 |
| [power.lan](http://power.lan) | Undervoltage history (`uv-web`) | 8099 |
| [net.lan](http://net.lan) | Internet quality ([08-internet-quality.md](08-internet-quality.md)) | 8098, localhost only |
| [ntfy.lan](http://ntfy.lan) | Push notifications server ([11-notifications.md](11-notifications.md)) | 2586, localhost only |
| `router.lan` | The ISP router (192.168.0.1) | 80 on the router |

Old addresses keep working: `http://192.168.0.250/admin` and `http://pi.hole/admin` still reach Pi-hole.

## How it works

```
browser ── http://beszel.lan ──► DNS: Pi-hole local record → 192.168.0.250
        ── HTTP, Host: beszel.lan ──► nginx proxy on :80 ──► 127.0.0.1:8090 (Beszel)
```

- **DNS:** Pi-hole's local DNS records (`dns.hosts`) map each name to the Pi. They appear in the Pi-hole UI under Local DNS, DNS Records, and you can edit them there. `.lan` is also Pi-hole's default local domain (`dns.domain.name = lan`), so unknown `.lan` names are answered locally and never sent to the internet.
- **Proxy:** a small nginx container (`~/proxy`, 2 MiB of RAM) owns port 80 and routes by host name. Files are in [pi/proxy/](pi/proxy/). It writes no access log, to spare the SD card.
- **Pi-hole's own web server moved to 8080 and 8443**, through `FTLCONF_webserver_port` in `~/pihole/docker-compose.yml`. Port 443 on the Pi is now closed. That is deliberate: browsers that try HTTPS first get an immediate refusal and fall back to HTTP, instead of showing a certificate warning.

## Tips

- If the browser searches instead of opening the name, type the full address once (`http://home.lan/`), then bookmark it or add it to the phone's home screen.
- Names only work on devices that use Pi-hole for DNS. A VPN, a work DNS setting or mobile data won't resolve them; use the IP and port from the table instead.
- `router.lan` resolves, but some ISP routers reject unknown host names, so the links page opens the router by IP.

## Add a service

1. Add a `server { server_name new.lan; location / { proxy_pass http://127.0.0.1:PORT; } }` block to [pi/proxy/nginx.conf](pi/proxy/nginx.conf), and a card to [pi/proxy/www/index.html](pi/proxy/www/index.html).
2. Copy both to the Pi (`scp raspberry-pi/pi/proxy/nginx.conf pi:proxy/`), then reload without downtime: `ssh pi 'docker exec proxy nginx -t && docker exec proxy nginx -s reload'`.
3. Add the name in Pi-hole (UI, Local DNS), or:
   `docker exec pihole pihole-FTL --config dns.hosts '[..., "192.168.0.250 new.lan"]'`. Repeat the whole list, because the command replaces it.

## Undo

```bash
cd ~/proxy && docker compose down                      # frees port 80
cd ~/pihole && cp docker-compose.yml.bak-2026-11-20 docker-compose.yml && docker compose up -d   # Pi-hole back on 80/443
docker exec pihole pihole-FTL --config dns.hosts '[]'  # remove the names
```

## HTTPS for the `.lan` names

**Now:** the `.lan` names use plain HTTP on port 80. Pi-hole also serves HTTPS itself on 8443 (`https://192.168.0.250:8443/admin`), with a certificate it created for itself. Browsers warn about it because nothing trusts the certificate yet.

**What HTTPS would protect:** the Pi-hole admin password and session, sent over Wi-Fi or Ethernet. On a home network the risk is small (someone on your Wi-Fi sniffing traffic), but the Pi-hole password also unlocks DNS control.

| Option | Warning in browsers? | Effort | Notes |
| --- | --- | --- | --- |
| A. Use Pi-hole's own `:8443` | Yes, until you trust its CA (`tls_ca.crt` in `~/pihole/etc-pihole/`) on each device | None | Works today. The name must match the certificate, so use the address or name it was made for. |
| B. nginx serves HTTPS for all `.lan` names, with a small local CA | No, once the CA is installed on each device | Medium: make a CA and one certificate, add `listen 443 ssl` blocks, install the CA on laptops and phones | One CA covers every service, including NetAlertX and net.lan. |
| C. A real certificate (Let's Encrypt) | No | Needs a public domain name you own (about 10 USD a year) and a DNS-based challenge; `.lan` names cannot get one | Clean, but the most moving parts. |
| D. Tailscale's certificates | No | Comes with the Tailscale idea in [06-ideas.md](06-ideas.md) | Real, trusted certificates for the Pi's `*.ts.net` name, no CA to install. Works for remote access too. |

**Downsides of any of them:**

- **Trust installation** on every device (A and B). Android and Chrome honor a user-installed CA; Firefox on desktop has its own store; on iPhones the CA needs an extra "full trust" switch. Some apps ignore user CAs.
- **Certificates expire.** A local CA can last 10 years, but the server certificate should be renewed every year or two, which is one more chore.
- **Scripts and bookmarks** that use `http://pihole.lan` need updating, unless HTTP stays open next to HTTPS.
- **A bit more CPU** on every new connection. On this Pi it is a few tens of milliseconds, not noticeable.
- **A mistake can lock you out of the UI**, though the Pi-hole admin is always still reachable on `http://192.168.0.250:8080/admin` while that port stays open.

**Suggestion:** B, because one local CA covers everything and keeps the names. If you plan to use Tailscale anyway, D is simpler and cleaner. Waiting on your choice ([PENDING.md](PENDING.md)).
