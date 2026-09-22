import { useUngespeichert } from "@/lib/ungespeichert";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { driverApi } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { verkleinereBildDatei } from "@/lib/bilder";
import { kmAusText } from "@/lib/preis";
import { lesen, schreiben, sitzungsSpeicher } from "@/lib/speicher";
import { protokollIstFinal, protokollNachsehen } from "@/pages/driver/fahrtPruefung";
import { toast } from "sonner";
import { X, Plus, Trash2, Camera, CheckCircle2 } from "lucide-react";

// Rollenprüfung 22.09.2026 (RP-070): Der Server nimmt höchstens 10 Schlüssel
// an (PickupReportIn.keys_count le=10) — die App erlaubte 20, 11–20 endeten
// als 422 erst beim Absenden.
export const SCHLUESSEL_HOECHSTENS = 10;

/** Idempotenz-Schlüssel je Dialog (RP-066/RP-165) — mit Rückfall ohne crypto. */
export function neueBerichtId() {
  try {
    if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  } catch { /* Rückfall unten */ }
  return `ac-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

// Rollenprüfung 22.09.2026 (Review): Der Server wiederholt einen Bericht mit
// demselben Schlüssel nur bei GLEICHEM Inhalt. Blieb der Schlüssel nach einer
// verlorenen Antwort im Zwischenstand liegen und wurden die Angaben danach
// geändert (Fahrt wieder geöffnet), antwortet er 409 mit diesem Text
// (BERICHT_ANDERER_INHALT in routes/drivers.py) — dann ein neuer Schlüssel.
export const ANDERER_INHALT_MERKMAL = "schon mit anderen Angaben gespeichert";

export function istAndererInhalt(e) {
  return e?.response?.status === 409 && errMsg(e, "").includes(ANDERER_INHALT_MERKMAL);
}

// RP-064/RP-163: Zwischenstand des Dialogs je Termin (nur diese Sitzung) —
// scheitert das Absenden, weil das Protokoll noch fehlt, geht nichts verloren.
const entwurfSchluessel = (id) => `ah_abholcheck_${id}`;

function entwurfLesen(id) {
  try {
    const roh = lesen(sitzungsSpeicher(), entwurfSchluessel(id), null);
    return roh ? JSON.parse(roh) : null;
  } catch {
    return null;
  }
}

function entwurfSichern(id, stand) {
  const speicher = sitzungsSpeicher();
  if (schreiben(speicher, entwurfSchluessel(id), JSON.stringify(stand))) return;
  // Zu groß (Fotos) — dann wenigstens alles ohne Fotos.
  schreiben(speicher, entwurfSchluessel(id), JSON.stringify({
    ...stand, deviations: (stand.deviations || []).map((d) => ({ ...d, photo_b64: null })),
  }));
}

export function abholCheckEntwurfLoeschen(id) {
  try { sitzungsSpeicher()?.removeItem(entwurfSchluessel(id)); } catch { /* egal */ }
}

/**
 * Abhol-Check der Fahrer-App: km-Stand, Schlüssel, Tankstand + Abweichungen
 * (optional mit Foto). Wird VOR der "Abgeholt"-Bestätigung ausgefüllt und
 * als unveränderbarer Abholbericht ans Backend gemeldet. Der Händler sieht
 * die Abweichungen anschließend in der Fahrzeugakte.
 */

const DEVIATION_TYPES = [
  { key: "mileage",       label: "Kilometerstand weicht ab" },
  { key: "keys",          label: "Schlüssel fehlt" },
  { key: "damage",        label: "Schaden / Kratzer" },
  { key: "tires",         label: "Reifen schlechter als angegeben" },
  { key: "warning_light", label: "Warnleuchte an" },
  { key: "equipment",     label: "Ausstattung fehlt" },
  { key: "documents",     label: "Dokument fehlt" },
  { key: "other",         label: "Sonstiges" },
];

const FUEL_LEVELS = ["leer", "1/4", "1/2", "3/4", "voll"];
// Prüfbericht 20.09. K-25: Standardwerte — weichen Schlüssel oder Tankstand
// davon ab, gilt der Dialog als ungespeichert (vorher zählten nur km/Notiz/
// Abweichungen, ein geänderter Tankstand ging beim Verlassen still verloren).
export const SCHLUESSEL_STANDARD = "2";
export const TANK_STANDARD = "1/2";
// K-05: dieselben Längen wie der Server (routes/drivers.py PickupReportIn).
export const ABWEICHUNG_TEXT_MAX = 200;
export const BEMERKUNG_MAX = 5000;

/** K-25: hat der Fahrer schon etwas eingetragen, das verloren ginge? */
export function abholCheckUngespeichert({ mileage, keys, fuel, notes, deviations }) {
  return Boolean(mileage || (notes || "").trim() || (deviations || []).length
    || String(keys ?? SCHLUESSEL_STANDARD) !== SCHLUESSEL_STANDARD
    || (fuel ?? TANK_STANDARD) !== TANK_STANDARD);
}

export default function AbholCheckDialog({ appointment, onDone, onClose }) {
  const navigate = useNavigate();
  // RP-064/RP-163: gesicherten Zwischenstand dieser Fahrt wieder aufnehmen.
  const [start] = useState(() => entwurfLesen(appointment.id) || {});
  const [mileage, setMileage] = useState(start.mileage ?? "");
  const [keys, setKeys] = useState(start.keys ?? SCHLUESSEL_STANDARD);
  const [fuel, setFuel] = useState(start.fuel ?? TANK_STANDARD);
  const [notes, setNotes] = useState(start.notes ?? "");
  const [deviations, setDeviations] = useState(Array.isArray(start.deviations) ? start.deviations : []);
  const [busy, setBusy] = useState(false);
  // RP-066/RP-165: EIN Schlüssel je Dialog — ein zweiter Versuch nach einem
  // Netzabbruch schickt denselben, der Server erkennt die Wiederholung.
  const [berichtId, setBerichtId] = useState(() => start.berichtId || neueBerichtId());
  const berichtGespeichert = useRef(false);
  // Rollenprüfung 22.09.2026 (RP-068/RP-167): Der Kilometerstand steht schon
  // im unterschriebenen Protokoll (Abschnitt 4) — vorbelegen statt ein
  // zweites Mal abfragen. Nur solange der Fahrer selbst nichts eingetragen hat
  // (auch kein gesicherter Zwischenstand).
  const kmGetippt = useRef(Boolean(start.mileage));
  const [kmAusProtokoll, setKmAusProtokoll] = useState(false);
  useEffect(() => {
    if (kmGetippt.current) return undefined;
    let aktiv = true;
    protokollNachsehen(appointment.id).then(({ kmStand }) => {
      if (!aktiv || kmGetippt.current || kmStand === null || kmStand === undefined) return;
      setMileage(String(kmStand));
      setKmAusProtokoll(true);
    }).catch(() => { /* ohne Vorbelegung weiter */ });
    return () => { aktiv = false; };
  }, [appointment.id]);
  const ungespeichert = abholCheckUngespeichert({ mileage, keys, fuel, notes, deviations });
  useEffect(() => {
    if (!ungespeichert) return undefined;
    const t = setTimeout(() => {
      if (!berichtGespeichert.current) {
        entwurfSichern(appointment.id, { mileage, keys, fuel, notes, deviations, berichtId });
      }
    }, 400);
    return () => clearTimeout(t);
  }, [appointment.id, ungespeichert, mileage, keys, fuel, notes, deviations, berichtId]);
  // Runde 21 (Gegenpruefung): Fotos, die gerade noch verkleinert werden —
  // solange darf nicht abgesendet werden (sonst fehlt das Foto im
  // unveraenderbaren Bericht).
  const [fotoLaeuft, setFotoLaeuft] = useState(0);
  // Runde 31: Kilometerstand, Abweichungen und Kamera-Fotos gehen erst beim
  // Absenden an den Server — bis dahin nicht still verlieren.
  useUngespeichert(ungespeichert);

  const addDeviation = () =>
    setDeviations((d) => [...d, {
      // feste Kennung je Zeile: ein spaet fertiges Foto landet sicher in der
      // richtigen Abweichung, auch wenn inzwischen eine Zeile geloescht wurde
      id: (window.crypto && window.crypto.randomUUID && window.crypto.randomUUID()) || `${Date.now()}-${Math.random()}`,
      field: "damage", label: "", expected: "", actual: "", note: "", photo_b64: null,
    }]);

  const updateDev = (id, patch) =>
    setDeviations((d) => d.map((x) => (x.id === id ? { ...x, ...patch } : x)));

  const removeDev = (id) => setDeviations((d) => d.filter((x) => x.id !== id));

  // Runde 21: Foto im Handy auf max. 2000 px verkleinern — schneller Upload
  // auch mit schwachem Netz, und Aufnahmeort/Geraetedaten fallen weg. Klappt
  // das nicht, geht das Original wie bisher (max. 6 MB).
  const attachPhoto = async (id, file) => {
    if (!file) return;
    setFotoLaeuft((n) => n + 1);
    try {
      let dataUrl = null;
      try { dataUrl = await verkleinereBildDatei(file); } catch (e) {
        // Rollenprüfung 22.09.2026 (RP-533): HEIC & Co. — die Meldung aus
        // lib/bilder sagt, was zu tun ist (statt "konnte nicht gelesen werden").
        if (e?.name === "BildFormatFehler") { toast.error(e.message); return; }
        dataUrl = null;
      }
      if (!dataUrl) { toast.error("Foto konnte nicht gelesen werden"); return; }
      if (dataUrl.length > 8000000) { toast.error("Foto zu groß (max. 6 MB)"); return; }
      updateDev(id, { photo_b64: dataUrl });
    } finally {
      setFotoLaeuft((n) => n - 1);
    }
  };

  const submit = async () => {
    if (busy) return;
    if (fotoLaeuft > 0) { toast.info("Foto wird noch vorbereitet – bitte kurz warten."); return; }
    for (const d of deviations) {
      if (!d.label.trim()) { toast.error("Bitte jede Abweichung kurz benennen."); return; }
    }
    // Pruefbericht 20.09.2026 (K-01/F20, R1-43): deutsch lesen ("85.120" ist
    // fuenfundachtzigtausend, nicht 85), keine negativen Werte, und ohne
    // Kilometerstand kein Bericht — vorher ging ein leeres Feld still durch.
    // Pruefbericht 20.09.2026 (DP-02/K-02/U2): Der Proxy nimmt hoechstens 25 MB
    // je Anfrage. Scheiterte die Verkleinerung (HEIC, defektes EXIF), gingen
    // die Originale raus — vier Handyfotos genuegten, und der fertige Bericht
    // samt Kilometerstand war weg. Jetzt vorher pruefen und klar sagen.
    const fotoSumme = deviations.reduce((n, d) => n + String(d.photo_b64 || "").length, 0);
    if (fotoSumme > 18_000_000) {
      toast.error("Die Fotos sind zusammen zu groß zum Senden. Bitte ein oder zwei Fotos "
        + "entfernen oder neu aufnehmen (kleinere Auflösung).");
      return;
    }
    const km = kmAusText(mileage);
    if (km === null) { toast.error("Bitte den Kilometerstand bei Abholung eintragen."); return; }
    if (Number.isNaN(km)) {
      toast.error("Kilometerstand bitte als ganze Zahl eintragen, z. B. 85.120 oder 85120.");
      return;
    }
    const schluessel = keys === "" ? null : Number(keys);
    if (schluessel !== null && (!Number.isInteger(schluessel) || schluessel < 0
                                || schluessel > SCHLUESSEL_HOECHSTENS)) {
      toast.error(`Anzahl Schlüssel bitte als ganze Zahl von 0 bis ${SCHLUESSEL_HOECHSTENS} eintragen.`);
      return;
    }
    setBusy(true);
    try {
      // "Abgeholt" gibt es nur mit unterschriebenem Protokoll. Vorher wurde
      // erst der Bericht geschickt und dann der Status — der lief ohne
      // Protokoll in 409, jeder neue Versuch legte eine weitere
      // Berichtsversion an (Pruefbericht Runde 4). Jetzt: Protokoll zuerst.
      // Die Startseite prüft das seit RP-064 schon VOR dem Öffnen; hier bleibt
      // die Prüfung als Netz, falls sich der Stand inzwischen geändert hat.
      if ((appointment.status || "") !== "abgeholt") {
        const final = await protokollIstFinal(appointment.id);
        if (final !== true) {
          // RP-064/RP-163: die Eingaben bleiben für diese Fahrt gesichert.
          entwurfSichern(appointment.id, { mileage, keys, fuel, notes, deviations, berichtId });
          toast.info("Bitte zuerst das Abholprotokoll ausfüllen und unterschreiben — danach den "
                     + "Abhol-Check senden. Deine Eingaben bleiben gespeichert.");
          onClose?.();
          navigate(`/fahrer/protokoll/${appointment.id}`);
          return;
        }
      }
      await driverApi.post(`/driver/appointments/${appointment.id}/report`, {
        mileage_at_pickup: km,
        keys_count: schluessel,
        fuel_level: fuel,
        deviations: deviations.map((d) => ({
          field: d.field,
          label: d.label,
          expected: d.expected, actual: d.actual, note: d.note,
          photo_b64: d.photo_b64,
        })),
        notes,
        client_bericht_id: berichtId,
      });
      // Der Bericht ist gespeichert — der Zwischenstand wird nicht mehr gebraucht.
      berichtGespeichert.current = true;
      abholCheckEntwurfLoeschen(appointment.id);
      // Idempotent: nach dem Protokoll-Abschluss ist der Termin bereits
      // "abgeholt"; das Backend bestaetigt das ohne Fehler.
      await driverApi.put(`/driver/appointments/${appointment.id}/status`, { status: "abgeholt" });
      toast.success(deviations.length
        ? `Abgeholt — ${deviations.length} Abweichung(en) gemeldet`
        : "Abgeholt — keine Abweichungen");
      onDone?.();
    } catch (e) {
      if (istAndererInhalt(e)) {
        // Rollenprüfung 22.09.2026 (Review): der alte Schlüssel gehört zu einem
        // schon gespeicherten Bericht — die geänderten Angaben bekommen einen
        // neuen; der nächste Tipp meldet sie als neue Version.
        const neu = neueBerichtId();
        setBerichtId(neu);
        entwurfSichern(appointment.id, { mileage, keys, fuel, notes, deviations, berichtId: neu });
      }
      toast.error(errMsg(e, "Abholbericht konnte nicht gesendet werden"));
    } finally {
      setBusy(false);
    }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const inputStyle = { borderColor: "var(--border-default)" };

  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.7)" }}>
      <div className="bleibt-dunkel w-full sm:max-w-xl max-h-[92vh] overflow-y-auto rounded-t-2xl sm:rounded-2xl p-5"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)",
                    color: "#ffffff" /* Dialog ist bewusst dunkel — Schrift
                    auch im hellen Theme explizit hell, sonst dunkel-auf-dunkel */ }}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-lg font-bold">Abhol-Check</div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white"><X size={20} /></button>
        </div>
        <div className="text-xs text-zinc-500 mb-4">
          {appointment.title} — bitte vor Ort prüfen und dann bestätigen.
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[11px] text-zinc-500">Kilometerstand bei Abholung</label>
            {/* text + inputMode: "85.120" bleibt so stehen, wie getippt, und
                wird deutsch gelesen (ein Zahlenfeld machte daraus 85,12). */}
            <input type="text" inputMode="numeric" value={mileage}
                   onChange={(e) => { kmGetippt.current = true; setKmAusProtokoll(false); setMileage(e.target.value); }}
                   data-testid="abholcheck-km" autoComplete="off"
                   placeholder="z. B. 85.120" className={inputCls} style={inputStyle} />
            {kmAusProtokoll && (
              <div className="mt-1 text-[10px] text-zinc-500" data-testid="abholcheck-km-aus-protokoll">
                aus dem unterschriebenen Protokoll übernommen — bitte prüfen
              </div>
            )}
          </div>
          <div>
            <label className="text-[11px] text-zinc-500">Anzahl Schlüssel</label>
            <input type="number" inputMode="numeric" value={keys} min={0} max={SCHLUESSEL_HOECHSTENS}
                   data-testid="abholcheck-schluessel"
                   onChange={(e) => setKeys(e.target.value)} className={inputCls} style={inputStyle} />
          </div>
        </div>

        <div className="mt-3">
          <label className="text-[11px] text-zinc-500">Tankfüllstand</label>
          <div className="flex gap-1.5 mt-1">
            {FUEL_LEVELS.map((f) => (
              <button key={f} type="button" onClick={() => setFuel(f)}
                      className={`px-3 py-1.5 rounded-lg text-xs border ${fuel === f ? "bg-white/15 font-semibold" : "text-zinc-400"}`}
                      style={inputStyle}>
                {f}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-4 flex items-center justify-between">
          <div className="text-sm font-semibold">Abweichungen ({deviations.length})</div>
          <button type="button" onClick={addDeviation}
                  className="inline-flex items-center gap-1 text-xs text-zinc-300 hover:text-white">
            <Plus size={14} /> Abweichung hinzufügen
          </button>
        </div>

        {deviations.map((d) => (
          <div key={d.id} className="mt-2 rounded-xl border p-3 space-y-2" style={inputStyle}>
            <div className="flex items-center gap-2">
              <select value={d.field}
                      onChange={(e) => updateDev(d.id, { field: e.target.value })}
                      className="flex-1 rounded-lg border bg-[#141416] px-2 py-1.5 text-xs"
                      style={inputStyle}>
                {DEVIATION_TYPES.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
              </select>
              <button type="button" onClick={() => removeDev(d.id)} className="text-zinc-500 hover:text-red-400">
                <Trash2 size={15} />
              </button>
            </div>
            {/* K-05: Längen wie der Server — sonst 422 mit englischem Text erst beim Absenden */}
            <input value={d.label} onChange={(e) => updateDev(d.id, { label: e.target.value })}
                   placeholder="Kurzbeschreibung, z.B. Kratzer hinten rechts *" maxLength={ABWEICHUNG_TEXT_MAX}
                   className={inputCls} style={inputStyle} />
            <div className="grid grid-cols-2 gap-2">
              <input value={d.expected} onChange={(e) => updateDev(d.id, { expected: e.target.value })}
                     placeholder="Laut Vertrag (z.B. 84.000 km)" maxLength={ABWEICHUNG_TEXT_MAX}
                     className={inputCls} style={inputStyle} />
              <input value={d.actual} onChange={(e) => updateDev(d.id, { actual: e.target.value })}
                     placeholder="Vor Ort (z.B. 85.120 km)" maxLength={ABWEICHUNG_TEXT_MAX}
                     className={inputCls} style={inputStyle} />
            </div>
            <div className="flex items-center gap-2">
              <label className="inline-flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer hover:text-white">
                <Camera size={14} />
                {d.photo_b64 ? "Foto ersetzen" : "Foto anhängen"}
                <input type="file" accept="image/*" capture="environment" className="hidden"
                       onChange={(e) => attachPhoto(d.id, e.target.files?.[0])} />
              </label>
              {d.photo_b64 && <img src={d.photo_b64} alt="" className="h-10 w-10 rounded object-cover" />}
            </div>
          </div>
        ))}

        <div className="mt-3">
          <label className="text-[11px] text-zinc-500">Bemerkungen (optional)</label>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2}
                    maxLength={BEMERKUNG_MAX} className={inputCls} style={inputStyle} />
        </div>

        <button onClick={submit} disabled={busy || fotoLaeuft > 0} data-testid="abholcheck-absenden"
                className="mt-4 w-full inline-flex items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white disabled:opacity-50"
                style={{ background: "var(--accent-red, #FF3B30)" }}>
          <CheckCircle2 size={17} />
          {fotoLaeuft > 0 ? "Foto wird vorbereitet…" : busy ? "Wird gesendet…" : deviations.length
            ? `Abholung mit ${deviations.length} Abweichung(en) bestätigen`
            : "Abholung ohne Abweichungen bestätigen"}
        </button>
        <div className="mt-2 text-[10px] text-zinc-600 text-center">
          Der Bericht ist nach dem Absenden nicht mehr änderbar — Korrekturen
          erzeugen eine neue Version.
        </div>
      </div>
    </div>
  );
}
