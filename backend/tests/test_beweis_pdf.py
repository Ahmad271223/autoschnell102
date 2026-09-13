# -*- coding: utf-8 -*-
"""Beweisdokument je Inserat (ersetzt die Snapshots, 10.09.2026): der
Renderer ohne Netz und ohne Datenbank."""
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfReader

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import beweis_pdf as B  # noqa: E402


@pytest.fixture(autouse=True)
def _ohne_logo_dateien(tmp_path_factory, monkeypatch):
    """Standard: leerer Logo-Ordner — die echten Dateien unter
    backend/assets/logos/ wuerden sonst Bildzaehlungen verfaelschen.
    test_13 prueft die echten Dateien ausdruecklich."""
    monkeypatch.setattr(B, "LOGO_ORDNER", tmp_path_factory.mktemp("logos_leer"))


def _foto(farbe=(30, 90, 160), w=900, h=600) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), farbe).save(b, "JPEG", quality=70)
    return b.getvalue()


BASIS = dict(
    make_label="Volkswagen", model_label="Golf",
    model_description="Golf VII 1.4 TSI Highline", first_registration="03/2017",
    mileage=123456, fuel_label="Benzin", gearbox_label="Automatik", power_kw=110,
    power_ps=150, color="Grau", previous_owners="2", hu="05/2027",
    features=["Klimaautomatik", "Navigationssystem", "Sitzheizung"],
    description="Gepflegt.\nScheckheft.\n\nPreis VB – Tausch möglich.",
    list_price=14990.0, accident_damaged=False, roadworthy=True,
)
ZEIT = datetime(2026, 9, 10, 13, 5, 12, tzinfo=timezone.utc)


def _pdf(quelle="mobile", daten=None, url="https://suchen.mobile.de/fahrzeuge/details.html?id=412345678",
         item_id="412345678", fotos=None, foto_urls=None, **extra):
    fotos = [_foto(), _foto((160, 40, 40))] if fotos is None else fotos
    foto_urls = ([f"https://img.classistatic.de/api/v1/mo-prod/images/{i}" for i in range(len(fotos))]
                 if foto_urls is None else foto_urls)
    return B.beweis_pdf(quelle=quelle, daten=daten or dict(BASIS, mobile_ad_id=item_id),
                        url=url, item_id=item_id, beweis_id="3f2a9c11-aaaa", abgerufen_am=ZEIT,
                        erstellt_am=ZEIT, fotos=fotos, foto_urls=foto_urls, **extra)


def _text(pdf: bytes) -> str:
    return "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)


@pytest.mark.parametrize("quelle,name", [("mobile", "mobile.de"), ("autoscout24", "AutoScout24"),
                                         ("kleinanzeigen", "Kleinanzeigen")])
def test_01_alle_portale_mit_kennzeichnung_id_und_url(quelle, name):
    url = f"https://www.example-{quelle}.de/inserat/4711"
    pdf = _pdf(quelle=quelle, url=url, item_id="4711", daten=dict(BASIS, mobile_ad_id="4711"))
    assert pdf.startswith(b"%PDF")
    t = _text(pdf)
    assert "BEWEISDOKUMENT" in t and name in t
    assert "Anzeigen-ID 4711" in t
    assert url in t.replace("\n", "")
    assert f"Kein Dokument von {name}" in t.replace("\n", " ")


def test_02_daten_geordnet_und_zeit_in_berlin():
    t = _text(_pdf())
    for label in ("Fahrzeugdaten laut Inserat", "Marke", "Kilometerstand", "123.456 km",
                  "110 kW (150 PS)", "Ausstattung laut Inserat (3)", "Beschreibung laut Inserat",
                  "Anbieter laut Inserat", "14.990 €"):
        assert label in t, label
    assert "10.09.2026, 15:05:12 Uhr" in t   # 13:05 UTC = 15:05 Sommerzeit


def test_03_fotos_eingebettet_und_fehlende_benannt():
    # Verschiedene Farben: gleiche Bytes legt das PDF nur einmal ab.
    pdf = _pdf(fotos=[_foto(), None, _foto((20, 160, 60))],
               foto_urls=[f"https://img.kleinanzeigen.de/{i}" for i in range(5)])
    r = PdfReader(io.BytesIO(pdf))
    bilder = sum(len(p.images) for p in r.pages)
    assert bilder == 2
    t = _text(pdf)
    assert "2 von 5 Inseratsfotos eingebettet" in t
    assert "Foto 2 konnte nicht geladen werden" in t
    assert "Anhang: Adressen der Inseratsfotos" in t and "https://img.kleinanzeigen.de/4" in t


def test_04_ohne_fotos_ehrlicher_hinweis():
    assert "keine Fotos" in _text(_pdf(fotos=[], foto_urls=[]))
    t = _text(_pdf(fotos=[None, None], foto_urls=["https://img.kleinanzeigen.de/a",
                                                  "https://img.kleinanzeigen.de/b"]))
    assert "nicht vom Portal geladen werden" in t


def test_05_kleinanzeigen_behauptet_nichts_zu_unfall():
    """Der Kleinanzeigen-Parser setzt accident_damaged/roadworthy als
    Platzhalter — das Beweisdokument darf daraus keine Aussage machen."""
    t = _text(_pdf(quelle="kleinanzeigen", daten=dict(BASIS, mobile_ad_id="1",
                                                        accident_damaged=False)))
    assert "Unfall" not in t and "fahrbereit" not in t
    t2 = _text(_pdf(daten=dict(BASIS, mobile_ad_id="1", accident_damaged=True,
                               roadworthy=False)))
    assert "Unfallschaden laut Inserat" in t2 and "nicht fahrbereit laut Inserat" in t2


def test_06_privatanbieter_ohne_name_und_telefon():
    privat = dict(BASIS, mobile_ad_id="1", seller_type="privat", seller_name="Max Mustermann",
                  seller_address="Nebenweg 3", seller_zip="80331", seller_city="München",
                  seller_phone="0170 1234567")
    t = _text(_pdf(quelle="autoscout24", daten=privat))
    assert "Privatanbieter" in t and "80331 München" in t
    assert "Mustermann" not in t and "0170" not in t and "Nebenweg" not in t
    t_voll = _text(_pdf(quelle="autoscout24", daten=privat, privatdaten=True))
    assert "Max Mustermann" in t_voll and "0170 1234567" in t_voll


def test_07_haendler_mit_allen_angaben():
    t = _text(_pdf(daten=dict(BASIS, mobile_ad_id="1", seller_type="haendler",
                              seller_name="Autohaus Muster GmbH", seller_address="Hauptstr. 1",
                              seller_zip="97070", seller_city="Würzburg",
                              seller_phone="+49 931 123456")))
    assert "gewerblicher Anbieter" in t and "Autohaus Muster GmbH" in t
    assert "Hauptstr. 1" in t and "+49 931 123456" in t


def test_08_weitere_angaben_und_robust_gegen_sonderzeichen():
    daten = dict(BASIS, mobile_ad_id="1", price_label="14.990 € VB", sonderwert="abc",
                 _intern="nie zeigen", liste=["a", "b"], kaputt=object(),
                 description="Emoji 😀✅ weg​; Łódź bleibt; <b>kein HTML</b> & mehr " * 400)
    pdf = _pdf(quelle="kleinanzeigen", daten=daten)
    t = _text(pdf)
    assert "Weitere ausgelesene Angaben" in t and "sonderwert" in t and "a, b" in t
    assert "nie zeigen" not in t
    assert "😀" not in t and "Łódź" in t and "<b>kein HTML</b>" in t
    assert len(PdfReader(io.BytesIO(pdf)).pages) >= 3   # lange Beschreibung umbricht


def test_09_leere_daten_brechen_nicht():
    pdf = B.beweis_pdf(quelle="mobile", daten={}, url="", item_id="", beweis_id="",
                       abgerufen_am=None, erstellt_am=None, fotos=[], foto_urls=[])
    t = _text(pdf)
    assert "keine Angabe" in t and "unbekannt" in t


def test_10_logo_datei_ersetzt_schriftzug(tmp_path, monkeypatch):
    ohne = _pdf(fotos=[], foto_urls=[])
    assert sum(len(p.images) for p in PdfReader(io.BytesIO(ohne)).pages) == 0
    Image.new("RGB", (400, 100), (242, 107, 33)).save(tmp_path / "mobile.png")
    monkeypatch.setattr(B, "LOGO_ORDNER", tmp_path)
    mit = _pdf(fotos=[], foto_urls=[])
    assert all(len(p.images) == 1 for p in PdfReader(io.BytesIO(mit)).pages)
    # Unlesbare Datei: zurueck zum Schriftzug statt Absturz.
    (tmp_path / "mobile.png").write_bytes(b"kein bild")
    assert _pdf(fotos=[], foto_urls=[]).startswith(b"%PDF")


def test_11_zeit_ohne_tzdata(monkeypatch):
    import zoneinfo

    def _kaputt(*a, **k):
        raise zoneinfo.ZoneInfoNotFoundError("fehlt")

    monkeypatch.setattr(zoneinfo, "ZoneInfo", _kaputt)
    assert B.zeit_text(datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)).startswith("01.07.2026, 12:00")
    assert B.zeit_text(datetime(2026, 12, 1, 10, 0, tzinfo=timezone.utc)).startswith("01.12.2026, 11:00")


def test_12_autoscout_zeigt_beide_kennungen():
    t = _text(_pdf(quelle="autoscout24", item_id="0f9e8d7c-6b5a",
                   daten=dict(BASIS, mobile_ad_id="a1b2c3-uniq")))
    assert "a1b2c3-uniq" in t and "Kennung in der Adresse" in t and "0f9e8d7c-6b5a" in t


def test_13_echte_portal_logos_sind_gueltig_und_klein_genug():
    """mobile.de und AutoScout24 kommen aus den Vergleichs-Buttons
    (frontend/public/logos); jede Datei muss lesbar und unter der Grenze sein."""
    from pathlib import Path
    ordner = Path(B.__file__).resolve().parent / "assets" / "logos"
    for quelle in ("mobile", "autoscout24", "kleinanzeigen"):
        datei = ordner / f"{quelle}.png"
        assert datei.is_file(), datei
        assert 0 < datei.stat().st_size <= B.LOGO_MAX_BYTES
        with Image.open(datei) as im:
            assert im.width > im.height * 1.2 and im.height >= 100, im.size


def test_14_echtes_logo_landet_im_kopf(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(B, "LOGO_ORDNER", Path(B.__file__).resolve().parent / "assets" / "logos")
    pdf = _pdf(quelle="autoscout24", fotos=[], foto_urls=[])
    assert all(len(p.images) == 1 for p in PdfReader(io.BytesIO(pdf)).pages)


# ------------------------------------------------------------ Gegenpruefung 10.09.2026
def _uris(pdf: bytes):
    aus = []
    for p in PdfReader(io.BytesIO(pdf)).pages:
        for a in p.get("/Annots") or []:
            obj = a.get_object()
            if obj.get("/A") and obj["/A"].get("/URI"):
                aus.append(str(obj["/A"]["/URI"]))
    return aus


def test_15_hochkomma_in_der_url_lenkt_den_link_nicht_um():
    url = "https://suchen.mobile.de/fahrzeuge/details.html?id=1&x=' href='https://phishing.example/"
    uris = _uris(_pdf(url=url, fotos=[], foto_urls=[]))
    assert uris and not any(u.startswith("https://phishing.example") for u in uris), uris


def test_16_kontaktdaten_privater_anbieter_werden_unkenntlich():
    text = ("Privatverkauf, Anruf bei Max 0171 2223334 oder +49 (171) 222-3335, "
            "Mail max.muster@example.de. Baujahr 2015-2019, 123.456 km, 14.990 Euro VB.")
    daten = dict(BASIS, mobile_ad_id="1", seller_type="privat", description=text,
                 model_description="Golf, Tel. 0151/98765432")
    t = _text(_pdf(quelle="kleinanzeigen", daten=daten)).replace("\n", " ")
    for weg in ("2223334", "222-3335", "max.muster@example.de", "98765432"):
        assert weg not in t, weg
    assert B.KONTAKT_ENTFERNT in t and "2015-2019" in t and "123.456 km" in t and "14.990" in t
    assert "unkenntlich gemacht" in t
    haendler = _text(_pdf(daten=dict(daten, seller_type="haendler"))).replace("\n", " ")
    assert "0171 2223334" in haendler, "gewerbliche Angaben bleiben vollstaendig"


def test_17_autoscout_ersatzname_haendler_macht_niemanden_gewerblich():
    daten = dict(BASIS, mobile_ad_id="1", seller_name="Händler", seller_phone="0170 9999999",
                 seller_address="Privatweg 12", seller_zip="80331", seller_city="München")
    t = _text(_pdf(quelle="autoscout24", daten=daten))
    assert "0170 9999999" not in t and "Privatweg" not in t and "gewerblicher Anbieter" not in t
    assert B.verkaeufer_art(daten, "autoscout24") is None
    assert B.verkaeufer_art(daten, "mobile") == "haendler", "mobile.de-Altdaten: Ersatzname nur bei gewerblich"


def test_18_fotos_binaer_eingebettet():
    pdf = _pdf()
    assert b"/ASCII85Decode" not in pdf
    from reportlab import rl_config
    assert rl_config.useA85 == 1, "Einstellung wird nach dem Aufbau zurueckgesetzt"
