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
