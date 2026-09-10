import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { X, Camera, Gauge, User as UserIcon } from "lucide-react";
import AbholFoto from "@/components/AbholFoto";

const fmtDatum = (d) => (d ? d.toLocaleDateString("de-DE") : "");

/** Loeschdatum der Fahrerfotos: Bericht erstellt + Frist (Tage). */
export function fotosBis(createdAt, tage) {
  if (!createdAt || !tage) return null;
  const d = new Date(createdAt);
  if (Number.isNaN(d.getTime())) return null;
  d.setDate(d.getDate() + Number(tage));
  return d;
}

/**
 * Abholbericht eines Termins mit den Fotos des Fahrers (Runde 21).
 *
 * Vorher gab es die Fahrerfotos nur in der Fahrzeugakte — Sucher hatten
 * keinen Menueweg dorthin, und im Bestand fehlte der Link gerade bei frisch
 * abgeholten Fahrzeugen. Jetzt oeffnet der Terminplaner den Bericht direkt.
 * Als Portal, damit der Dialog nicht im Termin-Knopf steckt; Klicks laufen
 * nicht zum Termin durch (stopPropagation am Hintergrund).
 */
export default function AbholberichtDialog({ appt, onClose }) {
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");

  useEffect(() => {
    let aktiv = true;
    api.get(`/appointments/${appt.id}/report`)
      .then((r) => { if (aktiv) setDaten(r.data || {}); })
      .catch((e) => { if (aktiv) setFehler(errMsg(e, "Abholbericht konnte nicht geladen werden")); });
    return () => { aktiv = false; };
  }, [appt.id]);

  const report = daten?.report;
  const devs = report?.deviations || [];
  const bis = fotosBis(report?.created_at, daten?.fahrerfoto_tage);
  const mitFoto = devs.filter((d) => d.photo_key).length;

  const inhalt = (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/70"
         onClick={(e) => { e.stopPropagation(); onClose(); }}
         data-testid="abholbericht-dialog">
      <div className="w-full max-w-lg max-h-[88vh] overflow-y-auto rounded-2xl p-5"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)", color: "#fff" }}
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[11px] uppercase tracking-wider text-zinc-500">Abholbericht</div>
            <div className="text-lg font-bold leading-tight">{appt.title}</div>
          </div>
          <button type="button" onClick={(e) => { e.stopPropagation(); onClose(); }}
                  className="text-zinc-400 hover:text-white" aria-label="Schließen">
            <X size={20} />
          </button>
        </div>

        {!daten && !fehler && <div className="mt-4 text-sm text-zinc-500">Lädt…</div>}
        {fehler && <div className="mt-4 text-sm text-red-400">{fehler}</div>}
        {daten && !report && (
          <div className="mt-4 text-sm text-zinc-500">Zu diesem Termin gibt es noch keinen Abholbericht.</div>
        )}

        {report && (
          <>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-zinc-400">
              {report.driver_name && <span className="inline-flex items-center gap-1"><UserIcon size={12} /> {report.driver_name}</span>}
              {report.mileage_at_pickup != null && (
                <span className="inline-flex items-center gap-1"><Gauge size={12} /> {Number(report.mileage_at_pickup).toLocaleString("de-DE")} km</span>
              )}
              {report.version > 1 && <span>Version {report.version} (korrigiert)</span>}
            </div>

            {devs.length === 0 ? (
              <div className="mt-4 text-sm text-emerald-300">Keine Abweichungen gemeldet.</div>
            ) : (
              <ul className="mt-4 space-y-3">
                {devs.map((d, i) => (
                  <li key={d.id || i} className="flex gap-3 items-start rounded-xl p-3"
                      style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.06)" }}>
                    {d.photo_key
                      ? <AbholFoto photoKey={d.photo_key} label={d.label} size={72} />
                      : (
                        <span className="inline-flex items-center justify-center rounded-md text-zinc-600 text-[10px] text-center leading-tight"
                              style={{ width: 72, height: 72, border: "1px dashed rgba(255,255,255,0.12)" }}>
                          {d.photo_deleted_at ? "Foto nach Frist gelöscht" : "kein Foto"}
                        </span>
                      )}
                    <div className="min-w-0 text-sm">
                      <div className="font-semibold">{d.label}</div>
                      {(d.expected || d.actual) && (
                        <div className="text-xs text-zinc-400 mt-0.5">
                          {d.expected ? <>Laut Vertrag: {d.expected} · </> : null}
                          Vor Ort: {d.actual || "—"}
                        </div>
                      )}
                      {d.note && <div className="text-xs text-zinc-500 mt-1 whitespace-pre-line">{d.note}</div>}
                    </div>
                  </li>
                ))}
              </ul>
            )}

            {report.notes && (
              <div className="mt-3 text-xs text-zinc-400 whitespace-pre-line">
                <span className="text-zinc-500">Notiz des Fahrers: </span>{report.notes}
              </div>
            )}

            {mitFoto > 0 && bis && (
              <div className="mt-4 text-[11px] text-zinc-500 inline-flex items-center gap-1.5" data-testid="fotos-bis">
                <Camera size={12} /> Die Fotos werden am {fmtDatum(bis)} automatisch gelöscht.
                Im Verkaufsinserat kannst du sie vorher übernehmen.
              </div>
            )}
          </>
        )}

        {appt.vehicle_id && (
          <div className="mt-4">
            <Link to={`/app/akte/${appt.vehicle_id}`} onClick={(e) => e.stopPropagation()}
                  className="text-xs text-sky-300 hover:underline">
              Zur Fahrzeugakte
            </Link>
          </div>
        )}
      </div>
    </div>
  );
  return createPortal(inhalt, document.body);
}
