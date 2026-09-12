import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { preisAusText, preisText } from "@/lib/preis";
import { toast } from "sonner";
import {
  AlertTriangle, Check, ClipboardCheck, Phone, RotateCcw, Euro,
} from "lucide-react";

/**
 * Abholprotokolle, die auf die Freigabe des Chefs warten (Runde 30).
 *
 * Ablauf vor Ort: Der Fahrer füllt das Protokoll aus und schickt es ab —
 * noch ohne Unterschriften. Hier sieht der Händler auf einen Blick, was
 * abweicht und welche Schäden neu sind, kann beim Verkäufer anrufen und
 * nachverhandeln. Gibt er frei (ggf. mit neuem Preis), unterschreiben
 * Verkäufer und Fahrer vor Ort und das Protokoll wird endgültig.
 */
const eur = preisText;

export default function FreigabeKasten({ onAenderung }) {
  const [liste, setListe] = useState([]);
  const [preis, setPreis] = useState({});
  const [notiz, setNotiz] = useState({});
  const [busy, setBusy] = useState("");

  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/protocols/zur-freigabe");
      setListe(Array.isArray(data) ? data : []);
    } catch {
      /* still: der Kasten ist eine Ergänzung, kein Muss */
    }
  }, []);

  useEffect(() => {
    laden();
    // Der Fahrer steht vor Ort und wartet — regelmäßig nachsehen.
    const t = setInterval(laden, 20000);
    return () => clearInterval(t);
  }, [laden]);

  const senden = async (eintrag, zurueck) => {
    setBusy(eintrag.protocol_id);
    try {
      const koerper = { zurueck };
      const p = preis[eintrag.protocol_id];
      if (!zurueck && p !== undefined && String(p).trim() !== "") {
        // Gegenprüfung 12.09.2026: "15.000" wurde vorher zu 15 €.
        const zahl = preisAusText(p);
        if (zahl === null) {
          toast.error("Bitte einen gültigen Preis eingeben, z. B. 15.000 oder 15000,50");
          setBusy("");
          return;
        }
        koerper.neuer_preis = zahl;
      }
      const n = notiz[eintrag.protocol_id];
      if (n !== undefined && String(n).trim() !== "") koerper.notiz = String(n).trim();
      await api.post(`/protocols/${eintrag.protocol_id}/freigabe`, koerper);
      toast.success(zurueck
        ? "Zurück an den Fahrer geschickt"
        : "Freigegeben — der Fahrer kann jetzt unterschreiben lassen");
      await laden();
      onAenderung?.();
    } catch (e) {
      toast.error(errMsg(e, "Freigabe fehlgeschlagen"));
    } finally {
      setBusy("");
    }
  };

  if (!liste.length) return null;

  return (
    <div className="apple-surface-gloss p-4 lg:p-5 mb-5" data-testid="freigabe-kasten">
      <div className="flex items-center gap-2 mb-3">
        <ClipboardCheck size={17} style={{ color: "var(--accent-red)" }} />
        <div className="font-display font-bold text-lg tracking-tight">
          Abholprotokolle zur Freigabe
        </div>
        <span className="text-xs rounded-full px-2 py-0.5"
              style={{ background: "var(--accent-red)", color: "#fff" }}>
          {liste.length}
        </span>
      </div>
      <div className="space-y-4">
        {liste.map((e) => {
          const wartet = e.status === "freigegeben";
          return (
            <div key={e.protocol_id} className="rounded-xl border p-3"
                 style={{ borderColor: "var(--border-default)" }}
                 data-testid={`freigabe-${e.protocol_id}`}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-semibold text-sm truncate">{e.fahrzeug || "Fahrzeug"}</div>
                  <div className="text-[11px] text-zinc-500 truncate">
                    {e.abholung} · {e.abholort}
                  </div>
                  <div className="text-[11px] text-zinc-500 truncate">
                    Fahrer: {e.fahrer || "—"} · Verkäufer: {e.verkaeufer || "—"}
                  </div>
                </div>
                {wartet && (
                  <span className="text-[11px] rounded-full px-2 py-0.5"
                        style={{ background: "#34c75922", color: "#34c759" }}>
                    freigegeben — wartet auf Unterschriften
                  </span>
                )}
              </div>

              {/* Was der Fahrer vor Ort gefunden hat */}
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <div className="rounded-lg p-2 text-[11px]"
                     style={{ background: "rgba(255,255,255,0.03)" }}>
                  <div className="text-zinc-500 mb-1">Kilometerstand bei Abholung</div>
                  <div className="text-sm">{e.kilometerstand || "—"}</div>
                </div>
                <div className="rounded-lg p-2 text-[11px]"
                     style={{ background: "rgba(255,255,255,0.03)" }}>
                  <div className="text-zinc-500 mb-1">Bekannte Schäden bestätigt</div>
                  <div className="text-sm">
                    {e.schaeden_bestaetigt === true ? "ja"
                      : e.schaeden_bestaetigt === false ? "nein" : "—"}
                  </div>
                </div>
              </div>

              {(e.abweichungen || []).length > 0 && (
                <div className="mt-2 rounded-lg p-2"
                     style={{ background: "#ff9f0a14", border: "1px solid #ff9f0a44" }}>
                  <div className="text-[11px] font-semibold mb-1 inline-flex items-center gap-1"
                       style={{ color: "#ff9f0a" }}>
                    <AlertTriangle size={12} /> Weicht vom Vertrag ab
                  </div>
                  <ul className="text-[11px] space-y-0.5">
                    {e.abweichungen.map((a, i) => (
                      <li key={i}>
                        <span className="text-zinc-400">{a.feld}:</span>{" "}
                        <b>{a.status}</b>{a.wert ? ` → ${a.wert}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {(e.neue_schaeden || []).length > 0 && (
                <div className="mt-2 rounded-lg p-2"
                     style={{ background: "#ff3b3014", border: "1px solid #ff3b3044" }}>
                  <div className="text-[11px] font-semibold mb-1" style={{ color: "#ff3b30" }}>
                    Neue Schäden vor Ort: {e.neue_schaeden.length}
                  </div>
                  <ul className="text-[11px] space-y-0.5">
                    {e.neue_schaeden.slice(0, 8).map((s, i) => (
                      <li key={i}>
                        {(s.type_label || s.label || s.type || "Schaden")}{(s.zone || s.part_label) ? ` · ${s.zone || s.part_label}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {e.bemerkungen && (
                <div className="mt-2 text-[11px] text-zinc-400">
                  <span className="text-zinc-500">Bemerkung des Fahrers:</span> {e.bemerkungen}
                </div>
              )}

              {/* Preis nachverhandeln */}
              <div className="mt-3 rounded-lg p-2.5"
                   style={{ background: "rgba(255,255,255,0.03)" }}>
                <div className="flex flex-wrap items-end gap-3">
                  <div className="text-[11px]">
                    <div className="text-zinc-500">Preis laut Vertrag</div>
                    <div className="text-sm font-semibold">{eur(e.preis_vertrag)}</div>
                  </div>
                  <div className="text-[11px] flex-1 min-w-[130px]">
                    <label className="text-zinc-500 inline-flex items-center gap-1">
                      <Euro size={11} /> Neuer Preis (nach Verhandlung)
                    </label>
                    <input
                      type="text" inputMode="decimal"
                      data-testid={`freigabe-preis-${e.protocol_id}`}
                      placeholder={e.neuer_preis != null ? String(e.neuer_preis) : "z. B. 15.000"}
                      value={preis[e.protocol_id] ?? ""}
                      onChange={(ev) => setPreis((s) => ({ ...s, [e.protocol_id]: ev.target.value }))}
                      className="mt-1 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm outline-none focus:border-white/40"
                      style={{ borderColor: "var(--border-default)" }} />
                  </div>
                  <div className="text-[11px] flex-1 min-w-[150px]">
                    <label className="text-zinc-500">Vermerk (erscheint im Protokoll)</label>
                    <input
                      type="text" maxLength={200}
                      value={notiz[e.protocol_id] ?? ""}
                      onChange={(ev) => setNotiz((s) => ({ ...s, [e.protocol_id]: ev.target.value }))}
                      placeholder="z. B. Rost am Schweller"
                      className="mt-1 w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm outline-none focus:border-white/40"
                      style={{ borderColor: "var(--border-default)" }} />
                  </div>
                </div>
                {e.neuer_preis != null && (
                  <div className="mt-2 text-[11px]" style={{ color: "#34c759" }}>
                    Aktuell freigegeben mit {eur(e.neuer_preis)}
                    {e.preis_notiz ? ` · ${e.preis_notiz}` : ""}
                  </div>
                )}
              </div>

              <div className="mt-3 flex flex-wrap gap-2">
                <button onClick={() => senden(e, false)} disabled={busy === e.protocol_id}
                        data-testid={`freigabe-ok-${e.protocol_id}`}
                        className="apple-btn apple-btn-primary !py-2 disabled:opacity-50">
                  <Check size={14} /> {wartet ? "Preis aktualisieren" : "Freigeben"}
                </button>
                <button onClick={() => senden(e, true)} disabled={busy === e.protocol_id}
                        data-testid={`freigabe-zurueck-${e.protocol_id}`}
                        className="apple-btn apple-btn-secondary !py-2 disabled:opacity-50">
                  <RotateCcw size={14} /> Zurück an den Fahrer
                </button>
                {e.verkaeufer && (
                  <span className="inline-flex items-center gap-1 text-[11px] text-zinc-500 self-center">
                    <Phone size={11} /> Vor der Freigabe beim Verkäufer nachverhandeln
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
