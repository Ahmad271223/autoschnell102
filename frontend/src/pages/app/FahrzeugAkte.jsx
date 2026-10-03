import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errMsg, openAuthedFile } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useFeatures } from "@/lib/features";
import { useUngespeichert } from "@/lib/ungespeichert";
import {
  bestandFormAus, bestandGeaendert, bestandMitOffenenAenderungen, fristErneuertText, kostenLesen,
} from "@/lib/bestandForm";
import { protokollBefundHinweis, termineOffenDetail, termineStornoFrage } from "@/lib/akteHinweise";
import { toast } from "sonner";
import AbholFoto from "@/components/AbholFoto";
import BeweisCard from "@/components/BeweisCard";
import { fotosBis } from "@/components/AbholberichtDialog";
import StatusSchild from "@/components/StatusSchild";
import {
  aktionText, beschreibungLesbar, datumDE, inseratText, kaufvorgangText, lesbar, lifecycleText,
} from "@/lib/fahrzeugStatus";
import {
  ArrowLeft, AlertTriangle, Clock, Tag, Archive, Trash2, FileText, PenLine,
} from "lucide-react";
import { openContractPdf } from "@/lib/pdf";

/**
 * Durchgehende Fahrzeugakte: Beschaffung · Kauf · Abholung (mit Abweichungs-
 * Diff) · Bestand · Verkauf · Historie. Kein Wissen über das Fahrzeug geht
 * verloren — alles an einem Ort.
 */

const fmtDate = (s) => {
  if (!s) return "—";
  try { return new Date(s).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return s; }
};
// Geschuetztes Leerzeichen: "20.000 €" bricht auf dem Handy nie vor dem €-Zeichen um.
const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE")}\u00a0€`);

// Kilometer nur anzeigen, wenn es wirklich eine Zahl ist — Altdaten mit
// Text ("85.000 km") ergaben sonst "NaN km" (Pruefbericht 20.09.2026, N17).
const fmtKm = (n) => {
  const zahl = typeof n === "number" ? n : Number(n);
  return Number.isFinite(zahl) && zahl > 0 ? `${zahl.toLocaleString("de-DE")} km` : null;
};

// Pruefbericht 20.09.2026 (B7): Section und KV standen INNERHALB der
// Seitenfunktion. Bei jedem Render entstand ein neuer Komponententyp, React
// baute den ganzen Block samt <input> neu auf — im Bestandsformular sprang
// der Fokus nach jedem Zeichen heraus. Auf Modulebene bleibt der Typ stabil.
function Section({ title, children, warn }) {
  return (
    <div className="tactical-card p-4 mt-4 min-w-0">
      <div className="flex items-center gap-2 mb-3">
        <div className="w-1 h-4 rounded" style={{ background: "var(--accent-red)" }} />
        <div className="text-sm font-bold uppercase tracking-wide">{title}</div>
        {warn}
      </div>
      {children}
    </div>
  );
}

// Inserate, die noch laufen (wie backend/routes/resale.py _AKTIV).
const INSERAT_AKTIV = ["entwurf", "verkaufsbereit", "reserviert", "veroeffentlicht", "zurueckgezogen"];

function KV({ k, val }) {
  return (
    <div className="flex justify-between gap-4 py-1 border-b text-sm" style={{ borderColor: "var(--wa-04)" }}>
      {/* Die Beschriftung bricht um, nicht der Wert ("20.000 €" bleibt ganz). */}
      <span className="min-w-0 text-zinc-500">{k}</span>
      <span className="text-right break-words">{val == null || val === "" ? "—" : val}</span>
    </div>
  );
}

export default function FahrzeugAkte() {
  const { id } = useParams();
  const nav = useNavigate();
  const features = useFeatures();          // Go-Live-Schalter (15.09.2026)
  const [akte, setAkte] = useState(null);
  const [selectedDevs, setSelectedDevs] = useState([]);
  const [bestandForm, setBestandForm] = useState(null);
  // Rollenprüfung 22.09.2026 (RP-462): letzter Serverstand des
  // Bestandsformulars — Grundlage für "ungespeichert" und dafür, dass ein
  // Neuladen (nach "Nur speichern", "Änderungen übernehmen" …) offene
  // Eingaben nicht mehr verwirft.
  const bestandStandRef = useRef(null);
  const [busy, setBusy] = useState(false);
  // Pruefbericht 20.09.2026 (B5): Ein Ladefehler (404 nach Entfernen oder
  // Loeschen, 500, Funkloch) liess die Seite fuer immer auf "lade…" stehen —
  // der Rueckweg lag hinter diesem Zustand. Jetzt: Text, Zurueck, Erneut.
  const [ladeFehler, setLadeFehler] = useState(null);
  const { user } = useAuth();
  // Wunsch Ahmad 14.09.2026: "Wenn ein Sucher ein Auto loescht, soll das nur
  // bei ihm loeschen, nicht beim Chef." Sucher sehen deshalb keine Chef-
  // Entscheidungen (Bestand/Weiterverkauf/Loeschen), sondern nur "Aus meiner
  // Liste entfernen" (POST /vehicles/{id}/entfernen).
  const sucher = user?.role === "sucher";
  // Sucher haben keinen Bestand (Chef-Seite) — ihr Rueckweg ist die eigene
  // Fahrzeugliste (Pruefbericht 20.09.2026, B6/F7).
  const zurueck = sucher
    ? { pfad: "/app/fahrzeuge", text: "Zurück zu meinen Fahrzeugen" }
    : { pfad: "/app/bestand", text: "Zurück zum Bestand" };

  // Prüfbericht 20.09. U-47: nur die LETZTE Anfrage darf die Akte setzen —
  // beim Wechsel Akte A→B (Zurück/Vor) überschrieb sonst die langsamere
  // Antwort von A die schon angezeigte Akte B (wie anfrageNr in Bestand.jsx).
  const anfrageNr = useRef(0);
  const load = useCallback(async ({ offeneVerwerfen = false } = {}) => {
    const nr = ++anfrageNr.current;
    try {
      const r = await api.get(`/vehicles/${id}/akte`);
      if (nr !== anfrageNr.current) return;
      setLadeFehler(null);
      setAkte(r.data);
      const neu = bestandFormAus(r.data.vehicle?.bestand);
      const alterStand = offeneVerwerfen ? null : bestandStandRef.current;
      bestandStandRef.current = neu;
      setBestandForm((alt) => bestandMitOffenenAenderungen(alt, alterStand, neu));
    } catch (e) {
      if (nr !== anfrageNr.current) return;
      // Wunsch Ahmad 21.09.2026 (R1-01): der Chef haengt keine Fahrzeuge mehr
      // um — "einem anderen Konto zugeordnet" ist deshalb kein Grund mehr.
      const text = e?.response?.status === 404
        ? "Diese Fahrzeugakte ist nicht (mehr) für dich sichtbar — das Fahrzeug wurde "
          + "gelöscht oder aus deiner Liste entfernt."
        : errMsg(e, "Akte konnte nicht geladen werden");
      setLadeFehler(text);
      toast.error(text);
    }
  }, [id]);

  useEffect(() => {
    setAkte(null);
    setLadeFehler(null);
    // anderes Fahrzeug: kein Formularrest des vorigen
    bestandStandRef.current = null;
    setBestandForm(null);
    load();
  }, [load]);

  // RP-462: Verlassen/Neuladen mit offenen Standort-/Kosten-Eingaben fragt nach
  // (auch der Wechsel über die Seitenleiste, siehe AppLayout).
  useUngespeichert(bestandGeaendert(bestandForm, bestandStandRef.current));

  if (!akte) {
    return (
      <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="akte-laedt">
        <Link to={zurueck.pfad} className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
          <ArrowLeft size={14} /> {zurueck.text}
        </Link>
        {ladeFehler ? (
          <div className="tactical-card p-4 mt-4 text-sm" role="alert" data-testid="akte-ladefehler">
            <div style={{ color: "var(--text-primary)" }}>{ladeFehler}</div>
            <button type="button" onClick={() => { setLadeFehler(null); load(); }}
                    className="mt-3 rounded-lg px-3 py-2 text-xs border font-semibold"
                    style={{ borderColor: "var(--border-default)" }} data-testid="akte-erneut">
              Erneut versuchen
            </button>
          </div>
        ) : (
          <div className="mt-6 text-zinc-500 text-sm">lade…</div>
        )}
      </div>
    );
  }

  const v = akte.vehicle;
  const d = v.data || {};
  const report = akte.pickup_report;
  const deviations = report?.deviations || [];
  const listing = (akte.listings || [])[0];

  const decide = async (decision) => {
    if (busy) return;
    if (decision === "loeschen" &&
        !window.confirm("Fahrzeug wirklich löschen?\nFotos werden entfernt — Vertrag und Historie bleiben erhalten.")) return;
    setBusy(true);
    try {
      if (decision === "verkaufsentwurf") {
        // Rollenprüfung 22.09.2026 (RP-092/RP-191/RP-342): EIN Aufruf. Vorher
        // setzte /decision das Fahrzeug erst auf "verkaufsentwurf" und danach
        // legte /resale/draft das Inserat an — scheiterte der zweite Schritt,
        // stand das Auto ohne Inserat und ohne Knopf da. create_draft setzt
        // den Fahrzeugstatus selbst und nimmt ihn bei einem Fehler zurück.
        const draft = await api.post(`/resale/draft/${v.id}`);
        nav(`/app/inserat/${draft.data.id}`);
        return;
      }
      // RP-496: der angezeigte Zustand geht mit — hat er sich inzwischen
      // geändert (zweiter Tab), antwortet der Server mit 409 statt still
      // umzuschalten.
      const body = { decision, von_lifecycle: v.lifecycle };
      let r;
      try {
        r = await api.post(`/vehicles/${v.id}/decision`, body);
      } catch (e) {
        // RP-454 (Welle B2): offene Abholtermine — der Chef sieht sie (Datum,
        // Fahrer) und entscheidet, ob sie mit dem Fahrzeug storniert werden.
        // Nie still: erst nach dem Ja geht termine_stornieren=true raus.
        const offen = termineOffenDetail(e);
        if (!offen || !window.confirm(termineStornoFrage(offen))) throw e;
        r = await api.post(`/vehicles/${v.id}/decision`, { ...body, termine_stornieren: true });
      }
      toast.success(r.data?.verlaengert
        ? fristErneuertText(r.data?.expires_at)
        : r.data?.termine_storniert?.length
          ? `Fahrzeug gelöscht, ${r.data.termine_storniert.length} Termin(e) storniert`
          : "Gespeichert");
      load();
    } catch (e) {
      // 409: Status inzwischen geändert (RP-496), Termine offen (RP-454) oder
      // Inserat wird gerade angelegt — den aktuellen Stand zeigen (offene
      // Eingaben im Bestandsformular bleiben, RP-462).
      toast.error(errMsg(e));
      if (e?.response?.status === 409) load();
    } finally { setBusy(false); }
  };

  const entfernen = async () => {
    if (busy) return;
    if (!window.confirm("Fahrzeug aus deiner Liste entfernen?\n"
        + "Es verschwindet nur bei dir — der Chef behält Fahrzeug, Vertrag, Termine und Historie.")) return;
    setBusy(true);
    try {
      await api.post(`/vehicles/${v.id}/entfernen`);
      toast.success("Aus deiner Liste entfernt — der Chef behält das Fahrzeug");
      nav("/app/fahrzeuge");
    } catch (e) { toast.error(errMsg(e)); setBusy(false); }
  };

  const applyDeviations = async () => {
    if (busy) return;
    if (!selectedDevs.length) { toast.error("Bitte Abweichungen auswählen"); return; }
    setBusy(true);
    try {
      const r = await api.post(`/vehicles/${v.id}/apply-deviations`, { deviation_ids: selectedDevs });
      toast.success(`${(r.data.applied || []).length} Änderung(en) übernommen`);
      setSelectedDevs([]);
      load();
    } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };

  // RP-474: was aus dem unterschriebenen Abholprotokoll noch fehlt (nur Chef,
  // nur solange das Fahrzeug nicht abgeschlossen ist — wie der Server).
  const befund = akte.protokoll_befund;
  const bekannteMaengel = v.known_defects || [];
  const befundNeueSchaeden = (befund?.schaeden || []).filter((s) => !bekannteMaengel.includes(s));
  const befundKmNeu = befund?.km != null && Number(befund.km) !== Number(d.mileage);
  const befundOffen = !sucher && Boolean(befund)
    && !["verkauft", "archiviert", "geloescht"].includes(v.lifecycle)
    && (befundKmNeu || befundNeueSchaeden.length > 0);

  const befundUebernehmen = async () => {
    if (busy) return;
    setBusy(true);
    try {
      // "abholprotokoll" = backend/routes/bestand.py PROTOKOLL_BEFUND_ID
      const r = await api.post(`/vehicles/${v.id}/apply-deviations`, { deviation_ids: ["abholprotokoll"] });
      toast.success(`${(r.data.applied || []).length} Änderung(en) aus dem Abholprotokoll übernommen`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
      if (e?.response?.status === 409) load();
    } finally { setBusy(false); }
  };

  const saveBestand = async () => {
    if (busy) return;
    // Rollenprüfung 22.09.2026 (RP-449/RP-565): Beträge deutsch lesen
    // ("1.200" = 1.200 €, "249,90" = 249,90 €); Unlesbares nicht speichern.
    const { costs, fehler } = kostenLesen(bestandForm.costs);
    if (fehler) { toast.error(fehler); return; }
    setBusy(true);
    try {
      // RP-461: der geladene Stand geht mit — hat ein anderes Gerät
      // inzwischen gespeichert, kommt 409 statt eines stillen Überschreibens.
      await api.put(`/vehicles/${v.id}/bestand`, {
        location: bestandForm.location, notes: bestandForm.notes, costs,
        stand: bestandForm.stand || "",
      });
      toast.success("Bestandsdaten gespeichert");
      load({ offeneVerwerfen: true });
    } catch (e) {
      toast.error(errMsg(e));
      // Bei 409 den neuen Serverstand holen — die eigenen Eingaben bleiben
      // stehen (RP-462), ein zweiter Klick speichert sie dann.
      if (e?.response?.status === 409) load();
    } finally { setBusy(false); }
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="akte-page">
      <Link to={zurueck.pfad} className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
        <ArrowLeft size={14} /> {zurueck.text}
      </Link>
      {/* 11.09.2026 (Befund Ahmad "zu eng da oben"): Modellbeschreibung in
          eigener Zeile, Status/Bearbeiter/Frist als eine Zeile, Knoepfe rechts
          (auf dem Handy darunter) und Abstand zum Beweisdokument. */}
      <header className="mt-3 mb-6 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between"
              data-testid="akte-kopf">
        <div className="min-w-0 flex-1">
          <div className="overline">Fahrzeugakte · {v.source === "manuell" ? "manuell angelegt" : "über System beschafft"}</div>
          <h1 className="font-display font-black text-2xl lg:text-3xl tracking-tighter mt-2 break-words">
            {d.make_label} {d.model_label}
          </h1>
          {d.model_description && (
            <p className="mt-1 text-sm break-words" style={{ color: "var(--text-secondary)" }}
               data-testid="akte-beschreibung">
              {beschreibungLesbar(d.model_description)}
            </p>
          )}
          <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2.5 text-xs text-zinc-500">
            <span className="inline-flex items-center gap-2">
              Status
              <StatusSchild status={v.lifecycle} text={lifecycleText(v.lifecycle)} data-testid="akte-status" />
            </span>
            {/* Wunsch Ahmad 21.09.2026 (R1-01): "man soll nie an dem sein
                abgeschlossenen Vertrag oder sonstwas wegnehmen" — das
                Auswahlfeld zum Umhaengen ist entfallen. Der Chef sieht den
                Bearbeiter nur noch als Text. */}
            {!sucher && (
              <span className="inline-flex flex-wrap items-center gap-2" data-testid="akte-besitzer">
                Bearbeiter
                <span style={{ color: "var(--text-primary)" }}>
                  {akte.owner
                    ? `${akte.owner.name}${akte.owner.hauptaccount ? " (Hauptaccount)" : ""}`
                    : "— nicht zugeordnet —"}
                </span>
                {akte.mitbearbeiter?.length > 0 && (
                  <span data-testid="akte-mitbearbeiter">mit {akte.mitbearbeiter.map((m) => m.name).join(", ")}</span>
                )}
              </span>
            )}
            {akte.retention_days_left != null && (
              <span className={`inline-flex items-center gap-1 ${akte.retention_days_left <= 10 ? "text-amber-400" : ""}`}>
                <Clock size={12} /> noch {akte.retention_days_left} Tage im Bestand
              </span>
            )}
          </div>
        </div>
        <div className="flex flex-wrap gap-2 lg:justify-end lg:shrink-0 lg:max-w-[50%]">
          {sucher && (
            <button onClick={entfernen} disabled={busy} data-testid="akte-sucher-entfernen"
                    className="rounded-lg px-3 py-2 text-xs text-zinc-500 hover:text-red-400 inline-flex items-center gap-1.5 disabled:opacity-50">
              <Trash2 size={13} /> Aus meiner Liste entfernen
            </button>
          )}
          {!sucher && v.lifecycle === "abgeholt" && (
            <>
              {/* Rollenprüfung 22.09.2026 (RP-045/RP-144): nur mit
                  freigeschaltetem Marktplatz — sonst 503 "Demnächst verfügbar". */}
              {features.marktplatz && (
                <button onClick={() => decide("verkaufsentwurf")} disabled={busy} data-testid="akte-weiterverkaufen"
                        className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5 disabled:opacity-50" style={{ background: "var(--accent-red)" }}>
                  <Tag size={13} /> Speichern & weiterverkaufen
                </button>
              )}
              <button onClick={() => decide("bestand")} disabled={busy} className="rounded-lg px-3 py-2 text-xs border inline-flex items-center gap-1.5 disabled:opacity-50" style={{ borderColor: "var(--border-default)" }}>
                <Archive size={13} /> Nur speichern
              </button>
              <button onClick={() => decide("loeschen")} disabled={busy} className="rounded-lg px-3 py-2 text-xs text-zinc-500 hover:text-red-400 inline-flex items-center gap-1.5 disabled:opacity-50">
                <Trash2 size={13} /> Löschen
              </button>
            </>
          )}
          {features.marktplatz && !sucher && v.lifecycle === "bestand" && (
            <button onClick={() => decide("verkaufsentwurf")} disabled={busy}
                    className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5 disabled:opacity-50" style={{ background: "var(--accent-red)" }}>
              <Tag size={13} /> Weiterverkaufen
            </button>
          )}
          {/* Rollenprüfung 22.09.2026 (RP-450): Nach 50 Tagen archiviert der
              Aufräumer das Fahrzeug endgültig (Fotos weg) — auch wenn es noch
              auf dem Hof steht. Der Chef kann die Frist hier verlängern.
              Review 22.09.: die neue Frist ist 50 Tage ab heute, nicht die
              alte Frist + 50 — so steht es jetzt auf dem Knopf. */}
          {!sucher && v.lifecycle === "bestand" && (
            <button onClick={() => decide("bestand")} disabled={busy} data-testid="akte-frist-verlaengern"
                    title="Setzt die Frist neu auf 50 Tage ab heute"
                    className="rounded-lg px-3 py-2 text-xs border inline-flex items-center gap-1.5 disabled:opacity-50"
                    style={{ borderColor: "var(--border-default)" }}>
              <Clock size={13} /> Frist erneuern (50 Tage ab heute)
            </button>
          )}
          {/* RP-191/RP-342: Fahrzeug steht auf "Verkaufsentwurf", aber es gibt
              kein Inserat (z. B. alter Abbruch zwischen zwei Aufrufen) — hier
              lässt es sich nachholen. */}
          {features.marktplatz && !sucher && v.lifecycle === "verkaufsentwurf"
            && !(akte.listings || []).some((l) => INSERAT_AKTIV.includes(l.status)) && (
            <button onClick={() => decide("verkaufsentwurf")} disabled={busy} data-testid="akte-inserat-anlegen"
                    className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5 disabled:opacity-50"
                    style={{ background: "var(--accent-red)" }}>
              <Tag size={13} /> Inserat anlegen
            </button>
          )}
          {/* Ab Vertragserstellung sofort inserierbar (Abholung läuft parallel) */}
          {features.marktplatz && !sucher && ["vertrag_erstellt", "gekauft", "abholung_geplant"].includes(v.lifecycle) && (
            <button onClick={() => decide("verkaufsentwurf")} disabled={busy}
                    className="rounded-lg px-3 py-2 text-xs font-semibold text-white inline-flex items-center gap-1.5 disabled:opacity-50" style={{ background: "var(--accent-red)" }}>
              <Tag size={13} /> Jetzt inserieren
            </button>
          )}
          {/* Pruefbericht 20.09.2026 (B17): Die Inseratseite ist Chefsache
              (resale.py -> current_chef). Sucher bekamen den Knopf trotzdem
              und landeten in einem 403 mit ewigem "lade…". */}
          {features.marktplatz && !sucher && listing && INSERAT_AKTIV.includes(listing.status) && (
            <Link to={`/app/inserat/${listing.id}`} data-testid="akte-inserat-link"
                  className="rounded-lg px-3 py-2 text-xs border inline-flex items-center gap-1.5" style={{ borderColor: "var(--border-default)" }}>
              {listing.status === "veroeffentlicht" ? "Inserat öffnen (live · vom Marktplatz nehmen / löschen)" : `Inserat öffnen (${inseratText(listing.status)})`}
            </Link>
          )}
        </div>
      </header>

      {/* Beweisdokument zum Inserat (ersetzt die Snapshots, 10.09.2026) */}
      {v.id && v.source !== "manuell" && (
        <div className="mb-4" data-testid="akte-beweis">
          <BeweisCard vehicleId={v.id} />
        </div>
      )}

      {/* Abholung + Diff */}
      {report && (
        <Section
          title="Abholung"
          warn={deviations.length > 0 && (
            <span className="ml-auto inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-md" style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
              <AlertTriangle size={12} /> {deviations.length} Abweichung(en)
            </span>
          )}
        >
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-3">
            <div><div className="text-[11px] text-zinc-500">Fahrer</div>{report.driver_name || "—"}</div>
            <div><div className="text-[11px] text-zinc-500">km bei Abholung</div>{report.mileage_at_pickup?.toLocaleString("de-DE") || "—"}</div>
            <div><div className="text-[11px] text-zinc-500">Schlüssel</div>{report.keys_count ?? "—"}</div>
            <div><div className="text-[11px] text-zinc-500">Tank</div>{report.fuel_level || "—"}</div>
          </div>
          {deviations.length > 0 && (
            <>
              <div className="text-xs text-zinc-400 mb-2">
                {sucher
                  ? "Ursprüngliche Daten vs. bei Abholung festgestellt (übernehmen kann der Chef):"
                  : "Ursprüngliche Daten vs. bei Abholung festgestellt — auswählen und übernehmen:"}
              </div>
              {deviations.some((d) => d.photo_key) && fotosBis(report.created_at, akte.fahrerfoto_tage) && (
                <div className="text-[11px] text-zinc-500 mb-2" data-testid="akte-fotos-bis">
                  Fahrerfotos werden ab dem {fotosBis(report.created_at, akte.fahrerfoto_tage).toLocaleDateString("de-DE")} automatisch
                  gelöscht. Wichtige Fotos vorher im Verkaufsinserat übernehmen.
                </div>
              )}
              {/* Pruefbericht 20.09.2026 (M-08): am Handy waagerecht scrollbar statt abgeschnitten */}
              <div className="overflow-x-auto">
              <table className="w-full min-w-[480px] text-sm">
                <thead>
                  <tr className="text-left overline">
                    {!sucher && <th className="py-2 pr-2 w-8"></th>}
                    <th className="py-2 pr-2">Abweichung</th>
                    <th className="py-2 pr-2 text-right">Beim Einkauf</th>
                    <th className="py-2 text-right">Bei Abholung</th>
                  </tr>
                </thead>
                <tbody>
                  {deviations.map((dev) => (
                    <tr key={dev.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                      {!sucher && (
                        <td className="py-2 pr-2">
                          <input type="checkbox" aria-label={`Abweichung übernehmen: ${dev.label}`}
                                 checked={selectedDevs.includes(dev.id)}
                                 onChange={(e) => setSelectedDevs((s) =>
                                   e.target.checked ? [...s, dev.id] : s.filter((x) => x !== dev.id))} />
                        </td>
                      )}
                      <td className="py-2 pr-2">
                        {dev.label}
                        {dev.photo_key && (
                          <span className="ml-2 inline-block align-middle">
                            <AbholFoto photoKey={dev.photo_key} label={dev.label} size={44} />
                          </span>
                        )}
                        {!dev.photo_key && dev.photo_deleted_at && (
                          <span className="ml-2 text-[11px] text-zinc-500">Foto nach Frist gelöscht</span>
                        )}
                      </td>
                      <td className="py-2 pr-2 text-right text-zinc-400">{dev.expected || "—"}</td>
                      <td className="py-2 text-right font-medium">{dev.actual || "Ja"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
              {/* Runde 19: Uebernahme ist Chefsache (Backend current_haendler) */}
              {!sucher && (
                <button onClick={applyDeviations} disabled={busy}
                        className="mt-3 rounded-lg px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                        style={{ background: "var(--accent-red)" }}>
                  Änderungen übernehmen ({selectedDevs.length})
                </button>
              )}
              {v.deviations_applied_at && (
                <span className="ml-3 text-[11px] text-zinc-500">zuletzt übernommen: {fmtDate(v.deviations_applied_at)}</span>
              )}
            </>
          )}
          {report.notes && <div className="mt-3 text-xs text-zinc-400">Bemerkung Fahrer: {report.notes}</div>}
        </Section>
      )}

      {/* Rollenprüfung 22.09.2026 (RP-474): km und neue Schäden aus dem
          UNTERSCHRIEBENEN Abholprotokoll — auch ohne Abhol-Check. Vorher kamen
          sie nie ins Fahrzeug (nur ins Inserat, und auch das erst seit heute). */}
      {befundOffen && (
        <Section title={protokollBefundHinweis({ km: befundKmNeu ? befund.km : null, schaeden: befundNeueSchaeden.length })}
                 warn={<span className="text-[11px] font-normal normal-case text-amber-400">Nach der Unterschrift</span>}>
          <div className="text-xs text-zinc-400 mb-2">
            Im unterschriebenen Abholprotokoll steht etwas, das noch nicht in den Fahrzeugdaten ist:
          </div>
          <div className="text-sm space-y-1" data-testid="akte-protokoll-befund">
            {befundKmNeu && (
              <div>
                Kilometerstand: <span className="font-semibold">{fmtKm(befund.km) || `${befund.km} km`}</span>
                <span className="text-zinc-500"> (bisher {fmtKm(d.mileage) || "—"})</span>
              </div>
            )}
            {befundNeueSchaeden.map((s, i) => <div key={i} className="text-amber-400">• {s}</div>)}
          </div>
          <button onClick={befundUebernehmen} disabled={busy} data-testid="akte-protokoll-uebernehmen"
                  className="mt-3 rounded-lg px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
                  style={{ background: "var(--accent-red)" }}>
            In die Fahrzeugdaten übernehmen
          </button>
        </Section>
      )}

      {/* Fahrzeugdaten + Kauf */}
      {/* grid-cols-1 = minmax(0,1fr): ein langer Name darf die Spalte auf dem
          Handy nicht breiter als den Bildschirm machen (Pruefbefund 11.09.2026). */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Section title="Fahrzeugdaten">
          <KV k="Erstzulassung" val={d.first_registration} />
          <KV k="Kilometerstand" val={fmtKm(d.mileage)} />
          <KV k="Kraftstoff" val={d.fuel_label || d.fuel} />
          <KV k="Getriebe" val={d.gearbox_label || d.gearbox} />
          <KV k="Leistung" val={d.power_ps ? `${d.power_ps} PS` : null} />
          <KV k="Farbe" val={d.color} />
          <KV k="FIN" val={d.vin} />
          {/* Rollenprüfung 22.09.2026 (RP-475): übernommene Schlüssel-Abweichung
              (data.keys_count) — vorher las diesen Wert niemand. */}
          {d.keys_count != null && d.keys_count !== "" && <KV k="Schlüssel" val={String(d.keys_count)} />}
          {(v.known_defects || []).length > 0 && (
            <div className="mt-2 text-xs">
              <div className="text-zinc-500 mb-1">Bekannte Mängel:</div>
              {v.known_defects.map((m, i) => <div key={i} className="text-amber-400">• {m}</div>)}
            </div>
          )}
        </Section>
        {/* Umbau Kaufvorgaenge (09.09.2026): je Vertrag ein Vorgang mit
            eigenem Sucher, Preis, Status und Termin. Chef sieht alle,
            Sucher nur eigene. */}
        {(akte.kaufvorgaenge || []).length > 0 && (
          <Section title="Kaufvorgänge">
            {akte.kaufvorgaenge.map((k) => (
              <div key={k.id} className="py-2 border-b last:border-b-0 text-sm"
                   style={{ borderColor: "var(--wa-04)" }} data-testid={`kaufvorgang-${k.id}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className="min-w-0 truncate font-medium" style={{ color: "var(--text-primary)" }}>
                    {k.user_name || k.user_id}
                  </span>
                  <StatusSchild status={k.status} text={kaufvorgangText(k.status)} />
                </div>
                <div className="mt-1 flex items-center justify-between gap-3 text-xs text-zinc-500">
                  <span style={{ color: "var(--text-secondary)" }}>{fmtEur(k.purchase_price)}</span>
                  <span>{fmtDate(k.created_at)}</span>
                </div>
              </div>
            ))}
            {/* Audit 13.09.2026 (#10): Liste endet bei 50 — nicht still. */}
            {akte.kaufvorgaenge_gesamt > akte.kaufvorgaenge.length && (
              <div className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}
                   data-testid="akte-kaufvorgaenge-gekuerzt">
                {akte.kaufvorgaenge.length} von {akte.kaufvorgaenge_gesamt} Kaufvorgängen angezeigt (die neuesten).
              </div>
            )}
          </Section>
        )}
        <Section title="Beschaffung & Kauf">
          {v.purchase_price != null
            ? <KV k="Einkaufspreis (realisiert)" val={fmtEur(v.purchase_price)} />
            : akte.einkaufspreis?.preis != null
              ? <KV k={akte.einkaufspreis.quelle === "vertrag" ? "Einkaufspreis (aus dem Kaufvertrag)" : "Einkaufspreis"}
                    val={fmtEur(akte.einkaufspreis.preis)} />
              // Rollenprüfung 22.09.2026 (RP-057 b, kaufvorgang.einkaufspreis_vorschlag):
              // mehrere offene Verträge verschiedener Sucher mit verschiedenen
              // Preisen — welcher gilt, entscheidet erst die Abholung.
              : akte.einkaufspreis?.quelle === "mehrdeutig"
                ? <KV k="Einkaufspreis" val="mehrere Verträge mit verschiedenen Preisen — steht nach der Abholung fest" />
                : <KV k="Einkaufspreis" val="—" />}
          <KV k="Quelle" val={v.source === "manuell" ? "Manuell angelegt" : (d.detail_url ? "Inserat (Plattform)" : "Plattform")} />
          {(akte.appointments || []).slice(0, 1).map((a) => (
            <KV key={a.id} k="Geplante Abholung"
                val={`${datumDE(a.pickup_date)}${a.pickup_time ? ` · ${a.pickup_time}\u00a0Uhr` : ""} · ${lesbar(a.status || "offen")}`} />
          ))}
          {(akte.contracts || []).map((c, i) => (
            <button key={c.id || i} type="button" data-testid={`akte-vertrag-${c.id}`}
                    onClick={() => openContractPdf(c.id)
                      .catch((e) => toast.error(errMsg(e, "Kaufvertrag konnte nicht geladen werden")))}
                    className="mt-2 w-full flex items-center justify-between text-sm rounded-lg px-2 py-1.5 hover:bg-white/[0.04] text-left">
              <span className="inline-flex items-center gap-1.5 text-zinc-300">
                {/* Prüfbericht 20.09. U-125: ohne id nicht abstürzen */}
                <FileText size={13} /> Kaufvertrag {c.contract_no || String(c.id || "").slice(0, 8) || "ohne Nummer"}
                {i === 0 && <span className="text-[10px] text-zinc-500">aktuelle Fassung</span>}
              </span>
              <span className="text-zinc-500 text-xs">{fmtDate(c.created_at)}</span>
            </button>
          ))}
          {/* Runde 27: Die Akte zeigt die 10 neuesten — bei mehreren Suchern
              am selben Auto gibt es mehr. Das darf nicht still verschwinden. */}
          {akte.contracts_gesamt > (akte.contracts || []).length && (
            <div className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}
                 data-testid="akte-vertraege-gekuerzt">
              {(akte.contracts || []).length} von {akte.contracts_gesamt} Kaufverträgen angezeigt —
              alle findest du im Vertragsarchiv.
            </div>
          )}
          {akte.appointments_gesamt > (akte.appointments || []).length && (
            <div className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}
                 data-testid="akte-termine-gekuerzt">
              {(akte.appointments || []).length} von {akte.appointments_gesamt} Terminen angezeigt —
              alle stehen im Terminkalender.
            </div>
          )}
          {(akte.comparisons || []).length > 0 && (
            <div className="mt-2 text-xs text-zinc-500" data-testid="akte-vergleiche">
              {akte.comparisons_gesamt ?? akte.comparisons.length} Vergleich(e) durchgeführt
              {(akte.comparisons_gesamt || 0) > akte.comparisons.length
                ? ` — die ${akte.comparisons.length} neuesten angezeigt` : ""}
            </div>
          )}
        </Section>

        {/* Unterschriebene Abhol-Protokolle vom Fahrer (mit Unterschrift des
            Kunden/Verkäufers) — als Unterlage zum Auto, für Chef + Sucher. */}
        {(akte.protocols || []).length > 0 && (
          <Section title="Unterlagen · Abhol-Protokoll">
            {akte.protocols.map((p) => (
              <button key={p.id} type="button"
                 onClick={() => openAuthedFile(`/protocols/${p.id}.pdf`).catch(() => toast.error("Protokoll konnte nicht geladen werden"))}
                 className="mt-2 w-full flex items-center justify-between text-sm rounded-lg px-2 py-1.5 hover:bg-white/[0.04] text-left">
                <span className="inline-flex items-center gap-1.5 text-zinc-200">
                  <PenLine size={13} className="text-[color:var(--accent-green,#34c759)]" />
                  Abhol-Protokoll (unterschrieben)
                  {p.version > 1 && <span className="text-[10px] text-zinc-500">v{p.version}</span>}
                  {/* Prüfbericht 20.09.2026 (R1-22): Versionen zählen je Termin — bei
                      mehreren Terminen zum Fahrzeug sagt das Abholdatum, welcher gemeint ist. */}
                  {p.pickup_date && (
                    <span className="text-[10px] text-zinc-500" data-testid="akte-protokoll-abholung">
                      · Abholung vom {String(p.pickup_date).split("-").reverse().join(".")}
                      {p.pickup_time ? ` ${p.pickup_time}` : ""}
                    </span>
                  )}
                </span>
                <span className="text-zinc-500 text-xs">{fmtDate(p.finalized_at)}</span>
              </button>
            ))}
            <div className="mt-1.5 text-[11px] text-zinc-500">
              Unterschriften von Fahrer und Verkäufer/Kunde · beim Antippen als PDF öffnen
            </div>
            {(akte.protocols_gesamt || 0) > akte.protocols.length && (
              <div className="mt-1 text-[11px]" style={{ color: "var(--text-secondary)" }}
                   data-testid="akte-protokolle-gekuerzt">
                {akte.protocols.length} von {akte.protocols_gesamt} Protokollen angezeigt — die neuesten zuerst.
              </div>
            )}
          </Section>
        )}
      </div>

      {/* Bestand */}
      {/* Runde 19: Standort, Kosten und Notizen bearbeitet nur der Chef (Backend chef-only) */}
      {!sucher && bestandForm && ["bestand", "verkaufsentwurf", "verkaufsbereit", "abgeholt", "reserviert"].includes(v.lifecycle) && (
        <Section title="Bestand · Standort & Kosten">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="text-[11px] text-zinc-500">Standort</label>
              <input value={bestandForm.location}
                     onChange={(e) => setBestandForm((s) => ({ ...s, location: e.target.value }))}
                     className="w-full rounded-lg border bg-transparent px-3 py-2 text-sm"
                     style={{ borderColor: "var(--border-default)" }} placeholder="z.B. Platz 4" />
            </div>
            <div>
              <label className="text-[11px] text-zinc-500">Interne Notizen</label>
              <input value={bestandForm.notes}
                     onChange={(e) => setBestandForm((s) => ({ ...s, notes: e.target.value }))}
                     className="w-full rounded-lg border bg-transparent px-3 py-2 text-sm"
                     style={{ borderColor: "var(--border-default)" }} />
            </div>
          </div>
          <div className="mt-3">
            <div className="flex items-center justify-between">
              <label className="text-[11px] text-zinc-500">Kosten (Transport, Aufbereitung, Reparatur, …)</label>
              <button onClick={() => setBestandForm((s) => ({ ...s, costs: [...s.costs, { label: "", amount: "" }] }))}
                      className="text-xs text-zinc-400 hover:text-white">+ Kostenposition</button>
            </div>
            {bestandForm.costs.map((c, i) => (
              <div key={i} className="mt-1.5 flex gap-2">
                <input value={c.label} placeholder="Bezeichnung" aria-label={`Kostenposition ${i + 1}: Bezeichnung`}
                       onChange={(e) => setBestandForm((s) => ({ ...s, costs: s.costs.map((x, xi) => xi === i ? { ...x, label: e.target.value } : x) }))}
                       className="flex-1 min-w-0 rounded-lg border bg-transparent px-3 py-1.5 text-sm" style={{ borderColor: "var(--border-default)" }} />
                {/* Rollenprüfung 22.09.2026 (RP-449/RP-565): Text statt
                    type="number" — "1.200" blieb sonst 1,20 €, und bei "249,"
                    sprang das Feld auf 0 zurück. Gelesen wird beim Speichern. */}
                <input type="text" inputMode="decimal" autoComplete="off" value={c.amount} placeholder="€"
                       aria-label={`Kostenposition ${i + 1}: Betrag in Euro`}
                       data-testid={`akte-kosten-betrag-${i}`}
                       onChange={(e) => setBestandForm((s) => ({ ...s, costs: s.costs.map((x, xi) => xi === i ? { ...x, amount: e.target.value } : x) }))}
                       className="w-28 rounded-lg border bg-transparent px-3 py-1.5 text-sm text-right" style={{ borderColor: "var(--border-default)" }} />
                <button onClick={() => setBestandForm((s) => ({ ...s, costs: s.costs.filter((_, xi) => xi !== i) }))}
                        aria-label={`Kostenposition ${i + 1} entfernen`}
                        className="text-zinc-500 hover:text-red-400"><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
          <button onClick={saveBestand} disabled={busy} className="mt-3 rounded-lg px-3 py-2 text-xs border font-semibold disabled:opacity-50"
                  style={{ borderColor: "var(--border-default)" }}>
            Bestandsdaten speichern
          </button>
        </Section>
      )}

      {/* Historie */}
      <Section title="Historie">
        <div className="space-y-1 max-h-64 overflow-y-auto">
          {(akte.history || []).map((h) => (
            <div key={h.id} className="flex justify-between gap-4 text-xs py-1 border-b" style={{ borderColor: "var(--wa-04)" }}>
              <span style={{ color: "var(--text-primary)" }}>{aktionText(h.action)}</span>
              <span className="text-zinc-600 whitespace-nowrap">{fmtDate(h.created_at)}</span>
            </div>
          ))}
          {(akte.history || []).length === 0 && <div className="text-xs text-zinc-500">Noch keine Einträge.</div>}
        </div>
        {/* Audit 13.09.2026 (#53): nicht still abschneiden. Fehlt das Feld
            (alter Server beim Rollout), bleibt der Hinweis weg. */}
        {akte.history_gekuerzt && (
          <div className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}
               data-testid="akte-historie-gekuerzt">
            Die {(akte.history || []).length} neuesten Einträge werden angezeigt — ältere sind vorhanden.
          </div>
        )}
      </Section>
    </div>
  );
}
