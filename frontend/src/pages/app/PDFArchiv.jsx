import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { thumbSrc, thumbFehler } from "@/lib/bilder";
import { toast } from "sonner";
import { Search, Trash2, Eye, X, Car, ChevronLeft, ChevronRight, MapPin, FileText, Send, Mail, CalendarPlus, UserRoundPen } from "lucide-react";
import { openContractPdf } from "@/lib/pdf";
import { openAuthedFile } from "@/lib/api";
import BeweisCard from "@/components/BeweisCard";
import SendDialog, { abholterminAnlegen, TERMIN_MELDUNG } from "@/components/SendDialog";
import FolgeMailDialog from "@/components/FolgeMailDialog";
import VerkaeuferKorrekturDialog from "@/components/VerkaeuferKorrekturDialog";

// Rollenprüfung 22.09.2026 (RP-007/RP-106/RP-257): Verträge je Seite.
export const ARCHIV_SEITE = 50;

// Rollenprüfung 22.09.2026 (RP-417): Braucht dieser Vertrag einen neuen
// Abholtermin? Nur, wenn der Server sagt, dass KEIN offener existiert, und
// der Kauf weder storniert noch abgeholt ist.
export function brauchtAbholtermin(it) {
  if (!it || it.termin_offen !== false) return false;
  return !["storniert", "abgeholt"].includes(it.kaufvorgang_status);
}

// Pruefbericht 20.09.2026 (H23): Jeder Status stand als GRUENE Pille da —
// auch "versand_vorbereitet", den das Backend bewusst setzt, weil WhatsApp
// nur den Chat oeffnet. Wer nie auf Senden getippt hat, hielt den Vertrag
// fuer zugestellt.
const VERTRAG_STATUS = {
  versendet:           { text: "versendet", farbe: "var(--accent-green)", grund: "rgba(52,199,89,0.12)", rand: "rgba(52,199,89,0.25)" },
  versand_vorbereitet: { text: "WhatsApp geöffnet – Versand nicht bestätigt", farbe: "var(--tx-amber)", grund: "rgba(255,159,10,0.12)", rand: "rgba(255,159,10,0.35)" },
  "neu erstellt":      { text: "neu erstellt – noch nicht versendet", farbe: "var(--st-blau)", grund: "rgba(10,132,255,0.12)", rand: "rgba(10,132,255,0.3)" },
  erstellt:            { text: "erstellt – noch nicht versendet", farbe: "var(--text-secondary)", grund: "var(--wa-04)", rand: "var(--border-default)" },
};
export function vertragStatus(status) {
  return VERTRAG_STATUS[status] || { text: status || "—", farbe: "var(--text-secondary)",
                                     grund: "var(--wa-04)", rand: "var(--border-default)" };
}

const DAY_FILTERS = [
  { v: 0, l: "Alle" },
  { v: 14, l: "14 Tage" },
  { v: 30, l: "30 Tage" },
  { v: 60, l: "60 Tage" },
  { v: 90, l: "90 Tage" },
];

export default function PDFArchiv() {
  const nav = useNavigate();
  const [items, setItems] = useState([]);
  const [q, setQ] = useState("");
  const [days, setDays] = useState(0);
  const [loading, setLoading] = useState(true);
  const [gallery, setGallery] = useState(null); // {item, urls, index}
  // Wunsch Ahmad 19.09.2026: Wurde der Vertrag nach der Abholung mit neuem
  // Preis/neuen Daten neu erstellt, fragt die App, ob er an den Verkaeufer
  // geht (WhatsApp oder E-Mail) — derselbe Dialog wie beim ersten Versand.
  const [senden, setSenden] = useState(null);
  // Wunsch Ahmad 20.09.2026: die drei nachtraeglichen Mails (Korrektur,
  // Hinweis nach Kaufabschluss, Bahnverbindung) — von Hand, nie automatisch.
  const [folgeMail, setFolgeMail] = useState(null);
  // Entscheidung Ahmad 22.09.2026 (RP-481): Verkaeuferdaten eines Vertrags
  // korrigieren -> neue Fassung, danach direkt der Versand-Dialog.
  const [korrektur, setKorrektur] = useState(null);

  // Runde 16 (15.09.2026): ein Ladefehler sah aus wie "keine Vertraege", und
  // die Kuerzung des Servers (X-Truncated ab 2.000) blieb unsichtbar.
  const [ladeFehler, setLadeFehler] = useState(false);
  // Rollenprüfung 22.09.2026 (RP-007/RP-106/RP-257): Das Archiv lud bis zu
  // 2.000 Verträge in EINEM Aufruf, und jede Karte stellte dazu zwei
  // Beweis-Abfragen — bei vielen Verträgen tausende Anfragen. Jetzt
  // seitenweise (SEITE Verträge, "Weitere laden" über den Cursor des
  // Servers), und die Beweis-Karte lädt erst, wenn sie sichtbar wird.
  const [weiterAb, setWeiterAb] = useState("");
  const [laedtMehr, setLaedtMehr] = useState(false);
  // M20: nur die letzte Anfrage zaehlt (Zeitraum schnell gewechselt).
  const anfrageNr = useRef(0);
  // Rollenprüfung 22.09.2026 (Review): "Weitere Verträge laden" nahm die
  // GERADE getippte (noch nicht mit Enter gesuchte) Suche zusammen mit dem
  // Cursor der alten Liste — Treffer zweier Abfragen standen gemischt da.
  // Jetzt merkt sich load(), womit die erste Seite geladen wurde, und
  // mehrLaden fragt genau damit weiter.
  const geladenMit = useRef({ q: "", days: 0 });
  const holen = async ({ before = "", suche = q, zeitraum = days } = {}) => {
    const params = { limit: ARCHIV_SEITE };
    if (suche) params.q = suche;
    if (zeitraum) params.days = zeitraum;
    if (before) params.before = before;
    const r = await api.get("/contracts", { params });
    const naechste = String(r.headers?.["x-next-before"] || "");
    const mehr = String(r.headers?.["x-truncated"] || "") === "1";
    return { liste: Array.isArray(r.data) ? r.data : [], weiter: mehr ? naechste : "" };
  };
  const load = async () => {
    const nr = ++anfrageNr.current;
    const mit = { q, days };
    setLoading(true);
    setLadeFehler(false);
    try {
      const { liste, weiter } = await holen({ suche: mit.q, zeitraum: mit.days });
      if (nr !== anfrageNr.current) return;
      geladenMit.current = mit;
      setItems(liste);
      setWeiterAb(weiter);
    } catch (e) {
      if (nr !== anfrageNr.current) return;
      // Pruefbericht 20.09.2026 (B13): errMsg war hier nicht importiert —
      // der Fehlerzweig warf selbst einen Fehler.
      setLadeFehler(true);
      toast.error(errMsg(e, "Verträge konnten nicht geladen werden"));
    } finally {
      if (nr === anfrageNr.current) setLoading(false);
    }
  };
  const mehrLaden = async () => {
    if (!weiterAb || laedtMehr) return;
    const nr = anfrageNr.current;
    setLaedtMehr(true);
    try {
      const { q: suche, days: zeitraum } = geladenMit.current;
      const { liste, weiter } = await holen({ before: weiterAb, suche, zeitraum });
      if (nr !== anfrageNr.current) return;   // inzwischen neu gesucht
      setItems((alt) => {
        const bekannt = new Set(alt.map((i) => i.id));
        return [...alt, ...liste.filter((i) => !bekannt.has(i.id))];
      });
      setWeiterAb(weiter);
    } catch (e) {
      toast.error(errMsg(e, "Weitere Verträge konnten nicht geladen werden"));
    } finally {
      setLaedtMehr(false);
    }
  };

  // Rollenprüfung 22.09.2026 (RP-417): Verträge ohne offenen Abholtermin
  // (ohne Abholdatum erstellt, oder "nicht abgeholt") bekommen hier einen —
  // vorher ging das nur versteckt über "Senden" → "Speichern & Termin".
  const [terminLaeuft, setTerminLaeuft] = useState("");
  const terminAnlegen = async (it) => {
    if (terminLaeuft) return;
    setTerminLaeuft(it.id);
    try {
      const erg = await abholterminAnlegen(it);
      if (erg.angelegt) {
        toast.success("Abholtermin angelegt — Datum und Uhrzeit im Terminplaner eintragen.");
        if (erg.hinweis) toast.warning(erg.hinweis, { duration: 10000 });
        nav("/app/termine");
      } else {
        toast.info(TERMIN_MELDUNG[erg.grund] || TERMIN_MELDUNG.offen);
        load();
      }
    } catch (e) {
      toast.error(errMsg(e, "Abholtermin konnte nicht angelegt werden"));
    } finally {
      setTerminLaeuft("");
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [days]);

  // Pruefbericht 20.09.2026 (B15/F14): Fehler beim Oeffnen blieben stumm,
  // und jeder weitere Klick startete einen neuen, bis zu 180 s langen Abruf.
  // Jetzt: Knopf gesperrt, solange geladen wird, Fehler als Meldung.
  const [pdfLaeuft, setPdfLaeuft] = useState("");
  const pdfOeffnen = async (id, variante) => {
    const name = `${id}:${variante}`;
    if (pdfLaeuft) return;
    setPdfLaeuft(name);
    try {
      await openContractPdf(id, variante === "digital" ? { variante: "digital" } : undefined);
    } catch (e) {
      toast.error(errMsg(e, "Kaufvertrag konnte nicht geladen werden"));
    } finally {
      setPdfLaeuft("");
    }
  };
  const openPdf = (id) => pdfOeffnen(id, "druck");
  // Digitale Fassung: ohne Unterschriftsfelder, mit dem digitalen Vertragstext
  // (das ist die Fassung, die per E-Mail/WhatsApp verschickt wird).
  const openDigital = (id) => pdfOeffnen(id, "digital");

  // Pruefbericht 20.09.2026 (B14/F13): ohne try/catch verschwanden 403
  // ("nur der Hauptaccount") und 409 ("Abholprotokoll laeuft") spurlos.
  const [loeschtId, setLoeschtId] = useState("");
  const remove = async (id) => {
    if (loeschtId) return;
    if (!window.confirm("Vertrag wirklich löschen?")) return;
    setLoeschtId(id);
    try {
      await api.delete(`/contracts/${id}`);
      toast.success("Gelöscht");
      load();
    } catch (e) {
      if (e?.response?.status === 404) {
        toast.info("Diesen Vertrag gibt es schon nicht mehr.");
        load();
      } else {
        toast.error(errMsg(e, "Vertrag konnte nicht gelöscht werden"), { duration: 10000 });
      }
    } finally {
      setLoeschtId("");
    }
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-6xl mx-auto" data-testid="pdfs-page">
      <div className="overline">Verträge / PDFs</div>
      <h1 className="font-display font-black text-4xl lg:text-5xl tracking-tighter mt-1">Vertragsarchiv</h1>

      {/* Suche + Zeitraum als Apple-Segmentleiste */}
      <div className="mt-8 flex flex-wrap gap-3 items-center">
        <div className="flex-1 min-w-[260px] flex items-center gap-3 apple-input !rounded-full !py-3 !px-5">
          <Search size={17} className="shrink-0" style={{ color: "var(--text-muted)" }} />
          <input data-testid="pdf-search-input" value={q} onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && load()}
                 placeholder="Suche nach Marke / Modell / Verkäufer / Vertragsnummer"
                 className="bg-transparent outline-none w-full text-[15px]" />
        </div>
        <div className="flex items-center gap-1 rounded-full p-1"
             style={{ background: "var(--apple-btn-secondary-bg)" }}>
          {DAY_FILTERS.map((d) => (
            <button key={d.v} onClick={() => setDays(d.v)} data-testid={`filter-days-${d.v}`}
                    className={`px-4 py-2 rounded-full text-[13px] font-semibold transition-colors ${
                      days === d.v ? "bg-white/15 text-white shadow-sm" : "hover:text-white"
                    }`}
                    style={days === d.v ? {} : { color: "var(--text-muted)" }}>
              {d.l}
            </button>
          ))}
        </div>
        <button onClick={load}
                className="apple-btn apple-btn-secondary !rounded-full !px-5 !py-2.5">
          Aktualisieren
        </button>
      </div>

      {/* Vertragsliste als grosse Apple-Karten */}
      <div className="mt-8 space-y-4" data-testid="pdfs-table">
        {loading && (
          <div className="apple-surface p-12 text-center text-[15px]"
               style={{ color: "var(--text-muted)" }}>Lade…</div>
        )}
        {!loading && ladeFehler && (
          <div className="apple-surface p-12 text-center text-[15px]" data-testid="pdfs-fehler"
               style={{ color: "var(--text-muted)" }}>
            Verträge konnten nicht geladen werden — bitte neu laden.
          </div>
        )}
        {!loading && !ladeFehler && items.length === 0 && (
          <div className="apple-surface p-12 text-center text-[15px]"
               style={{ color: "var(--text-muted)" }}>Noch keine Verträge erstellt.</div>
        )}
        {items.map((it) => (
          <div key={it.id}
               className="apple-surface p-5 sm:p-6 transition-colors hover:border-white/20"
               data-testid={`pdf-card-${it.id}`}>
            <div className="flex flex-col sm:flex-row sm:items-center gap-5">
              {/* Grosses Fahrzeugbild */}
              <VehicleThumb
                item={it}
                onOpen={(urls, index) => setGallery({ item: it, urls, index })}
              />

              {/* Hauptbereich */}
              {/* Rollenprüfung 22.09.2026 (RP-038/RP-137/RP-288): Inhalt und
                  Aktionsspalte standen immer nebeneinander — am Handy (375 px)
                  blieben neben fünf 40-px-Knöpfen rund 60 px für Fahrzeug,
                  Verkäufer und Hinweise. Unter sm stehen Preis und Knöpfe
                  jetzt UNTER dem Inhalt, die Knopfleiste bricht um. */}
              <div className="flex-1 min-w-0">
                <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="font-display font-bold text-xl tracking-tight truncate">
                        {it.make || "—"} {it.model || ""}
                      </span>
                      <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
                            data-testid={`pdf-status-${it.id}`}
                            style={{ background: vertragStatus(it.status).grund,
                                     border: `1px solid ${vertragStatus(it.status).rand}`,
                                     color: vertragStatus(it.status).farbe }}>
                        {vertragStatus(it.status).text}
                      </span>
                      {(it.version || 1) > 1 && (
                        <span className="text-[11px] font-semibold px-2 py-1 rounded-full"
                              title={`${it.version}. Fassung — ältere Fassungen unten abrufbar`}
                              style={{ background: "rgba(10,132,255,0.12)",
                                       border: "1px solid rgba(10,132,255,0.3)",
                                       color: "var(--st-blau)" }}>
                          v{it.version}
                        </span>
                      )}
                    </div>
                    <SpecsZeile cd={it.contract_data} />
                    <div className="mt-1.5 text-[13.5px] leading-relaxed"
                         style={{ color: "var(--text-secondary)" }}>
                      {it.seller_name}
                      <span style={{ color: "var(--text-muted)" }}>
                        {" "}· erstellt {new Date(it.created_at).toLocaleString("de-DE",
                          { day: "2-digit", month: "2-digit", year: "numeric",
                            hour: "2-digit", minute: "2-digit" })}
                        {it.created_by_name && <> von{" "}
                          <span className={it.created_by_role === "sucher" ? "text-sky-400" : ""}>
                            {it.created_by_name}
                          </span></>}
                      </span>
                    </div>
                    <AbholZeile item={it} />
                    {/* Wunsch Ahmad 21.09.2026: "Notizen (intern)" stehen nicht mehr im
                        Kundenvertrag (beide Fassungen gehen an den Verkaeufer) — hier
                        bleiben sie fuer das Buero sichtbar. */}
                    {String(it.contract_data?.notes || "").trim() && (
                      <div className="mt-1.5 text-[12.5px] leading-relaxed whitespace-pre-line"
                           data-testid={`vertrag-notiz-${it.id}`}
                           style={{ color: "var(--text-muted)" }}>
                        <span className="font-semibold">Notiz (intern): </span>
                        {String(it.contract_data.notes).trim()}
                      </div>
                    )}
                    <NachAbholungHinweis item={it} onSenden={() => setSenden(it)} />
                    <div className="mt-3">
                      {it.vehicle_id ? (
                        // RP-007: lädt erst, wenn die Karte sichtbar wird
                        <BeweisCard vehicleId={it.vehicle_id} compact lazy />
                      ) : (
                        <span className="text-xs" style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </div>
                    {(it.version || 1) > 1 && <VersionenZeile contractId={it.id} />}
                  </div>

                  {/* Preis + Aktionen rechts (am Handy darunter) */}
                  <div className="sm:shrink-0 flex flex-row sm:flex-col flex-wrap items-center sm:items-end justify-between gap-3"
                       data-testid={`pdf-aktionen-${it.id}`}>
                    <div className="font-display font-black text-2xl tracking-tight whitespace-nowrap">
                      {it.purchase_price
                        ? `${Number(it.purchase_price).toLocaleString("de-DE")} €` : "—"}
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      {brauchtAbholtermin(it) && (
                        <button onClick={() => terminAnlegen(it)}
                                data-testid={`termin-anlegen-${it.id}`}
                                disabled={!!terminLaeuft}
                                className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10 disabled:opacity-50"
                                style={{ background: "var(--apple-btn-secondary-bg)",
                                         color: "var(--text-primary)" }}
                                aria-label="Abholtermin anlegen"
                                title="Abholtermin anlegen — zu diesem Vertrag gibt es keinen offenen Termin">
                          <CalendarPlus size={16} />
                        </button>
                      )}
                      <button onClick={() => openPdf(it.id)} data-testid={`open-pdf-${it.id}`}
                              disabled={!!pdfLaeuft} aria-busy={pdfLaeuft === `${it.id}:druck`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10 disabled:opacity-50"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-primary)" }}
                              title="Vertrag öffnen (Druckfassung mit Unterschriftsfeldern)">
                        <Eye size={16} />
                      </button>
                      <button onClick={() => openDigital(it.id)} data-testid={`open-pdf-digital-${it.id}`}
                              disabled={!!pdfLaeuft} aria-busy={pdfLaeuft === `${it.id}:digital`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10 disabled:opacity-50"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-primary)" }}
                              title="Digitale Fassung (für E-Mail/WhatsApp, ohne Unterschriftsfelder)">
                        <FileText size={16} />
                      </button>
                      {/* Pruefbericht 20.09.2026 (H24): Aus dem Archiv liess sich ein
                          Vertrag gar nicht erneut versenden — meldete sich der
                          Verkaeufer einen Tag spaeter, blieb nur ein neuer Vertrag. */}
                      <button onClick={() => setSenden(it)}
                              data-testid={`senden-${it.id}`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-primary)" }}
                              title="Vertrag an den Verkäufer senden (WhatsApp oder E-Mail)">
                        <Send size={16} />
                      </button>
                      <button onClick={() => setFolgeMail(it)}
                              data-testid={`folgemail-${it.id}`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-primary)" }}
                              aria-label="Hinweis nach Kaufabschluss und Bahnverbindung"
                              title="Hinweis nach Kaufabschluss / Bahnverbindung — per E-Mail verschicken oder kopieren, mit Name und Daten dieses Vertrags">
                        <Mail size={16} />
                      </button>
                      <button onClick={() => setKorrektur(it)}
                              data-testid={`verkaeufer-korrektur-${it.id}`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-primary)" }}
                              aria-label="Verkäuferdaten korrigieren"
                              title="Verkäuferdaten korrigieren (Name, Anschrift, Kontakt) — erzeugt eine neue Fassung">
                        <UserRoundPen size={16} />
                      </button>
                      <button onClick={() => remove(it.id)} data-testid={`del-pdf-${it.id}`}
                              disabled={!!loeschtId}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-red-500/20 disabled:opacity-50"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-secondary)" }}
                              title="Löschen">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ))}
        {/* RP-007: nächste Seite nur auf Wunsch */}
        {!loading && !ladeFehler && weiterAb && (
          <div className="flex justify-center pt-2">
            <button onClick={mehrLaden} disabled={laedtMehr} data-testid="pdfs-mehr-laden"
                    className="apple-btn apple-btn-secondary !rounded-full !px-6 !py-2.5 disabled:opacity-60">
              {laedtMehr ? "Lade…" : "Weitere Verträge laden"}
            </button>
          </div>
        )}
      </div>

      {senden && (
        // Pruefbericht 20.09.2026 (U-92): key je Vertrag — sonst behielt der
        // Dialog Empfaenger und Zustand des zuvor gesendeten Vertrags.
        <SendDialog open contract={senden} key={senden.id}
                    onClose={() => { setSenden(null); load(); }} />
      )}

      {folgeMail && (
        <FolgeMailDialog open contract={folgeMail}
                         onClose={(gesendet) => { setFolgeMail(null); if (gesendet) load(); }} />
      )}

      {korrektur && (
        <VerkaeuferKorrekturDialog open contract={korrektur}
          onClose={(erg) => {
            const it = korrektur;
            setKorrektur(null);
            if (erg?.geaendert) {
              // Gleich weiter zum Versand: neue Fassung, Empfaenger aus der
              // Korrektur; der Versand-Dialog erkennt die fruehere Fassung im
              // send_status und nimmt die Korrektur-Vorlage.
              setSenden({ ...it, ...(erg.verkaeufer || {}), version: erg.version });
            }
            if (erg) load();
          }} />
      )}

      {/* Foto-Galerie: großes Bild, blättern mit Pfeilen / Tastatur / Wischen */}
      {gallery && (
        <GalleryViewer
          item={gallery.item}
          urls={gallery.urls}
          startIndex={gallery.index || 0}
          onClose={() => setGallery(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------ Sub-components ----------------------------- */

const FELD_NAMEN = {
  vehicle_make: "Marke", vehicle_model: "Modell",
  vehicle_first_registration: "Erstzulassung", vehicle_vin: "FIN",
  vehicle_color: "Farbe", vehicle_fuel: "Kraftstoff", vehicle_mileage: "Kilometer",
  previous_owners: "Halter", hu_until: "HU", commercial_since_ez: "gewerblich",
  accident_free: "unfallfrei",
};

// Pruefbericht 20.09.2026 (U-88): Was nach der Abholung anders ist — als
// Liste fuer den Hinweis (auch ohne Versandmerker, siehe unten).
export function nachAbholungTeile(ae) {
  if (!ae || typeof ae !== "object") return [];
  const teile = [];
  if (ae.preis != null) {
    const alt = ae.preis_vorher != null
      ? `${Number(ae.preis_vorher).toLocaleString("de-DE")} € → ` : "";
    teile.push(`neuer Preis ${alt}${Number(ae.preis).toLocaleString("de-DE")} €`);
  }
  (ae.felder || []).forEach((f) => teile.push(FELD_NAMEN[f] || f));
  if (ae.neue_schaeden) teile.push(`${ae.neue_schaeden} neue(r) Schaden/Schäden`);
  if (ae.sondervereinbarung) teile.push("Sondervereinbarung");
  return teile;
}

function NachAbholungHinweis({ item, onSenden }) {
  // Wunsch Ahmad 19.09.2026: Nach dem unterschriebenen Abholprotokoll wird der
  // Vertrag mit den vor Ort festgestellten Daten neu erstellt. Die alte Fassung
  // bleibt als Beweis (unten "Frühere Fassungen"), hier steht die GÜLTIGE — und
  // die Frage, ob der Verkäufer sie bekommen soll.
  // Pruefbericht 20.09.2026 (U-88): Der Versand entfernt nur den MERKER
  // (nach_abholung_versand_offen), die Aenderungen bleiben am Vertrag. Der
  // Hinweis, dass dieser Vertrag nach der Abholung neu entstand, verschwand
  // damit nach dem Senden — jetzt bleibt er stehen, nur ohne Senden-Knopf.
  const ae = item.nach_abholung_aenderungen;
  if (!ae) return null;
  const offen = Boolean(item.nach_abholung_versand_offen);
  const teile = nachAbholungTeile(ae);
  return (
    <div className="mt-3 rounded-xl border px-3 py-2.5 flex flex-wrap items-center gap-2 text-[13px]"
         data-testid={`nach-abholung-${item.id}`}
         style={offen
           ? { borderColor: "rgba(255,159,10,0.35)", background: "rgba(255,159,10,0.10)" }
           : { borderColor: "var(--border-default)", background: "var(--wa-04)" }}>
      <span style={{ color: "var(--text-primary)" }}>
        <b>Nach der Abholung neu erstellt</b>{teile.length ? ` — ${teile.join(", ")}.` : "."}
        {" "}Die vorherige Fassung bleibt als Nachweis erhalten.
        {!offen && item.nach_abholung_versand_ausgesetzt && (
          <> {" "}Versand ausgesetzt — der Ausgang der Abholung wurde geändert.</>
        )}
      </span>
      {offen && (
        <button onClick={onSenden} data-testid={`nach-abholung-senden-${item.id}`}
                className="ml-auto inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white"
                style={{ background: "var(--accent-red)" }}>
          <Send size={13} /> Neuen Vertrag senden
        </button>
      )}
    </div>
  );
}

function SpecsZeile({ cd }) {
  // km / PS / EZ aus den beim Vertrag gespeicherten Fahrzeugdaten.
  const km = Number(cd?.vehicle_mileage);
  const ps = (cd?.vehicle_power_ps || "").toString().trim();
  const ez = (cd?.vehicle_first_registration || "").toString().trim();
  const teile = [
    km > 0 && `${km.toLocaleString("de-DE")} km`,
    ps && `${ps} PS`,
    ez && `EZ ${ez}`,
  ].filter(Boolean);
  if (teile.length === 0) return null;
  return (
    <div className="mt-1.5 text-[13.5px] font-medium tracking-tight">
      {teile.join("  ·  ")}
    </div>
  );
}

function AbholZeile({ item }) {
  // Abholtermin + Abhol-Ort (= Verkäufer-Anschrift aus dem Vertrag).
  // Datenschutz (Pruefbericht 09/2026): In der Liste standardmaessig nur
  // Datum/Uhrzeit und Ort (keine Strasse, keine PLZ) — die vollstaendige
  // Anschrift erst nach Klick auf "Adresse anzeigen", Zustand je Karte.
  const [adresseOffen, setAdresseOffen] = useState(false);
  const cd = item.contract_data || {};
  const ort = (cd.seller_city || "").trim();
  const volleAdresse = [
    (cd.seller_address || "").trim(),
    [(cd.seller_zip || "").trim(), ort].filter(Boolean).join(" "),
  ].filter(Boolean).join(", ");
  if (!item.pickup_date && !volleAdresse) return null;
  const termin = item.pickup_date
    ? `Abholung ${item.pickup_date}${cd.pickup_time ? `, ${cd.pickup_time} Uhr` : ""}`
    : "";
  const ortText = adresseOffen ? volleAdresse : ort;
  const hatMehr = Boolean(volleAdresse) && volleAdresse !== ort;
  return (
    <div className="mt-1 flex items-center gap-1.5 text-[13.5px] leading-relaxed"
         style={{ color: "var(--text-secondary)" }}>
      <MapPin size={13} className="shrink-0" style={{ color: "var(--text-muted)" }} />
      <span className="truncate">
        {termin}
        {termin && ortText && " · "}
        {ortText}
      </span>
      {hatMehr && (
        <button type="button"
                onClick={() => setAdresseOffen((o) => !o)}
                data-testid={`adresse-toggle-${item.id}`}
                className="shrink-0 text-[11px] font-semibold underline-offset-2 hover:underline"
                style={{ color: "var(--text-muted)" }}
                title={adresseOffen ? "Vollständige Anschrift ausblenden" : "Vollständige Anschrift des Verkäufers einblenden"}>
          {adresseOffen ? "Adresse ausblenden" : "Adresse anzeigen"}
        </button>
      )}
    </div>
  );
}

function VehicleThumb({ item, onOpen }) {
  // Foto-URLs kommen direkt mit der Vertragsliste (vehicle_image_urls).
  // Rollenprüfung 22.09.2026 (RP-106): Fehlten sie, fragte jede Karte noch
  // einmal GET /vehicles/{id} — der Server ergänzt die Fotos alter Verträge
  // aber schon selbst aus dem Fahrzeug (list_contracts, bilder_nachgetragen).
  // Bleibt die Liste leer, hat auch das Fahrzeug keine: keine Extra-Anfrage.
  const urls = item.vehicle_image_urls || [];
  // 10.09.2026: fuer das kleine Vorschaubild den Bild-Proxy nutzen (klein,
  // zuverlaessig); die grosse Ansicht zeigt weiter das Originalfoto.
  const thumbs = item.vehicle_image_urls_thumbs || [];

  if (urls.length === 0) {
    return (
      <div
        className="h-24 w-full sm:w-36 shrink-0 rounded-xl flex items-center justify-center"
        style={{ background: "var(--wa-03)",
                 border: "1px solid var(--border-default)",
                 color: "var(--text-muted)" }}
        title="Keine Fotos zum Inserat gespeichert"
      >
        <Car size={26} />
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(urls, 0)}
      className="relative shrink-0 group w-full sm:w-36"
      data-testid={`photos-open-${item.id}`}
      title={`${urls.length} Fotos ansehen`}
    >
      <img
        src={thumbSrc(thumbs[0], urls[0])}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        onError={(e) => thumbFehler(e, urls[0])}
        className="h-40 sm:h-24 w-full object-cover rounded-xl transition-transform duration-200 group-hover:scale-[1.03]"
        style={{ border: "1px solid var(--border-default)" }}
      />
      {urls.length > 1 && (
        <span
          className="absolute bottom-1.5 right-1.5 inline-flex items-center justify-center rounded-full bg-black/75 backdrop-blur px-2 py-0.5 text-[11px] font-semibold text-white"
        >
          {urls.length} Fotos
        </span>
      )}
    </button>
  );
}

function GalleryViewer({ item, urls, startIndex = 0, onClose }) {
  const [index, setIndex] = useState(Math.min(startIndex, urls.length - 1));
  const title = `${item.make || ""} ${item.model || ""}`.trim() || "Fahrzeugfotos";

  const prev = useCallback(
    () => setIndex((i) => (i - 1 + urls.length) % urls.length), [urls.length]);
  const next = useCallback(
    () => setIndex((i) => (i + 1) % urls.length), [urls.length]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, prev, next]);

  // Wisch-Geste (Touch / Trackpad-Drag)
  const [touchX, setTouchX] = useState(null);
  // Pruefbericht 20.09.2026 (M-16): Nach einem Wisch schickt das Handy noch
  // einen Klick auf denselben Hintergrund — der schloss die Galerie. Der
  // Merker schluckt genau diesen einen Klick; ein neuer Tipp (touchstart)
  // setzt ihn zurueck, damit nie ein echter Klick verloren geht.
  const gewischt = useRef(false);
  const onTouchStart = (e) => { gewischt.current = false; setTouchX(e.touches[0].clientX); };
  const onTouchEnd = (e) => {
    if (touchX == null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 50) {
      gewischt.current = true;
      if (dx < 0) next(); else prev();
    }
    setTouchX(null);
  };
  const hintergrundKlick = () => {
    if (gewischt.current) { gewischt.current = false; return; }
    onClose();
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex flex-col"
      style={{ background: "rgba(0,0,0,0.94)", backdropFilter: "blur(14px)" }}
      onClick={hintergrundKlick}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      data-testid="photos-gallery"
    >
      {/* Kopfzeile */}
      <div
        className="flex items-center justify-between gap-3 px-4 sm:px-6 py-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="min-w-0">
          <div className="overline">Fotos vom Inserat</div>
          <div className="font-display font-bold text-lg tracking-tight truncate">
            {title} <span className="text-zinc-500 font-normal">· {index + 1} / {urls.length}</span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="w-10 h-10 shrink-0 rounded-full flex items-center justify-center text-white hover:bg-white/10"
          data-testid="photos-gallery-close"
          aria-label="Schließen"
        >
          <X size={20} />
        </button>
      </div>

      {/* Großes Bild + Pfeile */}
      <div className="relative flex-1 min-h-0 flex items-center justify-center px-14 sm:px-20">
        {urls.length > 1 && (
          <>
            <button
              onClick={(e) => { e.stopPropagation(); prev(); }}
              data-testid="photos-gallery-prev"
              className="absolute left-3 sm:left-6 w-11 h-11 rounded-full flex items-center justify-center text-white bg-white/5 hover:bg-white/15 backdrop-blur"
              aria-label="Vorheriges Foto"
            >
              <ChevronLeft size={24} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); next(); }}
              data-testid="photos-gallery-next"
              className="absolute right-3 sm:right-6 w-11 h-11 rounded-full flex items-center justify-center text-white bg-white/5 hover:bg-white/15 backdrop-blur"
              aria-label="Nächstes Foto"
            >
              <ChevronRight size={24} />
            </button>
          </>
        )}
        <img
          src={urls[index]}
          alt=""
          onClick={(e) => e.stopPropagation()}
          className="max-w-full max-h-full object-contain select-none rounded-md"
          draggable={false}
          data-testid="photos-gallery-image"
        />
      </div>

      {/* Filmstreifen zum direkten Anspringen */}
      {urls.length > 1 && (
        <div
          className="px-4 sm:px-6 py-3 flex gap-2 overflow-x-auto justify-start sm:justify-center"
          onClick={(e) => e.stopPropagation()}
        >
          {urls.map((u, i) => (
            <button
              key={`${u}-${i}`}
              onClick={() => setIndex(i)}
              data-testid={`photos-gallery-thumb-${i}`}
              className={`h-14 w-20 shrink-0 rounded-md overflow-hidden border-2 transition-opacity ${
                i === index ? "border-white opacity-100" : "border-transparent opacity-50 hover:opacity-90"
              }`}
            >
              <img src={u} alt="" loading="lazy" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


// Pruefbericht 20.09.2026 (U-88): Warum eine Fassung archiviert wurde —
// dieselben Gruende wie im Backend (routes/contracts.py: grund=...). Der
// Tooltip kannte nur den verschobenen Abholtermin.
const FASSUNG_GRUND = {
  abholtermin_geaendert: "Abholtermin geändert",
  abholung_abgeschlossen: "Preis/Daten nach Abholung",
  verkaeufer_korrigiert: "Verkäuferdaten korrigiert",
};
export function fassungGrundText(grund) {
  return FASSUNG_GRUND[grund] || "";
}

/** Ältere Vertragsfassungen (entstehen beim Verschieben des Abholtermins,
 *  nach der Abholung mit neuem Preis oder nach einer Verkäufer-Korrektur).
 *  Lazy: die Liste wird erst beim Aufklappen geladen; jede Fassung öffnet
 *  per Authorization-Abruf (Review 09/2026: Versionen existierten im
 *  Backend, waren aber nirgends sichtbar). */
function VersionenZeile({ contractId }) {
  const [offen, setOffen] = useState(false);
  const [versionen, setVersionen] = useState(null);
  // Rollenprüfung 22.09.2026 (RP-045/RP-144): Der Server kürzt die Liste
  // (X-Truncated, die neuesten 1.000) — das blieb hier unsichtbar.
  const [gekuerzt, setGekuerzt] = useState(false);

  const toggle = async () => {
    const jetzt = !offen;
    setOffen(jetzt);
    if (jetzt && versionen === null) {
      try {
        const r = await api.get(`/contracts/${contractId}/versions`);
        const data = r.data;
        setGekuerzt(String(r.headers?.["x-truncated"] || "") === "1");
        setVersionen(Array.isArray(data) ? data : []);
      } catch {
        // Fehler nicht als "keine Fassungen" cachen — zuklappen, damit der
        // naechste Klick neu laedt (Review-Workflow 09/2026).
        setVersionen(null);
        setOffen(false);
        toast.error("Fassungen konnten nicht geladen werden");
      }
    }
  };

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] uppercase font-bold tracking-wider"
              style={{ color: "var(--text-muted)" }}>
          Fassungen
        </span>
        <button onClick={toggle} data-testid={`versions-open-${contractId}`}
                className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full">
          {offen ? "ausblenden" : "ältere Fassungen anzeigen"}
        </button>
      </div>
      {offen && (
        <div className="mt-1.5 flex items-center gap-2 flex-wrap">
          {versionen === null ? (
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>lädt…</span>
          ) : versionen.length === 0 ? (
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>Keine älteren Fassungen.</span>
          ) : (
            <>
            {gekuerzt && (
              <span className="text-xs basis-full" style={{ color: "var(--text-muted)" }}
                    data-testid={`versions-gekuerzt-${contractId}`}>
                Es gibt mehr Fassungen — angezeigt sind die neuesten {versionen.length.toLocaleString("de-DE")}.
              </span>
            )}
            {versionen.map((v) => (
              <button key={v.id}
                      data-testid={`version-pdf-${contractId}-${v.version}`}
                      onClick={() => openAuthedFile(`/contracts/${contractId}/versions/${v.version}/pdf`)
                        .catch(() => toast.error("Fassung konnte nicht geladen werden"))}
                      title={`Archiviert ${new Date(v.archived_at).toLocaleString("de-DE")}${fassungGrundText(v.grund) ? ` · ${fassungGrundText(v.grund)}` : ""}`}
                      className="apple-btn apple-btn-secondary !py-1 !px-2 !text-[11px] !rounded-full">
                v{v.version}{v.pickup_date ? ` · Abholung ${v.pickup_date}` : ""}
              </button>
            ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
