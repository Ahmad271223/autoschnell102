import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { driverApi, openDriverPdf } from "@/context/DriverContext";
import { errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  Calendar, MapPin, Phone, FileText, ClipboardCheck,
  CheckCircle2, Car, ChevronDown, ChevronUp, Building2, XCircle,
} from "lucide-react";
import PhotoGallery from "@/components/PhotoGallery";
import AbholCheckDialog from "@/components/AbholCheckDialog";
import { NICHT_ABGEHOLT_GRUENDE, nichtAbgeholtNotiz, protokollIstFinal } from "./fahrtPruefung";

// Rollenprüfung 22.09.2026 (RP-464): Zuweisung, Verlegung und Storno kamen
// erst nach manuellem Neuladen an. Jetzt still nachladen — im Takt und
// sobald die App wieder sichtbar wird.
const NACHLADEN_MS = 60000;
const GESCHLOSSEN = ["abgeholt", "nicht abgeholt", "storniert", "erledigt"];

/** Prüfbericht 20.09. V-34: hat der Server die Liste gekappt (X-Truncated: 1)? */
export function listeGekuerzt(antwort) {
  return String(antwort?.headers?.["x-truncated"] || "") === "1";
}

/** RP-537: "Nicht abgeholt" nur mit Grund (vorher ein nacktes confirm). */
function NichtAbgeholtDialog({ fahrt, onAbbrechen, onSenden, busy }) {
  const [grund, setGrund] = useState("");
  const [text, setText] = useState("");
  const notiz = nichtAbgeholtNotiz(grund, text);
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4"
         style={{ background: "rgba(0,0,0,0.7)" }} data-testid="nicht-abgeholt-dialog">
      <div className="bleibt-dunkel w-full sm:max-w-md rounded-t-2xl sm:rounded-2xl p-5"
           style={{ background: "#141416", border: "1px solid rgba(255,255,255,0.1)", color: "#ffffff" }}>
        <div className="text-lg font-bold">Nicht abgeholt</div>
        <div className="text-xs text-zinc-500 mt-0.5 mb-3">
          {fahrt?.title || "Fahrt"} — warum konnte das Fahrzeug nicht abgeholt werden?
        </div>
        <div className="space-y-1.5">
          {NICHT_ABGEHOLT_GRUENDE.map((g) => (
            <label key={g.key} className="flex items-center gap-2 text-sm cursor-pointer">
              <input type="radio" name="nicht-abgeholt-grund" value={g.key}
                     checked={grund === g.key} onChange={() => setGrund(g.key)}
                     data-testid={`nicht-abgeholt-grund-${g.key}`} />
              {g.label}
            </label>
          ))}
        </div>
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} maxLength={1500}
                  data-testid="nicht-abgeholt-text"
                  placeholder={grund === "sonstiges" ? "Bitte kurz erklären *" : "Details für den Händler (optional)"}
                  className="mt-3 w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none"
                  style={{ borderColor: "rgba(255,255,255,0.15)" }} />
        <div className="mt-2 text-[11px] text-zinc-500">
          Der Grund geht an den Händler. Inseratsfotos werden nach 14 Tagen automatisch gelöscht.
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2">
          <button type="button" onClick={onAbbrechen} disabled={busy}
                  className="rounded-xl py-3 text-sm border disabled:opacity-50"
                  style={{ borderColor: "rgba(255,255,255,0.15)" }}>
            Abbrechen
          </button>
          <button type="button" onClick={() => notiz && onSenden(notiz)} disabled={busy || !notiz}
                  data-testid="nicht-abgeholt-senden"
                  className="rounded-xl py-3 text-sm font-semibold text-white disabled:opacity-40"
                  style={{ background: "var(--accent-red, #FF3B30)" }}>
            Als nicht abgeholt melden
          </button>
        </div>
      </div>
    </div>
  );
}

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
  // Pruefung 14.09.2026 (A1): Ladefehler getrennt merken — vorher stand nach
  // einem Funkloch "Noch keine Fahrten", obwohl Fahrten zugeteilt waren.
  const [ladeFehler, setLadeFehler] = useState(null);
  // Prüfbericht 20.09. V-34: der Server deckelt bei 500 Fahrten (offene haben
  // Vorrang, alte abgeschlossene fallen weg) und meldet das per X-Truncated.
  const [gekuerzt, setGekuerzt] = useState(false);
  // Rollenprüfung 22.09.2026 (RP-062/RP-161): Das Protokoll schickt nach einer
  // geänderten Fahrt hierher (/fahrer?fahrt=<id>) — genau diese Fahrt gleich
  // aufgeklappt zeigen, damit der Fahrer sie prüfen und erneut annehmen kann.
  const [zielFahrt] = useState(() => {
    try { return new URLSearchParams(window.location.search).get("fahrt") || ""; }
    catch { return ""; }
  });
  const [open, setOpen] = useState(() => (zielFahrt ? { [zielFahrt]: true } : {}));
  const [busy, setBusy] = useState(null);
  const [checkAppt, setCheckAppt] = useState(null); // Abhol-Check-Dialog
  const [nichtAbgeholt, setNichtAbgeholt] = useState(null); // RP-537: Grund-Dialog
  const navigate = useNavigate();

  const laden = () => {
    setLoading(true);
    setLadeFehler(null);
    return driverApi.get("/driver/appointments")
      .then((r) => { setItems(r.data); setGekuerzt(listeGekuerzt(r)); setLadeFehler(null); })
      .catch((e) => setLadeFehler(errMsg(e, "Termine konnten nicht geladen werden")))
      .finally(() => setLoading(false));
  };
  useEffect(() => { laden(); }, []);

  // RP-464: still nachladen (kein "Lade …", kein Fehler-Toast im Funkloch) —
  // alle 60 s, solange die App sichtbar ist, und beim Zurückkehren in die App.
  const stillLaeuft = useRef(false);
  useEffect(() => {
    const still = () => {
      if (document.visibilityState !== "visible" || stillLaeuft.current) return;
      stillLaeuft.current = true;
      driverApi.get("/driver/appointments")
        .then((r) => { setItems(r.data); setGekuerzt(listeGekuerzt(r)); setLadeFehler(null); })
        .catch(() => { /* nächster Takt versucht es erneut */ })
        .finally(() => { stillLaeuft.current = false; });
    };
    const takt = setInterval(still, NACHLADEN_MS);
    const sichtbar = () => { if (document.visibilityState === "visible") still(); };
    document.addEventListener("visibilitychange", sichtbar);
    window.addEventListener("focus", sichtbar);
    return () => {
      clearInterval(takt);
      document.removeEventListener("visibilitychange", sichtbar);
      window.removeEventListener("focus", sichtbar);
    };
  }, []);

  // Pruefung 14.09.2026 (A2): Nach einer gelungenen Aenderung die Liste neu
  // laden — scheitert NUR das Nachladen, gibt es keinen widerspruechlichen
  // Fehler-Toast mehr ("Statuswechsel fehlgeschlagen" nach "Als … markiert").
  const nachladen = async () => {
    try {
      const r = await driverApi.get("/driver/appointments");
      setItems(r.data);
    } catch (e) {
      toast.warning(errMsg(e, "Liste konnte nicht aktualisiert werden — bitte neu laden"));
    }
  };

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
      // Runde 12: Stand der angezeigten Fahrt mitschicken — die Zusage gilt genau dafür.
      const stand = items.find((a) => a.id === id)?.updated_at;
      await driverApi.put(`/driver/appointments/${id}/zuteilung`,
        { action, grund, ...(stand ? { stand } : {}) });
      toast.success(action === "annehmen" ? "Fahrt angenommen" : "Fahrt abgelehnt — der Händler wurde informiert");
    } catch (e) {
      toast.error(errMsg(e, "Antwort fehlgeschlagen"));
      setBusy(null);
      return;
    }
    await nachladen();
    setBusy(null);
  };

  // Erster Abhol-Bericht: der Server nimmt ihn bis 24 h nach "abgeholt" an
  // (routes/drivers.driver_submit_report) — dieselbe Frist wie dort.
  const abholCheckNoch = (a) => {
    const t = Date.parse(a.status_changed_at || "");
    return Number.isFinite(t) && Date.now() - t <= 24 * 60 * 60 * 1000;
  };

  const setStatus = async (id, status) => {
    if (busy) return;
    // "abgeholt" läuft über den Abhol-Check-Dialog (mit Abweichungsbericht).
    if (status === "abgeholt") {
      const appt = items.find((a) => a.id === id);
      if (!appt) return;
      // Rollenprüfung 22.09.2026 (RP-064/RP-163): ERST prüfen, ob das
      // Protokoll unterschrieben ist — vorher öffnete der Dialog sofort, und
      // erst beim Absenden hieß es "Protokoll fehlt": km, Abweichungen und
      // Fotos waren weg. Ohne Verbindung (null) entscheidet wie bisher die
      // Prüfung beim Absenden.
      setBusy(id);
      const final = await protokollIstFinal(id);
      setBusy(null);
      if (final === false) {
        toast.warning("Bitte zuerst das Abholprotokoll ausfüllen und unterschreiben — "
                      + "danach den Abhol-Check.");
        navigate(`/fahrer/protokoll/${id}`);
        return;
      }
      setCheckAppt(appt);
      return;
    }
    // RP-537: "Nicht abgeholt" nur mit Grund (Dialog statt confirm).
    setNichtAbgeholt(items.find((a) => a.id === id) || { id });
  };

  const nichtAbgeholtSenden = async (id, notiz) => {
    if (busy) return;
    setBusy(id);
    try {
      const stand = items.find((a) => a.id === id)?.updated_at;
      await driverApi.put(`/driver/appointments/${id}/status`,
        { status: "nicht abgeholt", notes: notiz, ...(stand ? { stand } : {}) });
      toast.success("Als nicht abgeholt markiert — der Händler sieht den Grund");
      setNichtAbgeholt(null);
    } catch (e) {
      toast.error(errMsg(e, "Statuswechsel fehlgeschlagen"));
      setBusy(null);
      return;
    }
    await nachladen();
    setBusy(null);
  };

  const grouped = useMemo(() => {
    const g = {};
    (items || []).forEach((a) => {
      const k = dayKey(a.pickup_date);
      (g[k] = g[k] || []).push(a);
    });
    // Pruefung 14.09.2026 (A7): innerhalb eines Tages nach Uhrzeit — vorher
    // stand die 14-Uhr-Fahrt je nach Datenbankreihenfolge vor der 9-Uhr-Fahrt.
    Object.values(g).forEach((liste) =>
      liste.sort((a, b) => String(a.pickup_time || "").localeCompare(String(b.pickup_time || ""))));
    return Object.entries(g).sort(([a], [b]) => a.localeCompare(b));
  }, [items]);

  const oeffnePdf = (path) =>
    openDriverPdf(path).catch((e) => toast.error(errMsg(e)));

  // RP-062: die Ziel-Fahrt einmal in den Blick holen, sobald sie geladen ist.
  const zielGezeigt = useRef(false);
  useEffect(() => {
    if (!zielFahrt || zielGezeigt.current || !items.some((a) => a.id === zielFahrt)) return;
    zielGezeigt.current = true;
    try {
      document.querySelector(`[data-testid="appt-${CSS.escape(zielFahrt)}"]`)
        ?.scrollIntoView?.({ block: "start", behavior: "smooth" });
    } catch { /* nur Komfort */ }
  }, [zielFahrt, items]);

  return (
    <div data-testid="driver-dashboard">
      <div className="overline">Meine Fahrten</div>
      <h1 className="font-display font-black text-2xl tracking-tighter mb-5">
        {items.length} {items.length === 1 ? "Termin" : "Termine"}
      </h1>

      {loading && (
        <div className="tactical-card p-8 text-center text-zinc-500 text-sm">Lade …</div>
      )}

      {!loading && ladeFehler && (
        <div className="tactical-card p-8 text-center" data-testid="driver-ladefehler">
          <XCircle size={32} className="mx-auto" style={{ color: "var(--accent-red)" }} />
          <div className="mt-3 font-semibold text-zinc-300">Termine konnten nicht geladen werden</div>
          <div className="mt-1 text-xs text-zinc-500">{ladeFehler}</div>
          <button onClick={laden} data-testid="driver-erneut-laden"
                  className="mt-4 px-4 py-2 rounded-sm text-xs font-semibold bg-white/10 hover:bg-white/20">
            Erneut versuchen
          </button>
        </div>
      )}

      {gekuerzt && (
        <div className="tactical-card px-4 py-2 mb-4 text-xs text-zinc-400" data-testid="driver-gekuerzt">
          Nicht alle Fahrten angezeigt — ältere abgeschlossene Fahrten fehlen, offene stehen alle drin.
        </div>
      )}

      {!loading && !ladeFehler && items.length === 0 && (
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
                                  style={{ background: "var(--wa-05)" }}>
                              {a.status}
                            </span>
                          )}
                        </div>
                        <div className="font-display font-bold text-lg tracking-tight mt-1 truncate">
                          {v.make || "Fahrzeug"} {v.model || ""}
                        </div>
                        <div className="text-xs text-zinc-500 flex items-center gap-1 mt-0.5">
                          <MapPin size={11} className="flex-shrink-0" />
                          <span className="truncate">
                            {a.pickup_address || a.seller_name || "—"}
                            {a.kontakt_nach_annahme && " · Adresse und Kontakt nach Annahme"}
                          </span>
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
                            direkt in der App ausfüllbar inkl. Unterschrift.
                            RP-235: nicht bei stornierten Fahrten (Server: 404). */}
                        {a.zuteilung !== "offen" && a.status !== "storniert" && (
                        <Link to={`/fahrer/protokoll/${a.id}`}
                              data-testid={`protokoll-${a.id}`}
                              className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-bold kinetic-button mb-2">
                          <ClipboardCheck size={16} /> Protokoll ausfüllen
                        </Link>
                        )}

                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                          {a.status !== "storniert" && (
                          <button onClick={() => oeffnePdf(`/driver/appointments/${a.id}/pickup-order.pdf`)}
                                  data-testid={`pickup-pdf-${a.id}`}
                                  className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm border"
                                  style={{ borderColor: "var(--border-default)" }}>
                            <FileText size={15} /> Papier-PDF
                          </button>
                          )}
                          {a.contract_id && (
                            <button onClick={() => oeffnePdf(`/driver/contracts/${a.contract_id}/pdf`)}
                                    data-testid={`contract-pdf-${a.id}`}
                                    className="flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-semibold bg-white/5 hover:bg-white/10">
                              <FileText size={15} /> Kaufvertrag
                            </button>
                          )}
                        </div>

                        {a.beweis_id && (
                          <button onClick={() => oeffnePdf(`/driver/beweise/${a.beweis_id}/pdf`)}
                                  data-testid={`beweis-pdf-${a.id}`}
                                  className="flex items-center justify-center gap-2 px-4 py-2 rounded-sm text-xs font-semibold bg-white/5 hover:bg-white/10">
                            <CheckCircle2 size={13} /> Beweisdokument (Inserat-PDF)
                          </button>
                        )}
                        {a.snapshot_id && (
                          <button onClick={() => oeffnePdf(`/driver/snapshots/${a.snapshot_id}/pdf`)}
                                  data-testid={`snapshot-alt-pdf-${a.id}`}
                                  className="flex items-center justify-center gap-2 px-4 py-2 rounded-sm text-xs font-semibold bg-white/5 hover:bg-white/10">
                            <CheckCircle2 size={13} /> Beweis-Aufnahme (vor 10.09.2026)
                          </button>
                        )}

                        {a.notes && (
                          <div className="text-xs text-zinc-400 p-3 rounded-sm"
                               style={{ background: "var(--wa-02)" }}>
                            <div className="overline mb-1">Notizen</div>
                            {a.notes}
                          </div>
                        )}
                        {/* RP-236: Chef-Notizen gibt es erst nach der Annahme. */}
                        {a.notizen_nach_annahme && (
                          <div className="text-[11px] text-zinc-500" data-testid={`notizen-nach-annahme-${a.id}`}>
                            Der Händler hat Notizen hinterlegt — sichtbar nach der Annahme.
                          </div>
                        )}

                        {/* Zuteilung: erst annehmen oder ablehnen (09/2026) */}
                        {a.zuteilung === "offen" && !GESCHLOSSEN.includes(a.status) && (
                          <div className="pt-2 border-t" style={{ borderColor: "var(--border-default)" }}
                               data-testid={`zuteilung-${a.id}`}>
                            <div className="text-xs mb-2 font-semibold"
                                 style={{ color: a.zuteilung_neu_wegen_aenderung ? "var(--accent-red)" : "var(--accent-green)" }}>
                              {a.zuteilung_neu_wegen_aenderung
                                ? "Fahrt wurde geändert (Datum, Uhrzeit, Adresse, Fahrzeug oder Verkäufer) — bitte prüfen und erneut bestätigen"
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
                                      style={{ background: "rgba(255,59,48,0.1)", color: "var(--tx-rot)", border: "1px solid rgba(255,59,48,0.25)" }}>
                                <XCircle size={15} /> Ablehnen
                              </button>
                            </div>
                          </div>
                        )}
                        {/* Status-Aktionen (Fahrer markiert Ergebnis) — erst nach Annahme */}
                        {/* RP-235: auch nicht bei stornierten/erledigten Fahrten (Server: 409). */}
                        {a.zuteilung !== "offen" && !GESCHLOSSEN.includes(a.status) && (
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
                                       color: "var(--tx-rot)",
                                       border: "1px solid rgba(255,59,48,0.25)" }}
                            >
                              <XCircle size={15} /> Nicht abgeholt
                            </button>
                          </div>
                        )}
                        {(a.status === "abgeholt" || a.status === "nicht abgeholt") && (
                          <div className="text-xs text-zinc-500 p-3 rounded-sm text-center"
                               style={{ background: "var(--wa-02)" }}>
                            {a.status === "abgeholt" ? "✓ Als abgeholt markiert" : "✕ Als nicht abgeholt markiert"}
                            {" · "}Fotos & Beweis-Archiv werden automatisch gelöscht
                          </div>
                        )}
                        {(a.status === "storniert" || a.status === "erledigt") && (
                          <div className="text-xs text-zinc-500 p-3 rounded-sm text-center"
                               data-testid={`fahrt-geschlossen-${a.id}`}
                               style={{ background: "var(--wa-02)" }}>
                            {a.status === "storniert"
                              ? "✕ Fahrt storniert — Kontakt und Unterlagen stehen nicht mehr zur Verfügung"
                              : "✓ Vom Händler als erledigt markiert"}
                          </div>
                        )}
                        {/* 19.09.2026 (sichtbarer Mangel 4): Nach dem unterschriebenen
                            Protokoll steht die Fahrt auf "abgeholt" — der Abhol-Check
                            (km, Schluessel, Tank, Fotos) war dann nicht mehr erreichbar,
                            obwohl der Server den ERSTEN Bericht 24 h lang annimmt. */}
                        {a.status === "abgeholt" && !a.bericht_vorhanden && abholCheckNoch(a) && (
                          <button onClick={() => setCheckAppt(a)} disabled={busy === a.id}
                                  data-testid={`abholcheck-nachtragen-${a.id}`}
                                  className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-sm text-sm font-semibold disabled:opacity-50"
                                  style={{ background: "rgba(52,199,89,0.14)", color: "var(--accent-green)",
                                           border: "1px solid rgba(52,199,89,0.3)" }}>
                            <CheckCircle2 size={15} /> Abhol-Check nachtragen (km, Schlüssel, Tank, Fotos)
                          </button>
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

      {nichtAbgeholt && (
        <NichtAbgeholtDialog fahrt={nichtAbgeholt} busy={busy === nichtAbgeholt.id}
                             onAbbrechen={() => setNichtAbgeholt(null)}
                             onSenden={(notiz) => nichtAbgeholtSenden(nichtAbgeholt.id, notiz)} />
      )}

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
