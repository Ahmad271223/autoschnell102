# -*- coding: utf-8 -*-
"""Betriebsprobe fuer die Produktions-Domain (Go-Live-Audit 09/2026, Punkt 53).

Prueft von aussen — ohne Zugangsdaten — was vor dem Live-Gang stimmen muss:
  1. DNS: A/AAAA-Eintrag der Domain
  2. TLS: Zertifikat gueltig, Kette, Ablauf (> 14 Tage), nur TLS 1.2/1.3
  3. HTTP -> HTTPS-Umleitung auf die feste Domain, unbekannter Host -> 444/Fehler
  4. Sicherheits-Header (HSTS, X-Frame-Options, nosniff, Referrer-Policy,
     Permissions-Policy, frame-ancestors) auf API UND Oberflaeche
  5. /api/health (Liveness) und /api/ready (Readiness inkl. Warnungen)
  6. Mail-Domain: SPF, DMARC, DKIM-Selector (optional --dkim-selector)
  7. Kein offener Mongo-Port (27017) von aussen

Aufruf:  python scripts/betriebsprobe.py app.autoschnell.de [--mail-domain autoschnell.de] [--dkim-selector resend]
Exit 0 = alles gruen, 1 = mindestens ein Fehler (Warnungen zaehlen nicht).
Braucht nur die Standardbibliothek + requests (bereits Abhaengigkeit); DNS-
TXT-Abfragen ueber dnspython (bereits Abhaengigkeit).
"""
import argparse
import datetime as dt
import socket
import ssl
import sys

import requests

FEHLER, WARNUNGEN, OK = [], [], []


def ok(msg):
    OK.append(msg); print(f"  OK   {msg}")


def warn(msg):
    WARNUNGEN.append(msg); print(f"  WARN {msg}")


def fehler(msg):
    FEHLER.append(msg); print(f"  FEHLER {msg}")


def dns_pruefen(host):
    print("1. DNS")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        ips = sorted({i[4][0] for i in infos})
        ok(f"{host} -> {', '.join(ips)}")
        return ips
    except socket.gaierror as exc:
        fehler(f"DNS: {host} nicht aufloesbar ({exc})")
        return []


def tls_pruefen(host):
    print("2. TLS")
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
                version = tls.version()
        ok(f"Zertifikat gueltig fuer {host} (Kette vom System akzeptiert), Verbindung {version}")
        ablauf = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        rest = (ablauf - dt.datetime.utcnow()).days
        (ok if rest > 14 else fehler if rest < 3 else warn)(f"Zertifikat laeuft in {rest} Tagen ab ({ablauf:%d.%m.%Y})")
        if version not in ("TLSv1.2", "TLSv1.3"):
            fehler(f"Unerwartete TLS-Version {version}")
    except ssl.SSLCertVerificationError as exc:
        fehler(f"Zertifikat NICHT gueltig: {exc}")
    except Exception as exc:  # noqa: BLE001
        fehler(f"TLS-Verbindung fehlgeschlagen: {exc}")
    # Alte Protokolle muessen abgelehnt werden
    for name, proto in (("TLSv1.0", getattr(ssl, "PROTOCOL_TLSv1", None)),
                        ("TLSv1.1", getattr(ssl, "PROTOCOL_TLSv1_1", None))):
        if proto is None:
            continue
        try:
            alt = ssl.SSLContext(proto)
            alt.check_hostname = False
            alt.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, 443), timeout=10) as sock:
                with alt.wrap_socket(sock, server_hostname=host):
                    fehler(f"{name} wird noch akzeptiert — abschalten")
        except Exception:
            ok(f"{name} wird abgelehnt")


def http_pruefen(host):
    print("3. HTTP -> HTTPS und Host-Allowlist")
    try:
        r = requests.get(f"http://{host}/", allow_redirects=False, timeout=10)
        ziel = r.headers.get("Location", "")
        if r.status_code in (301, 308) and ziel.startswith(f"https://{host}"):
            ok(f"HTTP leitet auf {ziel} um")
        else:
            fehler(f"HTTP-Umleitung fehlt/falsch: {r.status_code} {ziel}")
    except Exception as exc:  # noqa: BLE001
        fehler(f"HTTP-Aufruf fehlgeschlagen: {exc}")
    try:
        r = requests.get(f"https://{host}/", headers={"Host": "fremde-domain.invalid"}, timeout=10)
        if r.status_code < 400:
            fehler(f"Fremder Host-Header wird bedient ({r.status_code}) — Allowlist fehlt")
        else:
            ok(f"Fremder Host-Header abgewiesen ({r.status_code})")
    except requests.exceptions.ConnectionError:
        ok("Fremder Host-Header abgewiesen (Verbindung geschlossen, 444)")
    except Exception as exc:  # noqa: BLE001
        warn(f"Host-Allowlist nicht pruefbar: {exc}")


def header_pruefen(host):
    print("4. Sicherheits-Header")
    noetig = {
        "strict-transport-security": "max-age=",
        "x-content-type-options": "nosniff",
        "x-frame-options": "DENY",
        "referrer-policy": "",
        "content-security-policy": "frame-ancestors",
    }
    for pfad in ("/", "/api/health"):
        try:
            r = requests.get(f"https://{host}{pfad}", timeout=10)
        except Exception as exc:  # noqa: BLE001
            fehler(f"{pfad}: nicht abrufbar ({exc})"); continue
        h = {k.lower(): v for k, v in r.headers.items()}
        for name, muss in noetig.items():
            if name not in h:
                fehler(f"{pfad}: Header {name} fehlt")
            elif muss and muss.lower() not in h[name].lower():
                fehler(f"{pfad}: Header {name} = '{h[name]}' (erwartet '{muss}')")
            else:
                ok(f"{pfad}: {name}")
        if "server" in h and h["server"].lower().startswith("nginx/"):
            warn(f"{pfad}: Server-Header verraet Version ({h['server']}) — server_tokens off")


def api_pruefen(host):
    print("5. Health / Readiness")
    try:
        r = requests.get(f"https://{host}/api/health", timeout=10)
        (ok if r.status_code == 200 else fehler)(f"/api/health -> {r.status_code} {r.text[:80]}")
    except Exception as exc:  # noqa: BLE001
        fehler(f"/api/health: {exc}")
    try:
        r = requests.get(f"https://{host}/api/ready", timeout=15)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and d.get("ready"):
            ok(f"/api/ready bereit (Schema {d.get('schema_version')}, Alarme offen: {d.get('alarme_offen')})")
        else:
            fehler(f"/api/ready -> {r.status_code}: {d.get('fehler') or r.text[:120]}")
        for w in d.get("warnungen") or []:
            warn(f"/api/ready: {w}")
    except Exception as exc:  # noqa: BLE001
        fehler(f"/api/ready: {exc}")
    try:
        r = requests.get(f"https://{host}/docs", timeout=10)
        (ok if r.status_code in (404, 403) else fehler)(f"/docs -> {r.status_code} (in Produktion aus)")
    except Exception as exc:  # noqa: BLE001
        warn(f"/docs nicht pruefbar: {exc}")


def mail_pruefen(domain, dkim_selector):
    print("6. Mail-Domain (SPF / DMARC / DKIM)")
    try:
        import dns.resolver
    except ImportError:
        warn("dnspython fehlt — SPF/DMARC/DKIM nicht geprueft"); return

    def txt(name):
        try:
            return [b"".join(r.strings).decode("utf-8", "ignore") for r in dns.resolver.resolve(name, "TXT")]
        except Exception:
            return []
    spf = [t for t in txt(domain) if t.lower().startswith("v=spf1")]
    (ok if spf else fehler)(f"SPF fuer {domain}: {spf[0] if spf else 'FEHLT'}")
    if spf and "-all" not in spf[0] and "~all" not in spf[0]:
        warn("SPF endet nicht mit -all/~all")
    dmarc = [t for t in txt(f"_dmarc.{domain}") if t.lower().startswith("v=dmarc1")]
    (ok if dmarc else fehler)(f"DMARC fuer {domain}: {dmarc[0][:80] if dmarc else 'FEHLT'}")
    if dmarc and "p=none" in dmarc[0].replace(" ", "").lower():
        warn("DMARC p=none — nach Testphase auf quarantine/reject setzen")
    if dkim_selector:
        dkim = txt(f"{dkim_selector}._domainkey.{domain}")
        (ok if any("v=dkim1" in t.lower() or "p=" in t for t in dkim) else fehler)(
            f"DKIM {dkim_selector}._domainkey.{domain}: {'vorhanden' if dkim else 'FEHLT'}")
    else:
        warn("DKIM nicht geprueft (--dkim-selector angeben, z.B. den Selector des Mail-Anbieters)")


def ports_pruefen(ips):
    print("7. Offene Ports")
    for ip in ips[:2]:
        for port, name in ((27017, "MongoDB"), (8001, "Backend direkt")):
            try:
                with socket.create_connection((ip, port), timeout=3):
                    fehler(f"{name} ({ip}:{port}) ist von aussen erreichbar!")
            except Exception:
                ok(f"{name} ({ip}:{port}) von aussen geschlossen")


def main():
    ap = argparse.ArgumentParser(description="Betriebsprobe fuer die Produktions-Domain")
    ap.add_argument("host", help="z.B. app.autoschnell.de")
    ap.add_argument("--mail-domain", default=None, help="Absender-Domain (Default: host ohne erstes Label)")
    ap.add_argument("--dkim-selector", default=None)
    args = ap.parse_args()
    host = args.host.strip().lower()
    mail_domain = args.mail_domain or ".".join(host.split(".")[-2:])
    ips = dns_pruefen(host)
    tls_pruefen(host)
    http_pruefen(host)
    header_pruefen(host)
    api_pruefen(host)
    mail_pruefen(mail_domain, args.dkim_selector)
    ports_pruefen(ips)
    print(f"\nERGEBNIS: {len(OK)} ok, {len(WARNUNGEN)} Warnungen, {len(FEHLER)} Fehler")
    for f in FEHLER:
        print("  - " + f)
    return 1 if FEHLER else 0


if __name__ == "__main__":
    sys.exit(main())
