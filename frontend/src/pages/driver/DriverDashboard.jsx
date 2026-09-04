import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { driverApi, openDriverPdf } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  Calendar, MapPin, Phone, FileText, ClipboardCheck,
  CheckCircle2, Car, ChevronDown, ChevronUp, Building2, XCircle,
} from "lucide-react";
import PhotoGallery from "@/components/PhotoGallery";
import AbholCheckDialog from "@/components/AbholCheckDialog";

const fmtDate = (s) => {
  if (!s) return "—";
  try {
    return new Date(s).toLocaleDateString("de-DE", {
      weekday: "short", day: "2-digit", month: "short", year: "numeric",
    });
  } catch { return s; }
};

const dayKey = (s) => (s || "unbekannt").slice(0, 10);

export default function DriverDashboard() {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState({});
  const [busy, setBusy] = useState(null);
  const [checkAppt, setCheckAppt] = useState(null); // Abhol-Check-Dialog

  useEffect(() => {
    driverApi.get("/driver/appointments")
      .then((r) => setItems(r.data))
      .catch((e) => toast.error(errMsg(e, "Termine konnten nicht geladen werden")))
      .finally(() => setLoading(false));
  }, []);

  // Zugeteilte Fahrt annehmen / ablehnen (09/2026)
  const zuteilung = async (id, action) => {
    if (busy) return;
    let grund = "";
    if (action === "ablehnen") {
      grund = window.prompt("Fahrt ablehnen — Grund (optional):") ?? null;
      if (grund === null) return;
    }
    setBusy(id);
    try {
      await driverApi.put(`/driver/appointments/${id}/zuteilung`, { action, grund });
      toast.success(action === "annehmen" ? "Fahrt angenommen" : "Fahrt abgelehnt — der Händler wurde informiert");
      const r = await driverApi.get("/driver/appointments");
      setItems(r.data);
    } catch (e) {
      toast.error(errMsg(e, "Antwort fehlgeschlagen"));
    } finally {
      setBusy(null);
    }
  };

  const setStatus = async (id, status) => {
    if (busy) return;
    // "abgeholt" läuft über den Abhol-Check-Dialog (mit Abweichungsbericht).
    if (status === "abgeholt") {
      const appt = items.find((a) => a.id === id);
      if (appt) setCheckAppt(appt);
      return;
    }
    const confirmMsg =
      "Fahrt als 'nicht abgeholt' markieren?\nHinweis: Fotos & Beweis-Archiv werden nach 14 Tagen automatisch gelöscht.";
    if (!window.confirm(confirmMsg)) return;
    setBusy(id);
    try {
      await driverApi.put(`/driver/appointments/${id}/status`, { status });
      toast.success(status === "abgeholt" ? "Als abgeholt markiert" : "Als nicht abgeholt markiert");
      const r = await driverApi.get("/driver/appointments");
      setItems(r.data);
    } catch (e) {
      toast.error(errMsg(e, "Statuswechsel fehlgeschlagen"));
    } finally {
      setBusy(null);
    }
  };

  const grouped = useMemo(() => {
    const g = {};
    (items || []).forEach((a) => {
      const k = dayKey(a.pickup_date);
      (g[k] = g[k] || []).push(a);
    });
    return Object.entries(g).sort(([a], [b]) => a.localeCompare(b));
  }, [items]);

  const oeffnePdf = (path) =>
    openDriverPdf(path).catch((e) => toast.error(errMsg(e)));

  return (
    <div data-testid="driver-dashboard">
      <div className="overline">Meine Fahrten</div>
      <h1 className="font-display font-black text-2xl tracking-tighter mb-5">
        {items.length} {items.length === 1 ? "Termin" : "Termine"}
      </h1>

      {loading && (
        <div className="tactical-card p-8 text-center text-zinc-500 text-sm">Lade …</div>
      )}

      {!loading && items.length === 0 && (
        <div className="tactical-card p-10 text-center">
          <Car size={32} className="mx-auto text-zinc-600" />
          <div className="mt-3 font-semibold text-zinc-300">Noch keine Fahrten</div>
          <div className="mt-1 text-xs text-zinc-500">
            Sobald dir ein Händler eine Abholung zuweist, erscheint sie hier.
          </div>
        </div>
      )}

      <div className="space-y-6">
        {grouped.map(([day, appts]) => (
          <section key={day} data-testid={`day-${day}`}>
            <div className="flex items-center gap-2 mb-2">
              <Calendar size={13} className="text-zinc-500" />
              <span className="text-xs font-semibold uppercase tracking-[0.15em] text-zinc-400">
                {fmtDate(day)}
              </span>
              <span className="text-xs text-zinc-600">· {appts.length} Fahrt(en)</span>
            </div>
            <div className="space-y-3">
              {appts.map((a) => {
                const v = a.vehicle || {};
                const isOpen = !!open[a.id];
                const photos = v.photos || [];
                return (
                  <div key={a.id} className="tactical-card overflow-hidden"
                       data-testid={`appt-${a.id}`}>
                    <button onClick={() => setOpen({ ...open, [a.id]: !isOpen })}
                            className="w-full text-left p-4 flex items-start gap-3 hover:bg-white/[0.02]">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 text-xs text-zinc-400">
                          <span className="font-mono">{a.pickup_time || "—"}</span>
                          {a.dealer?.name && (
                            <span className="flex items-center gap-1 text-zinc-500">
                              <Building2 size={10} />{a.dealer.name}
                            </span>
                          )}
                          {a.status && (
                            <span className="ml-auto text-[10px] px-2 py-0.5 rounded-sm"
                                  style={{ background: "rgba(255,255,255,0.05)" }}>
                              {a.status}
                            </span>
                          )}
                        </div>
                        <div className="font-display font-bold text-lg tracking-tight mt-1 truncate">
                          {v.make || "Fahrzeug"} {v.model || ""}
                        </div>
                        <div className="text-xs text-zinc-500 flex items-center gap-1 mt-0.5">
                          <MapPin size={11} className="flex-shrink-0" />
                          <span className="truncate">{a.pickup_address || a.seller_name || "—"}</span>
                        </div>
                      </div>
                      {isOpen
                        ? <ChevronUp size={18} className="text-zinc-500 mt-1" />
                        : <ChevronDown size={18} className="text-zinc-500 mt-1" />}
                    </button>

                    {isOpen && (
                      <div className="border-t px-4 py-4 space-y-4"
                           style={{ borderColor: "var(--border-default)" }}>
                        <div className="grid grid-cols-2 gap-2 text-xs">
                          {[
                            ["EZL", v.ezl],
                            ["KM", v.km],
                            ["kW", v.power_kw],
                            ["Kraftstoff", v.fuel],
                            ["Farbe", v.color],
                            ["FIN", v.fin],
                          ].map(([k, val]) => (
                            <div key={k} className="flex justify-between border-b pb-1"
                                 style={{ borderColor: "var(--border-default)" }}>
                              <span className="text-zinc-500">{k}</span>
                              <span className="font-mono text-zinc-200 truncate ml-2">{val || "—"}</span>
                            </div>
                          ))}
                        </div>

                        <div className="flex gap-2">
                          {a.seller_phone && (
                            <a href={`tel:${a.seller_phone}`}
                               data-testid={`call-seller-${a.id}`}
                               className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm text-sm font-semibold"
                               style={{ background: "rgba(52,199,89,0.12)", color: "var(--accent-green)" }}>
                              <Phone size={14} /> {a.seller_name || "Anrufen"}
                            </a>
                          )}
                          {a.pickup_address && (
                            <a href={`https://maps.google.com/?q=${encodeURIComponent(a.pickup_address)}`}
                               target="_blank" rel="noreferrer"
                               className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm text-sm font-semibold bg-white/5 hover:bg-white/10">
                              <MapPin size={14} /> Navi
                            </a>
                          )}
                        </div>

                        {photos.length > 0 && (
                          <PhotoGallery photos={photos} label="Fahrzeug-Fotos" />
                        )}

                        {/* Digitales Protokoll: dieselben Punkte wie im PDF,
                            direkt in der App ausfüllbar inkl. Unterschrift. */}
                        {a.zuteilung !== "offen" && (
                        <Link to={`/fahrer/protokoll/${a.id}`}
                              data-testid={`protokoll-${a.id}`}
                              className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-bold kinetic-button mb-2">
                          <ClipboardCheck size={16} /> Protokoll ausfüllen
                        </Link>
                        )}

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          <button onClick={() => oeffnePdf(`/driver/appointments/${a.id}/pickup-order.pdf`)}
                                  data-testid={`pickup-pdf-${a.id}`}
                                  className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm border"
                                  style={{ borderColor: "var(--border-default)" }}>
                            <FileText size={15} /> Papier-PDF
                          </button>
                          {a.contract_id && (
                            <button onClick={() => oeffnePdf(`/driver/contracts/${a.contract_id}/pdf`)}
                                    data-testid={`contract-pdf-${a.id}`}
                                    className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-semibold bg-white/5 hover:bg-white/10">
                              <FileText size={15} /> Kaufvertrag
                            </button>
                          )}
                        </div>

                        {a.snapshot_id && (
                          <button onClick={() => oeffnePdf(`/driver/snapshots/${a.snapshot_id}/pdf`)}
                                  data-testid={`snapshot-pdf-${a.id}`}
                                  className="flex items-center justify-center gap-2 px-4 py-2 rounded-sm text-xs font-semibold bg-white/5 hover:bg-white/10">
                            <CheckCircle2 size={13} /> Beweis-Archiv (Inserat-PDF)
                          </button>
                        )}

                        {a.notes && (
                          <div className="text-xs text-zinc-400 p-3 rounded-sm"
                               style={{ background: "rgba(255,255,255,0.02)" }}>
                            <div className="overline mb-1">Notizen</div>
                            {a.notes}
                          </div>
                        )}

                        {/* Zuteilung: erst annehmen oder ablehnen (09/2026) */}
                        {a.zuteilung === "offen" && a.status !== "abgeholt" && a.status !== "nicht abgeholt" && (
                          <div className="pt-2 border-t" style={{ borderColor: "var(--border-default)" }}
                               data-testid={`zuteilung-${a.id}`}>
                            <div className="text-xs mb-2 font-semibold"
                                 style={{ color: a.zuteilung_neu_wegen_aenderung ? "var(--accent-red)" : "var(--accent-green)" }}>
                              {a.zuteilung_neu_wegen_aenderung
                                ? "Fahrt wurde geändert (Datum, Uhrzeit oder Adresse) — bitte erneut bestätigen"
                                : "Neue Fahrt zugeteilt — annehmen?"}
                            </div>
                            <div className="grid grid-cols-2 gap-2">
                              <button onClick={() => zuteilung(a.id, "annehmen")} disabled={busy === a.id}
                                      data-testid={`zuteilung-annehmen-${a.id}`}
                                      className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-bold disabled:opacity-50"
                                      style={{ background: "rgba(52,199,89,0.14)", color: "var(--accent-green)", border: "1px solid rgba(52,199,89,0.3)" }}>
                                <CheckCircle2 size={15} /> Annehmen
                              </button>
                              <button onClick={() => zuteilung(a.id, "ablehnen")} disabled={busy === a.id}
                                      data-testid={`zuteilung-ablehnen-${a.id}`}
                                      className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-semibold disabled:opacity-50"
                                      style={{ background: "rgba(255,59,48,0.1)", color: "#ff6b5f", border: "1px solid rgba(255,59,48,0.25)" }}>
                                <XCircle size={15} /> Ablehnen
                              </button>
                            </div>
                          </div>
                        )}
                        {/* Status-Aktionen (Fahrer markiert Ergebnis) — erst nach Annahme */}
                        {a.zuteilung !== "offen" && a.status !== "abgeholt" && a.status !== "nicht abgeholt" && (
                          <div className="grid grid-cols-2 gap-2 pt-2 border-t"
                               style={{ borderColor: "var(--border-default)" }}>
                            <button
                              onClick={() => setStatus(a.id, "abgeholt")}
                              disabled={busy === a.id}
                              data-testid={`mark-pickedup-${a.id}`}
                              className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-bold disabled:opacity-50"
                              style={{ background: "rgba(52,199,89,0.14)",
                                       color: "var(--accent-green)",
                                       border: "1px solid rgba(52,199,89,0.3)" }}
                            >
                              <CheckCircle2 size={15} /> Abgeholt
                            </button>
                            <button
                              onClick={() => setStatus(a.id, "nicht abgeholt")}
                              disabled={busy === a.id}
                              data-testid={`mark-notpickedup-${a.id}`}
                              className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-semibold disabled:opacity-50"
                              style={{ background: "rgba(255,59,48,0.1)",
                                       color: "#ff6b5f",
                                       border: "1px solid rgba(255,59,48,0.25)" }}
                            >
                              <XCircle size={15} /> Nicht abgeholt
                            </button>
                          </div>
                        )}
                        {(a.status === "abgeholt" || a.status === "nicht abgeholt") && (
                          <div className="text-xs text-zinc-500 p-3 rounded-sm text-center"
                               style={{ background: "rgba(255,255,255,0.02)" }}>
                            {a.status === "abgeholt" ? "✓ Als abgeholt markiert" : "✕ Als nicht abgeholt markiert"}
                            {" · "}Fotos & Beweis-Archiv werden automatisch gelöscht
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}
      </div>

      {checkAppt && (
        <AbholCheckDialog
          appointment={checkAppt}
          onClose={() => setCheckAppt(null)}
          onDone={async () => {
            setCheckAppt(null);
            try {
              const r = await driverApi.get("/driver/appointments");
              setItems(r.data);
            } catch { /* Liste wird beim nächsten Laden aktualisiert */ }
          }}
        />
      )}
    </div>
  );
}
