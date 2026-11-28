import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { lokalerSpeicher } from "@/lib/speicher";
import { fensterDanebenSetzen, zweitenBildschirmAnfragen } from "@/lib/popup";
import { einstellungLesen, einstellungSchreiben } from "@/lib/vergleichSpeicher";

/**
 * Die Schalter der Vergleichsseite (08.10.2026 aus Vergleich.jsx herausgezogen): Portale, Filter automatisch
 * öffnen, Filter daneben (zweiter Bildschirm). `schalterRef` hält die aktuellen Stände für einen laufenden
 * Vergleich (Runde 22: ein Lauf kann Minuten dauern, die Werte vom Start wären dann veraltet).
 */
export default function useVergleichsSchalter(user) {
  const kontoId = user?.id || null;
  // Portal-Toggles. Wunsch Ahmad 08.10.2026: EINE Wahl je Konto (users.vergleich_portale) — sie gilt auch fuer das
  // Windows-Programm und die Browser-Erweiterung; der Browser-Speicher ist nur noch der Rueckfall (alte Konten).
  const kontoPortale = user?.vergleich_portale;
  const [portalMobile, setPortalMobile] = useState(() => {
    if (kontoPortale && typeof kontoPortale.mobile === "boolean") return kontoPortale.mobile;
    return einstellungLesen(lokalerSpeicher(), "ah_portal_mobile", user?.id, true);
  });
  const [portalAutoscout, setPortalAutoscout] = useState(() => {
    if (kontoPortale && typeof kontoPortale.autoscout === "boolean") return kontoPortale.autoscout;
    return einstellungLesen(lokalerSpeicher(), "ah_portal_autoscout", user?.id, true);
  });

  // Runde 22 (11.09.2026): Filter nach dem Auslesen automatisch oeffnen —
  // Standard AN (Wunsch Ahmad: Einfuegen genuegt, alles geht von selbst auf).
  const [filterAuto, setFilterAuto] = useState(() => {
    return einstellungLesen(lokalerSpeicher(), "ah_filter_automatisch", user?.id, true);
  });
  // Runde 22 (11.09.2026, Gegenpruefung): aktuelle Schalter-Staende fuer das
  // automatische Oeffnen. Ein Lauf kann Minuten dauern — die Werte aus dem
  // Moment des Starts waeren veraltet, wenn der Sucher inzwischen umschaltet.
  const schalterRef = useRef({ mobile: portalMobile, autoscout: portalAutoscout, auto: filterAuto });

  // Kontowert nachziehen, sobald /auth/me ihn liefert (oder ein anderes Geraet ihn geaendert hat)
  const kontoMobile = kontoPortale?.mobile;
  const kontoAutoscout = kontoPortale?.autoscout;
  useEffect(() => {
    if (typeof kontoMobile === "boolean") { setPortalMobile(kontoMobile); schalterRef.current.mobile = kontoMobile; }
    if (typeof kontoAutoscout === "boolean") {
      setPortalAutoscout(kontoAutoscout); schalterRef.current.autoscout = kontoAutoscout;
    }
  }, [kontoMobile, kontoAutoscout]);

  const portaleSetzen = (mobile, autoscout) => {
    if (!mobile && !autoscout) {
      toast.info("Mindestens ein Portal muss an sein.");
      return;
    }
    const vorher = { mobile: portalMobile, autoscout: portalAutoscout };
    setPortalMobile(mobile);
    setPortalAutoscout(autoscout);
    schalterRef.current.mobile = mobile;
    schalterRef.current.autoscout = autoscout;
    einstellungSchreiben(lokalerSpeicher(), "ah_portal_mobile", kontoId, mobile);
    einstellungSchreiben(lokalerSpeicher(), "ah_portal_autoscout", kontoId, autoscout);
    api.put("/auth/vergleich-portale", { mobile, autoscout }).catch((e) => {
      setPortalMobile(vorher.mobile);
      setPortalAutoscout(vorher.autoscout);
      schalterRef.current.mobile = vorher.mobile;
      schalterRef.current.autoscout = vorher.autoscout;
      toast.error(errMsg(e, "Portalwahl nicht gespeichert – bitte erneut versuchen."));
    });
  };
  const toggleMobile = (v) => portaleSetzen(v, portalAutoscout);
  const toggleAutoscout = (v) => portaleSetzen(portalMobile, v);
  const toggleFilterAuto = (v) => {
    setFilterAuto(v);
    schalterRef.current.auto = v;
    einstellungSchreiben(lokalerSpeicher(), "ah_filter_automatisch", kontoId, v);
  };
  // 15.09.2026 (Wunsch Ahmad): Filter-Fenster neben der App bzw. auf dem
  // zweiten Bildschirm statt ueber der Seite. Die Bildschirm-Berechtigung
  // fragt der Browser beim Einschalten (Klick) ab; ist sie schon erteilt,
  // reicht das stille Nachfragen beim Laden.
  const [fensterDaneben, setFensterDaneben] = useState(() => {
    return einstellungLesen(lokalerSpeicher(), "ah_fenster_daneben", user?.id, false);
  });
  useEffect(() => {
    fensterDanebenSetzen(fensterDaneben);
    if (fensterDaneben) zweitenBildschirmAnfragen().catch(() => {});
  }, [fensterDaneben]);
  const toggleFensterDaneben = async (v) => {
    setFensterDaneben(v);
    einstellungSchreiben(lokalerSpeicher(), "ah_fenster_daneben", kontoId, v);
    fensterDanebenSetzen(v);
    if (!v) return;
    const r = await zweitenBildschirmAnfragen();
    if (r.ok && r.anzahl > 1) toast.success("Zweiter Bildschirm erkannt — die Filter öffnen dort.");
    else if (r.ok) toast.info("Nur ein Bildschirm erkannt — die Filter öffnen neben der App, wenn Platz ist.");
    else toast.info("Ohne Bildschirm-Berechtigung öffnen die Filter neben dem App-Fenster (gleicher Bildschirm).");
  };

  return {
    portalMobile, portalAutoscout, filterAuto, fensterDaneben, schalterRef,
    toggleMobile, toggleAutoscout, toggleFilterAuto, toggleFensterDaneben,
  };
}
