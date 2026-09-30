# -*- coding: utf-8 -*-
"""Haertungen aus den Prueflisten vom 30.09.2026 (alle klein, vor dem Go-Live):

  1. Firma ohne Chef-Konto ist gesperrt (vorher "nicht gesperrt": ihre Sucher arbeiteten weiter).
  2. Das Start-Audit der Firmen- und Fahrerloeschung wirft wieder — ohne die Spur "wer hat geloescht"
     beginnt keine Loeschung (seit f883473 stand dort versehentlich die schluckende Variante).
  3. Freitext-Schaeden in den dauerhaften Auto-Daten: in Produktion nie, egal was in der .env steht.
  4. Rollout: falscher Branch startet nicht; optional ein erwarteter Commit.
  (Der Legacy-Rueckfall der Fahrzeug-ID: tests/test_befunde_runde17_bestand.py, test_12.)
"""
import asyncio

import pytest

import auto_daten
import deps
import routes.kundenportal as KP
import test_rollout as TR

from test_golive_20260914_konten import SA, SUF, _db, _firma_anlegen, aufraeumen  # noqa: F401
from test_kundenportal_20260929 import _fehler, _lauf, welt  # noqa: F401


# ------------------------------------------------------------------ 1) Firma ohne Chef
def test_firma_ohne_chef_ist_gesperrt(welt):  # noqa: F811
    w = welt
    _lauf(KP.put_webseite(KP.WebseiteIn(slug="kfz-mueller", aktiv=True), user=w.chef))
    assert _lauf(deps.firma_gesperrt(w.dealer_id)) is False
    assert w.dealer_id not in _lauf(deps.gesperrte_firmen_ids())
    assert _lauf(KP.public_firma(slug="kfz-mueller"))["firma"] == "KFZ Müller GmbH"
    # Chef gesperrt -> Firma gesperrt (wie bisher)
    w.run(w.db.users.update_one({"id": w.chef["id"]}, {"$set": {"active": False}}))
    assert _lauf(deps.firma_gesperrt(w.dealer_id)) is True
    w.run(w.db.users.update_one({"id": w.chef["id"]}, {"$set": {"active": True}}))
    # Chef-Konto fehlt ganz (Zeiger zeigt ins Leere, kein anderes dealer-Konto): jetzt gesperrt
    w.run(w.db.users.delete_one({"id": w.chef["id"]}))
    assert _lauf(deps.firma_gesperrt(w.dealer_id)) is True
    assert w.dealer_id in _lauf(deps.gesperrte_firmen_ids())
    assert _fehler(KP.public_firma(slug="kfz-mueller")).status_code == 404
    # auch ohne Zeiger
    w.run(w.db.dealers.update_one({"id": w.dealer_id}, {"$unset": {"user_id": ""}}))
    assert _lauf(deps.firma_gesperrt(w.dealer_id)) is True
    assert w.dealer_id in _lauf(deps.gesperrte_firmen_ids())
    # ein (anderes) aktives dealer-Konto der Firma hebt die Sperre auf — aeltestes Konto gilt
    w.run(w.db.users.insert_one({"id": f"chef2-{w.s}", "dealer_id": w.dealer_id, "role": "dealer", "active": True,
                                 "created_at": "2026-02-01T00:00:00+00:00"}))
    assert _lauf(deps.firma_gesperrt(w.dealer_id)) is False
    assert w.dealer_id not in _lauf(deps.gesperrte_firmen_ids())
    # ohne Firmen-ID (Kaeufer) bleibt es "nicht gesperrt"; ganz ohne Firmen-Dokument entscheidet
    # current_firma (403 "Kein Haendlerprofil") — firma_gesperrt meldet dafuer nichts
    assert _lauf(deps.firma_gesperrt(None)) is False
    assert _lauf(deps.firma_gesperrt(f"gibt-es-nicht-{w.s}")) is False


# ------------------------------------------------------------------ 2) Start-Audit zwingend
def _audit_scheitert_bei(monkeypatch, modul, aktion, *weitere_module):
    """Liefert einen Schalter: solange schalter["an"], scheitert GENAU dieser Audit-Eintrag.
    Die Module arbeiten dabei sicher auf der echten Datenbank (deps.db) — wurde eines von ihnen zum
    ersten Mal waehrend einer Fixture mit Wegwerf-Datenbank importiert, zeigt sein `db` sonst dorthin."""
    echt = deps.log_activity
    schalter = {"an": True}

    async def audit(dealer_id, user_id, action, ref=None, meta=None):
        if schalter["an"] and action == aktion:
            raise RuntimeError("Audit-Schreiben gescheitert")
        return await echt(dealer_id, user_id, action, ref=ref, meta=meta)
    monkeypatch.setattr(deps, "log_activity", audit)
    monkeypatch.setattr(modul, "log_activity", audit)
    for m in (modul, *weitere_module):
        monkeypatch.setattr(m, "db", deps.db)
    return schalter


def test_firmenloeschung_beginnt_nicht_ohne_start_audit(aufraeumen, monkeypatch):  # noqa: F811
    import routes.admin as a
    dbx = _db()
    dealer_id, chef_id, sucher_id = _firma_anlegen(dbx, "startaudit")
    schalter = _audit_scheitert_bei(monkeypatch, a, "admin.firma.loeschung.gestartet")
    with pytest.raises(RuntimeError, match="Audit-Schreiben gescheitert"):
        asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))
    # nichts angefasst: kein Grabstein, Konten aktiv und angemeldet, Firma da
    firma = dbx.dealers.find_one({"id": dealer_id})
    assert firma is not None and "loeschung" not in firma, firma
    konten = list(dbx.users.find({"dealer_id": dealer_id}, {"_id": 0, "id": 1, "active": 1, "current_session_id": 1}))
    assert {k["id"] for k in konten} == {chef_id, sucher_id}
    assert all(k["active"] is True and k["current_session_id"] for k in konten), konten
    assert dbx.activity_logs.count_documents({"ref": dealer_id}) == 0
    # mit funktionierendem Audit laeuft die Loeschung normal durch
    schalter["an"] = False
    erg = asyncio.run(a.admin_delete_user(chef_id, firma_loeschen=True, admin=SA))
    assert erg["ok"] is True and dbx.dealers.count_documents({"id": dealer_id}) == 0
    assert dbx.activity_logs.count_documents({"action": "admin.firma.loeschung.gestartet", "ref": dealer_id}) == 1


def test_fahrerloeschung_pseudonymisiert_nicht_ohne_start_audit(monkeypatch):
    import routes.admin as a
    import routes.drivers as DR
    dbx = _db()
    did = f"hart_drv_{SUF}"
    dbx.driver_accounts.insert_one({"id": did, "email": f"{did}@e2etest-mail.de", "driver_code": f"FH{SUF[:4].upper()}",
                                    "active": True, "current_session_id": "sitz", "password_hash": "x",
                                    "display_name": "Hart Fahrer", "created_at": "2026-09-01T00:00:00+00:00"})
    gerufen = []
    echt = DR.fahrer_konto_anonymisieren

    async def spion(db, driver_id):
        gerufen.append(driver_id)
        return await echt(db, driver_id)
    try:
        monkeypatch.setattr(DR, "fahrer_konto_anonymisieren", spion)
        schalter = _audit_scheitert_bei(monkeypatch, a, "admin.fahrer.loeschung.gestartet", DR)
        with pytest.raises(RuntimeError, match="Audit-Schreiben gescheitert"):
            asyncio.run(a.admin_delete_driver(did, admin=SA))
        d = dbx.driver_accounts.find_one({"id": did}, {"_id": 0})
        assert gerufen == [], "ohne Start-Audit darf nichts pseudonymisiert werden"
        assert d is not None and d["display_name"] == "Hart Fahrer"
        assert d["active"] is False and (d.get("loeschung") or {}).get("status") == "laeuft"   # gesperrt, wiederaufnehmbar
        # Wiederholung mit funktionierendem Audit fuehrt zu Ende
        schalter["an"] = False
        assert asyncio.run(a.admin_delete_driver(did, admin=SA))["ok"] is True
        assert gerufen == [did] and dbx.driver_accounts.count_documents({"id": did}) == 0
    finally:
        dbx.driver_accounts.delete_many({"id": did})
        dbx.activity_logs.delete_many({"ref": did})


# ------------------------------------------------------------------ 3) Freitext-Schalter
@pytest.mark.parametrize("app_env,schalter,erwartet", [
    ("production", "true", False), ("Production", "1", False), ("production", "", False),
    ("development", "true", True), ("", "ja", True), ("", "false", False), ("", "", False)])
def test_freitext_schaeden_in_produktion_nie(monkeypatch, app_env, schalter, erwartet):
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("AUTO_DATEN_SCHAEDEN_FREITEXT", schalter)
    assert auto_daten.schaeden_freitext_erlaubt() is erwartet


# ------------------------------------------------------------------ 4) Rollout: Branch und erwarteter Commit
_GIT_MIT_BRANCH = """#!/bin/sh
echo "git $*" >> "$FAKE_LOG"
case "$*" in
  "rev-parse --abbrev-ref HEAD") echo "$FAKE_BRANCH" ;;
  "rev-parse HEAD") echo "$FAKE_HEAD" ;;
esac
exit 0
"""
GOLIVE = "feature/plattform-ausbau-2026-08"


def _rollout(tmp_path, monkeypatch, *, branch, marke=None, **umgebung):
    monkeypatch.setattr(TR, "_FAKE_GIT", _GIT_MIT_BRANCH)
    datei = tmp_path / "checkout" / "deploy" / ".rollout-branch"
    datei.parent.mkdir(parents=True, exist_ok=True)
    if marke is not None:
        datei.write_text(marke + "\n", encoding="utf-8", newline="\n")
    rc, out, im_drain, aufrufe = TR._skript_lauf(
        tmp_path, "rollout.sh", extra_env={"FAKE_BRANCH": branch, "FAKE_HEAD": "6c2a24c0ffee1234", **umgebung})
    return rc, out, im_drain, aufrufe, (datei.read_text(encoding="utf-8").strip() if datei.exists() else None)


@pytest.mark.skipif(not TR._SH, reason="kein sh vorhanden")
def test_rollout_falscher_branch_startet_nicht(tmp_path, monkeypatch):
    rc, out, im_drain, aufrufe, marke = _rollout(tmp_path, monkeypatch, branch="main", marke=GOLIVE)
    assert rc == 2, out
    assert "ausgecheckt ist der Branch 'main'" in out and GOLIVE in out and "NICHT gestartet" in out
    assert "BRANCH_WECHSEL=1" in out
    assert not im_drain, "vor dem Drain abgebrochen — der Server bleibt in der Rotation"
    assert not any("docker" in a for a in aufrufe) and not any("pull" in a for a in aufrufe), aufrufe
    assert marke == GOLIVE


@pytest.mark.skipif(not TR._SH, reason="kein sh vorhanden")
def test_rollout_bewusster_branchwechsel_und_erste_marke(tmp_path, monkeypatch):
    rc, out, im_drain, aufrufe, marke = _rollout(tmp_path, monkeypatch, branch="main", marke=GOLIVE, BRANCH_WECHSEL="1")
    assert rc == 0 and "FERTIG" in out and not im_drain, out
    assert marke == "main", "der neue Branch ist nach dem erfolgreichen Rollout gemerkt"
    # derselbe Branch wie zuletzt: laeuft ohne Rueckfrage
    rc, out, _d, _a, marke = _rollout(tmp_path / "zwei", monkeypatch, branch=GOLIVE, marke=GOLIVE)
    assert rc == 0 and "Branch: " + GOLIVE in out and marke == GOLIVE, out
    # noch keine Marke (erstes Rollout mit dieser Fassung): laeuft und merkt sich den Branch
    rc, out, _d, _a, marke = _rollout(tmp_path / "drei", monkeypatch, branch=GOLIVE)
    assert rc == 0 and marke == GOLIVE, out


@pytest.mark.skipif(not TR._SH, reason="kein sh vorhanden")
def test_rollout_erwarteter_commit(tmp_path, monkeypatch):
    rc, out, im_drain, aufrufe, _m = _rollout(tmp_path, monkeypatch, branch=GOLIVE, ERWARTET="abc1234")
    assert rc != 0 and "erwartet war 'abc1234'" in out and "es wird nichts gebaut" in out, out
    assert im_drain and "BLEIBT im Drain" in out
    assert not any("up -d --build" in a for a in aufrufe), aufrufe
    rc, out, im_drain, aufrufe, _m = _rollout(tmp_path / "zwei", monkeypatch, branch=GOLIVE, ERWARTET="6c2a24c")
    assert rc == 0 and "Commit wie erwartet: 6c2a24c" in out and "FERTIG" in out and not im_drain, out


@pytest.mark.skipif(not TR._SH, reason="kein sh vorhanden")
def test_rollout_ohne_git_auskunft_laeuft_wie_bisher(tmp_path):
    # die Standard-Attrappe antwortet auf rev-parse nicht: keine Pruefung, keine Marke, kein Abbruch
    rc, out, im_drain, _a = TR._skript_lauf(tmp_path, "rollout.sh")
    assert rc == 0 and "FERTIG" in out and not im_drain, out
    assert not (tmp_path / "checkout" / "deploy" / ".rollout-branch").exists()


# ------------------------------------------------------------------ 5) Kleinanzeigen-API faellt aus -> Alarm
def test_kleinanzeigen_api_ausfall_gibt_alarm_und_schliesst_ihn_wieder(welt, monkeypatch):  # noqa: F811
    import provider_fetch as PF
    w = welt
    monkeypatch.setattr(PF, "_KA_API", {"folge": 0, "alarm_am": 0.0, "offen": False})
    filt = {"typ": "kleinanzeigen_api_gestoert", "ref": "kleinanzeigen_api"}
    for _ in range(PF.KA_API_ALARM_AB - 1):                       # einzelne Aussetzer: kein Alarm
        _lauf(PF._ka_api_gestoert(w.db, RuntimeError("HTTP 503")))
    assert w.run(w.db.betriebsalarme.count_documents(filt)) == 0
    _lauf(PF._ka_api_gestoert(w.db, RuntimeError("HTTP 503")))    # der dritte in Folge
    a = w.run(w.db.betriebsalarme.find_one(filt, {"_id": 0}))
    assert a and a["offen"] is True and a["anzahl"] == 1 and "503" in a["details"]["detail"]
    for _ in range(20):                                           # weitere Fehler: kein Alarm-Gewitter
        _lauf(PF._ka_api_gestoert(w.db, RuntimeError("HTTP 503")))
    assert w.run(w.db.betriebsalarme.find_one(filt, {"_id": 0}))["anzahl"] == 1
    _lauf(PF._ka_api_wieder_da(w.db))                             # API antwortet wieder
    assert w.run(w.db.betriebsalarme.find_one(filt, {"_id": 0}))["offen"] is False
    assert PF._KA_API["folge"] == 0 and PF._KA_API["offen"] is False
    _lauf(PF._ka_api_gestoert(w.db, RuntimeError("HTTP 429")))    # ein neuer einzelner Aussetzer: wieder kein Alarm
    assert w.run(w.db.betriebsalarme.count_documents({**filt, "offen": True})) == 0
