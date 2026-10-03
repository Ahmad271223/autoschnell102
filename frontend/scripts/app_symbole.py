# -*- coding: utf-8 -*-
"""App-Symbole fuer AutoSchnell (installierbare Web-App).

Symbol wie im Logo auf Startseite und Login: Lucide "bolt" (Sechskant mit
Kreis), weiss auf AutoSchnell-Rot #FF3B30.

Aufruf (aus frontend/, braucht Pillow):  python scripts/app_symbole.py public

Erzeugt icon-96/192/512.png (purpose "any": abgerundetes Quadrat, Symbol
62 %), icon-maskable-512.png (vollflaechig, Symbol 56 % -> bleibt im
sicheren Kreis von Android), apple-touch-icon.png (180 px, deckend — iOS
rundet selbst ab), favicon.ico (16/32/48, kraeftigerer Strich) und icon.svg.
Die Groessen prueft e2e/app-installation.spec.js gegen manifest.json.
"""
import math
import os
import sys

from PIL import Image, ImageDraw

ZIEL = sys.argv[1]
ROT = (0xFF, 0x3B, 0x30, 255)
WEISS = (255, 255, 255, 255)
SS = 8  # Supersampling fuer glatte Kanten


def bogen(x1, y1, r, fa, fs, x2, y2, n=24):
    """SVG-Bogen (rx = ry = r, keine Drehung) als Punktfolge ohne Startpunkt."""
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    lam = (dx * dx + dy * dy) / (r * r)
    if lam > 1:
        r *= math.sqrt(lam)
    num = r * r * r * r - r * r * dy * dy - r * r * dx * dx
    den = r * r * dy * dy + r * r * dx * dx
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        co = -co
    cxp, cyp = co * dy, -co * dx
    cx, cy = cxp + (x1 + x2) / 2, cyp + (y1 + y2) / 2
    t1 = math.atan2((dy - cyp) / r, (dx - cxp) / r)
    t2 = math.atan2((-dy - cyp) / r, (-dx - cxp) / r)
    dt = t2 - t1
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    return [(cx + r * math.cos(t1 + dt * i / n), cy + r * math.sin(t1 + dt * i / n))
            for i in range(1, n + 1)]


# Lucide "bolt": M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8
#                a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z
PFAD = [(21, 16), (21, 8)]
PFAD += bogen(21, 8, 2, 0, 0, 20, 6.27)
PFAD += [(13, 2.27)]
PFAD += bogen(13, 2.27, 2, 0, 0, 11, 2.27)
PFAD += [(4, 6.27)]
PFAD += bogen(4, 6.27, 2, 0, 0, 3, 8)
PFAD += [(3, 16)]
PFAD += bogen(3, 16, 2, 0, 0, 4, 17.73)
PFAD += [(11, 21.73)]
PFAD += bogen(11, 21.73, 2, 0, 0, 13, 21.73)
PFAD += [(20, 17.73)]
PFAD += bogen(20, 17.73, 2, 0, 0, 21, 16)


def symbol(groesse, *, voll, glyphe, radius=0.22, strich=1.0):
    s = groesse * SS
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if voll:
        d.rectangle([0, 0, s, s], fill=ROT)
    else:
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * radius), fill=ROT)
    g = s * glyphe
    o = (s - g) / 2
    f = lambda x, y: (o + x / 24 * g, o + y / 24 * g)  # noqa: E731
    w = max(1, round(2 / 24 * g * strich))
    pts = [f(*p) for p in PFAD]
    # Runde Verbindungen wie stroke-linejoin="round": jede Strecke einzeln,
    # an jedem Punkt ein Kreis (joint="curve" hinterliess Haarlinien).
    for a, b in zip(pts, pts[1:] + pts[:1]):
        d.line([a, b], fill=WEISS, width=w)
    for x, y in pts:
        d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=WEISS)
    cx, cy = f(12, 12)
    r = 4 / 24 * g + w / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WEISS, width=w)
    return img.resize((groesse, groesse), Image.LANCZOS)


def speichern(img, name, opak=False):
    pfad = os.path.join(ZIEL, name)
    if opak:
        img = img.convert("RGB")
    img.save(pfad, optimize=True)
    print(f"{name}: {img.size[0]}x{img.size[1]}, {os.path.getsize(pfad)} Bytes")


for g in (96, 192, 512):
    speichern(symbol(g, voll=False, glyphe=0.62), f"icon-{g}.png")
# Maskierbar: Android schneidet Kreis/Tropfen aus -> Symbol im sicheren Bereich
speichern(symbol(512, voll=True, glyphe=0.56), "icon-maskable-512.png")
# iOS rundet selbst ab und verlangt ein deckendes Bild
speichern(symbol(180, voll=True, glyphe=0.6), "apple-touch-icon.png", opak=True)
# Browser-Tab (alte Browser / Lesezeichen): kraeftigerer Strich fuer 16 px
ico = symbol(256, voll=False, glyphe=0.72, radius=0.2, strich=1.3)
ico.save(os.path.join(ZIEL, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
print(f"favicon.ico: {os.path.getsize(os.path.join(ZIEL, 'favicon.ico'))} Bytes")

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<rect width="24" height="24" rx="5" fill="#FF3B30"/>'
    '<g transform="translate(3.36 3.36) scale(0.72)" fill="none" stroke="#fff" '
    'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round">'
    '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8'
    'a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>'
    '<circle cx="12" cy="12" r="4"/></g></svg>\n'
)
with open(os.path.join(ZIEL, "icon.svg"), "w", encoding="utf-8", newline="\n") as fh:
    fh.write(SVG)
print(f"icon.svg: {len(SVG)} Bytes")
