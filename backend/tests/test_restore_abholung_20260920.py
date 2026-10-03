# -*- coding: utf-8 -*-
"""Nachpruefung 20.09.2026, Nr. 75-81 — Restore und Vertrag nach Abholung.

  Nr. 75  Der dokumentierte Restore-Befehl liess Collections stehen, die es
          nur live gibt — genau der Mischstand, den Doku und Dateikopf
          ausschliessen ("nie gemischt").
  Nr. 76  Scheiterte das Verschieben beim exakten Restore, wurde das nur
          gedruckt: Exit 0 und "RESTORE OK", obwohl der verlangte Stand
          nicht erreicht war. Die Liste der Extras wurde zusaetzlich
          pauschal geleert.
  Nr. 77  S3-Objekte wurden VOR dem Umschalten direkt im Live-Eimer
          ueberschrieben. Scheiterte danach etwas, drehte der Rollback
          Datenbank und Ordner zurueck — die S3-Objekte nicht.
  Nr. 78  Das Protokoll wurde auf "final" gesetzt und der Nacharbeits-Merker
          entfernt, BEVOR der Vertrag neu erzeugt wurde; der
          Selbstheilungspfad kannte die Vertragsneuerzeugung gar nicht.
  Nr. 79  Ein Fehler beim Erzeugen der Vertrags-PDF kam als False zurueck —
          der Aufrufer legte den Alarm aber nur bei einer Ausnahme an.
  Nr. 80  Der Nachholjob lud nur Preis und Sondervereinbarung, nicht die
          Vor-Ort-Korrekturen und neuen Schaeden.
  Nr. 81  Dokumentierte .env-Schalter erreichten den Container nicht — und
          mein Waechter von gestern konnte sie nicht sehen.
"""
import inspect
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
WURZEL = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))


def _nur_code(funktion) -> str:
    """Nur ausgefuehrte Zeilen. Die Kommentare und Erklaerungstexte nennen den
    alten Fehler absichtlich beim Namen ("extra = []", ".env.example") —
    sie duerfen einen Test weder bestehen lassen noch durchfallen lassen."""
    import ast
    import textwrap
    baum = ast.parse(textwrap.dedent(inspect.getsource(funktion))).body[0]
    if (baum.body and isinstance(baum.body[0], ast.Expr)
            and isinstance(baum.body[0].value, ast.Constant)
            and isinstance(baum.body[0].value.value, str)):
        baum.body = baum.body[1:]
    return "\n".join(ast.unparse(k) for k in baum.body)


# ---------------------------------------------------------------- Nr. 75
def test_75_exakt_ist_der_standard():
    import restore_mongo as R
    q = inspect.getsource(R.wiederherstellen)
    assert 'exakt = not getattr(args, "zusaetzliche_behalten", False)' in q, (
        "ohne --exakt blieben live-eigene Collections stehen — genau der "
        "Mischstand, den die Doku ausschliesst (Nr. 75)")
    argumente = inspect.getsource(R.main)
    assert "--zusaetzliche-behalten" in argumente, "es braucht einen Ausweg"
    assert "gemischt" in argumente.lower(), "und der muss benannt sein"


def test_75b_der_mischstand_wird_laut_benannt():
    import restore_mongo as R
    q = inspect.getsource(R.wiederherstellen)
    stelle = q.split("if extra and not exakt:")[1][:600]
    assert "ACHTUNG" in stelle and "GEMISCHT" in stelle, (
        "wer den Mischstand waehlt, darf das nicht als beilaeufigen "
        "'Hinweis' praesentiert bekommen")


def test_75c_die_zusage_stimmt_jetzt_auch_in_der_doku():
    for datei, marke in ((WURZEL / "DEPLOYMENT.md", "Nr. 75"),
                         (BACKEND / "scripts" / "restore_mongo.py", "Nr. 75")):
        text = datei.read_text(encoding="utf-8")
        stelle = text[text.index("nie gemischt"):][:1400]
        assert marke in stelle, f"{datei.name}: die Einschraenkung fehlt"
        assert "zusaetzliche-behalten" in stelle


# ---------------------------------------------------------------- Nr. 76
def test_76_gescheitertes_verschieben_ist_kein_restore_ok():
    import restore_mongo as R
    q = inspect.getsource(R.wiederherstellen)
    assert "exakt_fehler" in q
    # Der Fehlschlag wird gesammelt, nicht nur gedruckt:
    assert "exakt_fehler.append" in q
    # ... und er beendet den Lauf VOR der Erfolgsmeldung.
    fehlerstelle = q.index("if exakt_fehler:")
    erfolg = q.index('print(f"RESTORE OK:')
    assert fehlerstelle < erfolg, "der Fehler muss vor RESTORE OK greifen"
    nach = q[fehlerstelle:erfolg]
    assert "return 1" in nach, "und mit Exit 1 enden"
    assert "RESTORE UNVOLLSTAENDIG" in nach


def test_76b_die_extras_werden_nicht_mehr_pauschal_geleert():
    import restore_mongo as R
    q = _nur_code(R.wiederherstellen)
    assert "extra = []" not in q, (
        "das leerte die Liste auch dann, wenn das Verschieben gescheitert "
        "war — der Betreiber sah nichts mehr davon (Nr. 76)")
    assert "nicht_bewegt" in q, "nur wirklich Verschobenes faellt raus"


# ---------------------------------------------------------------- Nr. 77
def test_77_s3_wird_vor_dem_ueberschreiben_gesichert():
    import restore_mongo as R
    q = inspect.getsource(R.s3_zurueckspielen)
    assert "copy_object" in q and "head_object" in q, (
        "der bisherige Stand muss vor dem Ueberschreiben gesichert werden "
        "(Nr. 77)")
    # Sichern VOR dem Hochladen:
    assert q.index("copy_object") < q.index("upload_file")
    assert R.S3_RUECKNAHME_PREFIX.endswith("/")


def test_77b_der_rollback_dreht_auch_s3_zurueck():
    import restore_mongo as R
    q = inspect.getsource(R._rollback)
    assert "s3_zuruecknehmen(s3_gesichert, stamp)" in q, (
        "sonst zeigt die zurueckgedrehte Datenbank auf zurueckgespielte "
        "Dateien (Nr. 77)")
    haupt = inspect.getsource(R.wiederherstellen)
    assert "s3_gesichert, stamp)" in haupt, "die Liste muss ankommen"
    # Auch wenn das Hochladen selbst mittendrin scheitert:
    stelle = haupt.split("FEHLER beim Zurueckspielen nach S3")[1][:700]
    assert "s3_zuruecknehmen(s3_gesichert, stamp)" in stelle


def test_77c_die_kopien_verschwinden_erst_nach_erfolg():
    import restore_mongo as R
    q = inspect.getsource(R.wiederherstellen)
    aufraeumen = q.index("s3_sicherung_aufraeumen(")
    erfolg = q.index('print(f"RESTORE OK:')
    fehler = q.index("if exakt_fehler:")
    assert fehler < aufraeumen < erfolg, (
        "vor einem spaeten Fehlschlag duerfen die Ruecknahme-Kopien nicht "
        "weg sein")


def test_77d_zuruecknehmen_unterscheidet_neu_und_ueberschrieben():
    """Objekte, die es vorher gab, kommen zurueck; neu angelegte fliegen."""
    import restore_mongo as R
    q = inspect.getsource(R.s3_zuruecknehmen)
    assert "if gab_es:" in q and "copy_object" in q and "delete_object" in q
    assert "fehler.append" in q, "Fehlschlaege werden gemeldet, nicht verschluckt"


# ---------------------------------------------------------------- Nr. 78
def test_78_merker_faellt_erst_nach_der_vertragsneuerzeugung():
    import routes.protocols as P
    q = inspect.getsource(P)
    block = q.split("Wunsch Ahmad 14.09.2026: Der Kaufvertrag wird abschliessend")[1]
    block = block[:2000]
    vertrag = block.index("vertrag_nach_abholung_aktualisieren(")
    merker = block.index("_nacharbeit_erledigt(")
    assert vertrag < merker, (
        "der Merker wurde VOR der Vertragsneuerzeugung entfernt — starb der "
        "Prozess dazwischen, fehlte die neue Fassung fuer immer (Nr. 78)")


def test_78b_die_selbstheilung_erzeugt_den_vertrag_mit():
    import routes.protocols as P
    q = inspect.getsource(P)
    heilung = q.split("auch den nachverhandelten Preis nachziehen")[1][:2200]
    assert "vertrag_nach_abholung_aktualisieren(" in heilung, (
        "der Selbstheilungspfad heilte Termin, Preis und Lebenszyklus — "
        "ausgerechnet die Vertragsneuerzeugung fehlte (Nr. 78)")
    assert "protokoll_korrekturen(appt, doc)" in heilung, \
        "und zwar MIT Korrekturen und neuen Schaeden"
    assert heilung.index("vertrag_nach_abholung_aktualisieren(") < \
        heilung.index("_nacharbeit_erledigt("), "Merker auch hier zuletzt"


# ---------------------------------------------------------------- Nr. 79
def test_79_ein_false_legt_denselben_alarm_wie_eine_ausnahme():
    import routes.protocols as P
    q = inspect.getsource(P.vertrag_nach_abholung_aktualisieren)
    assert q.count('betrieb.alarm(db, "vertrag_nach_abholung_offen"') == 2, (
        "regenerate_contract_for_pickup faengt den PDF-Fehler selbst ab und "
        "liefert nur False — das muss denselben Alarm ausloesen wie eine "
        "durchgereichte Ausnahme (Nr. 79)")
    assert "lieferte False" in q


def test_79b_der_stille_weg_ist_wirklich_vorhanden():
    """Gegenprobe: liefert regenerate_contract_for_pickup bei einem
    PDF-Fehler tatsaechlich False statt zu werfen?"""
    import routes.contracts as C
    q = inspect.getsource(C.regenerate_contract_for_pickup)
    # (22.09.: der except-Zweig traegt seit P-16 einen Betriebsalarm, daher
    # ein groesseres Fenster)
    stelle = q.split("_pdfs_erzeugen")[1][:1500]
    assert "except Exception:" in stelle and "return False" in stelle


def test_79c_nichts_zu_tun_loest_keinen_alarm_aus():
    """Wichtig: kein Alarm, wenn es schlicht nichts zu aendern gab."""
    import routes.protocols as P
    q = inspect.getsource(P.vertrag_nach_abholung_aktualisieren)
    kopf = q[:q.index("try:")]
    assert "return False" in kopf, "der Fall steigt VOR dem try aus"
    assert "betrieb.alarm" not in kopf


# ---------------------------------------------------------------- Nr. 80
def test_80_der_nachholer_nimmt_korrekturen_und_schaeden_mit():
    import cleanup_service as CS
    q = inspect.getsource(CS.vertrag_nach_abholung_nachholen)
    assert "protokoll_korrekturen(appt, p)" in q, (
        "ohne Korrekturen entstand eine unvollstaendige Fassung — und bei "
        "reinen Korrekturen gar keine (Nr. 80)")
    assert "korrekturen=korrekturen" in q and "neue_schaeden=neue_schaeden" in q
    # Das ganze Protokoll laden, nicht nur zwei Felder:
    assert '{"id": protokoll_id}, {"_id": 0}' in q, \
        "die Teilprojektion liess vehicle_check/new_damages weg"


def test_80b_derselbe_weg_wie_im_normalpfad():
    """Gegenprobe: Nachholer und Normalpfad berechnen dasselbe."""
    import cleanup_service as CS
    import routes.protocols as P
    nachholer = inspect.getsource(CS.vertrag_nach_abholung_nachholen)
    normal = inspect.getsource(P)
    for stueck in ("protokoll_korrekturen(", "korrekturen=korrekturen",
                   "neue_schaeden=neue_schaeden"):
        assert stueck in nachholer and stueck in normal, stueck


# ---------------------------------------------------------------- Nr. 81
def test_81_die_vier_schalter_erreichen_den_container():
    from tests.test_haertung_20260919 import _compose_umgebung
    umgebung = _compose_umgebung()
    for name in ("BEWEIS_AUTOMATISCH", "BILD_PROXY_HOSTS",
                 "VERTRAG_LINK_TAGE", "MOBILE_API_BASE"):
        assert name in umgebung, f"{name} erreicht den Container nicht (Nr. 81)"


def test_81b_der_waechter_sucht_jetzt_vom_code_aus():
    from tests import test_haertung_20260919 as H
    q = _nur_code(H.test_09_alles_was_der_code_liest_erreicht_den_container)
    assert "_vom_code_gelesen()" in q
    assert ".env.example" not in q, (
        "die alte Fassung verglich die Schnittmenge mit .env.example — "
        "Namen, die dort fehlen, konnte sie grundsaetzlich nicht sehen")


# ------------------------------------------------- Betriebsprobe (Ausgabe)
def test_betriebsprobe_ohne_abgekuendigte_aufrufe():
    """Ahmads Ausgabe hatte drei DeprecationWarnings mitten im Bericht."""
    q = (BACKEND / "scripts" / "betriebsprobe.py").read_text(encoding="utf-8")
    # Nur ausgefuehrte Zeilen — die Kommentare nennen die alten Aufrufe
    # absichtlich, damit klar bleibt, warum das hier steht.
    code = "\n".join(z.split("#", 1)[0] for z in q.splitlines())
    assert "utcnow()" not in code, "datetime.utcnow() ist abgekuendigt"
    assert "PROTOCOL_TLSv1" not in code, "ssl.PROTOCOL_TLSv1 ist abgekuendigt"
    assert "simplefilter(\"ignore\", DeprecationWarning)" in q, (
        "ssl.TLSVersion.TLSv1 ist ebenfalls abgekuendigt — dort wird die "
        "Warnung gezielt und mit Begruendung unterdrueckt")


def test_betriebsprobe_trennt_client_und_server():
    """Nebenbefund: scheiterte schon der eigene Client, meldete die Probe
    'Server lehnt ab' — geprueft war in Wahrheit nichts."""
    q = (BACKEND / "scripts" / "betriebsprobe.py").read_text(encoding="utf-8")
    stelle = q.split("Alte Protokolle muessen abgelehnt werden")[1][:2000]
    assert "nicht pruefbar" in stelle
    assert stelle.index("except Exception as exc") < stelle.index("create_connection"), \
        "erst der Kontext, dann der Handschlag — je mit eigenem Fehlerweg"
