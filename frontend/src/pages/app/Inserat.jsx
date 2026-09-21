import MonatJahrEingabe from "@/components/MonatJahrEingabe";
import { monatJahrFehler } from "@/lib/monatJahr";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, errMsg, openAuthedFile } from "@/lib/api";
import { openContractPdf } from "@/lib/pdf";
import { thumbSrc, thumbFehler, verkleinereBildDatei } from "@/lib/bilder";
import { INSERAT_LABELS } from "@/lib/fahrzeugStatus";
import { preisAusText, preisText } from "@/lib/preis";
import { toast } from "sonner";
import {
  ArrowLeft, Camera, CheckCircle2, Undo2, Tag, Globe, EyeOff, Trash2, X, FileText, PenLine,
} from "lucide-react";

/**
 * Inserats-Editor: automatisch vorausgefüllter Entwurf aus der Fahrzeugakte
 * (inkl. Abholungs-Abweichungen), Foto-Modus, Preise mit Margen-Rechner,
 * Workflow Entwurf → Verkaufsbereit → Reserviert/Verkauft.
 */

const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE", { minimumFractionDigits: 0 })} €`);

const STATUS_LABELS = INSERAT_LABELS;

export default function Inserat() {
  const { id } = useParams();
  const nav = useNavigate();
  const [l, setL] = useState(null);
  const [busy, setBusy] = useState(false);
  const [abholBusy, setAbholBusy] = useState(false);
  // U-103/H35: waehrend des Hochladens gesperrt (kein zweiter Upload parallel)
  const [ladeHoch, setLadeHoch] = useState(false);
  // Wunsch Ahmad 14.09.2026: Beim Inserieren soll der Chef das unterschriebene
  // Abholprotokoll und den abschliessenden Kaufvertrag weiter oeffnen koennen —
  // nur in SEINER Ansicht. Der Marktplatz bekommt davon nichts: die
  // oeffentliche Sicht (routes/marketplace._public_listing_view) ist eine
  // feste Feldliste ohne Fahrzeug-ID, Vertraege oder Protokolle.
  const [unterlagen, setUnterlagen] = useState(null);
  const fileRef = useRef(null);
  const backend = process.env.REACT_APP_BACKEND_URL;

  // Pruefbericht 20.09.2026 (B18/F1-F4): Jeder Ladefehler (404, 403 fuer
  // Sucher, 500, Funkloch) liess die Seite fuer immer auf "lade…" stehen —
  // ohne Text, ohne Rueckweg, ohne neuen Versuch.
  const [ladeFehler, setLadeFehler] = useState("");
  const load = useCallback(async () => {
    try {
      const r = await api.get(`/resale/${id}`);
      setLadeFehler("");
      setL(r.data);
    } catch (e) {
      const status = e?.response?.status;
      const text = status === 404
        ? "Dieses Inserat gibt es nicht (mehr)."
        : status === 403
          ? errMsg(e, "Inserate bearbeitet der Hauptaccount der Firma.")
          : errMsg(e, "Inserat konnte nicht geladen werden");
      setLadeFehler(text);
      toast.error(text);
    }
  }, [id]);

  useEffect(() => {
    setL(null);
    setLadeFehler("");
    load();
  }, [load]);

  // Pruefbericht 20.09.2026 (U-97): Eigene Fotos haben signierte Links
  // (Standard 1 h). Laedt der Browser danach neu (Tab-Wiederherstellung,
  // Originalgroesse), gab es leere Rahmen. Dann frische Links holen — NUR die
  // Fotoadressen, damit nichts Ungespeichertes ueberschrieben wird.
  const fotoLinksAm = useRef(0);
  const fotoFehler = useCallback(async () => {
    const jetzt = Date.now();
    if (jetzt - fotoLinksAm.current < 60_000) return;
    fotoLinksAm.current = jetzt;
    try {
      const r = await api.get(`/resale/${id}`);
      setL((s) => (s ? { ...s, photo_urls: r.data?.photo_urls || [] } : s));
    } catch { /* bleibt beim leeren Rahmen */ }
  }, [id]);

  // Eingabetext je Preisfeld (so, wie getippt) — gezeigt wird der Text, gerechnet
  // mit der gelesenen Zahl in l.prices.
  const [preisEingabe, setPreisEingabe] = useState({});
  const inseratId = l?.id;
  useEffect(() => {
    if (!inseratId) return;
    const text = (n) => (n === null || n === undefined ? "" : Number(n).toLocaleString("de-DE"));
    setPreisEingabe({ public: text(l?.prices?.public), b2b: text(l?.prices?.b2b),
                      network: text(l?.prices?.network) });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inseratId]);

  const vehicleId = l?.vehicle_id;
  useEffect(() => {
    if (!vehicleId) return undefined;
    let aktiv = true;
    api.get(`/vehicles/${vehicleId}/akte`)
      .then((r) => { if (aktiv) setUnterlagen({ protocols: r.data.protocols || [], contracts: r.data.contracts || [] }); })
      .catch(() => { if (aktiv) setUnterlagen({ protocols: [], contracts: [] }); });
    return () => { aktiv = false; };
  }, [vehicleId]);

  if (!l) {
    return (
      <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="inserat-laedt">
        <Link to="/app/bestand" className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
          <ArrowLeft size={14} /> Zurück zum Bestand
        </Link>
        {ladeFehler ? (
          <div className="tactical-card p-4 mt-4 text-sm" role="alert" data-testid="inserat-ladefehler">
            <div style={{ color: "var(--text-primary)" }}>{ladeFehler}</div>
            <button type="button" onClick={() => { setLadeFehler(""); load(); }}
                    className="mt-3 rounded-lg px-3 py-2 text-xs border font-semibold"
                    style={{ borderColor: "var(--border-default)" }}>
              Erneut versuchen
            </button>
          </div>
        ) : (
          <div className="mt-6 text-zinc-500 text-sm">lade…</div>
        )}
      </div>
    );
  }

  const set = (k) => (e) => setL((s) => ({ ...s, [k]: e.target.value }));
  // Pruefbericht 20.09.2026 (H36/U-104): Preise wurden mit parseFloat bzw. als
  // Zahlenfeld gelesen — "20.900" wurde 20,90 €, "20.900 €" leer, und die
  // Margenanzeige rechnete damit weiter. Jetzt deutsch (preisAusText), und ein
  // unlesbarer Wert blockiert das Speichern mit Hinweis statt still falsch.
  const setPrice = (k) => (e) => {
    const text = e.target.value;
    setPreisEingabe((p) => ({ ...p, [k]: text }));
    const zahl = preisAusText(text);
    setL((s) => ({ ...s, prices: { ...s.prices, [k]: text.trim() === "" ? null : zahl } }));
  };
  const preisFehler = Object.entries(preisEingabe)
    .filter(([, text]) => String(text || "").trim() && preisAusText(text) === null)
    .map(([k]) => k);

  const save = async (extra = {}) => {
    const ezFehler = monatJahrFehler(l.data?.first_registration);
    if (ezFehler) { toast.error(`Erstzulassung: ${ezFehler}`); return false; }
    if (preisFehler.length) {
      toast.error("Bitte die Preise als Zahl eintragen, z. B. 20.900 oder 20900.");
      return false;
    }
    setBusy(true);
    try {
      const r = await api.put(`/resale/${l.id}`, {
        title: l.title, description: l.description,
        known_defects: l.known_defects,
        photo_mode: l.photos?.mode,
        price_public: l.prices?.public, price_b2b: l.prices?.b2b,
        price_network: l.prices?.network,
        costs: l.costs,
        data: l.data,
        ...extra,
      });
      setL(r.data);
      toast.success("Gespeichert");
      return true;
    } catch (e) { toast.error(errMsg(e)); return false; }
    finally { setBusy(false); }
  };

  const setStatus = async (status, soldPrice) => {
    if (busy) return;
    if (status === "verkaufsbereit" && !(await save())) return;
    try {
      await api.post(`/resale/${l.id}/status`, { status, sold_price: soldPrice });
      toast.success(`Status: ${STATUS_LABELS[status] || status}`);
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  // H36/U-104: Der tatsaechliche Verkaufspreis wird deutsch gelesen und vor
  // dem Speichern so angezeigt, wie er verstanden wurde ("20.900" -> 20.900 €).
  const verkauftMelden = () => {
    const vorschlag = l.prices?.public != null ? Number(l.prices.public).toLocaleString("de-DE") : "";
    const roh = window.prompt("Tatsächlicher Verkaufspreis in € (z. B. 20.900):", vorschlag);
    if (roh === null) return;
    if (!roh.trim()) {
      if (window.confirm("Ohne Verkaufspreis als verkauft markieren?")) setStatus("verkauft", null);
      return;
    }
    const preis = preisAusText(roh);
    if (preis === null || preis <= 0) {
      toast.error("Bitte den Verkaufspreis als Zahl eintragen, z. B. 20.900.");
      return;
    }
    if (!window.confirm(`Verkaufspreis ${preisText(preis)} speichern?`)) return;
    setStatus("verkauft", preis);
  };

  const removeListing = async () => {
    if (!window.confirm(
      "Inserat wirklich löschen?\n\nHinweis: Bereits veröffentlichte Inserate "
      + "können jederzeit wieder veröffentlicht werden.")) return;
    try {
      await api.delete(`/resale/${l.id}`);
      toast.success("Inserat gelöscht");
      nav("/app/bestand");
    } catch (e) { toast.error(errMsg(e)); }
  };

  const publish = async (visibility = "public") => {
    if (busy) return;
    if (!(await save())) return;           // zuerst aktuellen Stand sichern (v.a. Preis)
    try {
      await api.post(`/resale/${l.id}/publish`, { visibility });
      toast.success("Auf dem Marktplatz veröffentlicht");
      load();
    } catch (e) {
      // 402 = kein Verkaufspaket / Kontingent voll -> aussagekräftige Meldung
      toast.error(errMsg(e, "Veröffentlichen nicht möglich"));
    }
  };

  // Nachpruefung 20.09.2026 (Nr. 52/53): Hier wurden bis zu 20 Fotos
  // UNVERKLEINERT als Base64 gelesen und in EINER Anfrage geschickt.
  // Ein Handyfoto hat oft 5-8 MB, Base64 macht daraus rund ein Drittel
  // mehr — schon drei Fotos sprengten die 25 MB, die nginx je Anfrage
  // durchlaesst (deploy/nginx.conf), und der Nutzer sah nur einen
  // unverstaendlichen Fehler. Jetzt wird jedes Foto im Browser auf 2000 px
  // verkleinert (dabei fallen auch Aufnahmeort und Geraet weg) und in
  // kleinen Paketen hochgeladen.
  const FOTOS_JE_PAKET = 4;
  // Pruefbericht 20.09.2026 (DP-01/U1): Pakete zusaetzlich nach GROESSE —
  // scheiterte die Verkleinerung (HEIC, defektes EXIF), ging das Original
  // (6-8 MB) mit, und vier davon sprengten die 25 MB des Proxys.
  const PAKET_ZEICHEN_MAX = 15_000_000;
  const EINZELFOTO_ZEICHEN_MAX = 12_000_000;
  // Regeln vom 20.09.2026 (Ahmad) — dieselben Zahlen wie im Server
  // (routes/resale.py: INSERAT_BESCHREIBUNG_MAX / INSERAT_FOTOS_MAX).
  const BESCHREIBUNG_MAX = 500;
  const FOTOS_MAX = 10;

  const uploadPhotos = async (files) => {
    if (!files?.length || ladeHoch) return;
    // 20.09.2026 (Ahmad): hoechstens FOTOS_MAX je Inserat. Lieber hier
    // abschneiden und es sagen, als den Server 400 werfen lassen, nachdem
    // der Nutzer zehn Fotos hochgeladen hat.
    const frei = FOTOS_MAX - (l.photos?.uploaded_keys || []).length;
    if (frei <= 0) {
      toast.error(`Dieses Inserat hat schon ${FOTOS_MAX} Fotos — bitte zuerst eines entfernen.`);
      return;
    }
    const auswahl = [...files].slice(0, frei);
    if (files.length > frei) {
      toast.message(`Es werden ${frei} von ${files.length} Fotos übernommen (maximal ${FOTOS_MAX} je Inserat).`);
    }
    setLadeHoch(true);
    try {
      const photos = [];
      let zuGross = 0;
      for (const f of auswahl) {
        const bild = await verkleinereBildDatei(f);
        if (String(bild || "").length > EINZELFOTO_ZEICHEN_MAX) { zuGross += 1; continue; }
        photos.push(bild);
      }
      if (zuGross) {
        toast.warning(`${zuGross} Foto(s) zu groß und nicht verkleinerbar (z. B. HEIC) — bitte als JPG aufnehmen oder speichern.`);
      }
      // Pakete: hoechstens FOTOS_JE_PAKET Fotos UND hoechstens PAKET_ZEICHEN_MAX.
      const pakete = [];
      let aktuell = [];
      let groesse = 0;
      for (const bild of photos) {
        const n = String(bild).length;
        if (aktuell.length && (aktuell.length >= FOTOS_JE_PAKET || groesse + n > PAKET_ZEICHEN_MAX)) {
          pakete.push(aktuell);
          aktuell = [];
          groesse = 0;
        }
        aktuell.push(bild);
        groesse += n;
      }
      if (aktuell.length) pakete.push(aktuell);
      let fertig = 0;
      for (const paket of pakete) {
        await api.post(`/resale/${l.id}/photos`, { photos_b64: paket });
        fertig += paket.length;
      }
      if (fertig) toast.success(`${fertig} Foto(s) hochgeladen`);
      load();
    } catch (e) {
      toast.error(e?.response?.status === 413
        ? "Die Fotos sind zu groß für eine Übertragung — bitte weniger Fotos auf einmal hochladen."
        : errMsg(e));
    } finally {
      setLadeHoch(false);
    }
  };

  // Runde 21: Fotos aus dem Abholbericht (z.B. Schaeden) mit einem Klick uebernehmen.
  const fahrerfotosUebernehmen = async () => {
    if (abholBusy) return;                 // Doppelklick-Sperre
    setAbholBusy(true);
    try {
      const r = await api.post(`/resale/${l.id}/photos/aus-abholbericht`, {});
      toast.success(`${r.data?.uebernommen || 0} Foto(s) vom Fahrer übernommen`);
      await load();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setAbholBusy(false); }
  };

  const margin = l.margin || {};
  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };
  const removePhoto = async (which) => {
    if (!window.confirm(which.key
        ? "Dieses hochgeladene Bild endgültig löschen?"
        : "Dieses Einkaufsfoto aus dem Inserat entfernen?\n(Das Original bleibt in der Fahrzeugakte.)")) return;
    try {
      const r = await api.post(`/resale/${l.id}/photos/remove`, which);
      setL((s) => ({ ...s, photos: { ...s.photos,
        ...(r.data.uploaded_keys ? { uploaded_keys: r.data.uploaded_keys } : {}),
        ...(r.data.einkauf_urls ? { einkauf_urls: r.data.einkauf_urls } : {}) } }));
      toast.success("Bild entfernt" + (l.status === "veroeffentlicht" ? " — Änderung ist sofort live" : ""));
      // Pruefbericht 20.09.2026 (U-96): die Vorschaubilder (einkauf_thumbs)
      // haengen am Index — ohne Neuladen waren sie danach verschoben.
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const einkaufFotos = l.photos?.einkauf_urls || [];
  const uploadedKeys = l.photos?.uploaded_keys || [];
  // Signierte, kurzlebige Links (Audit 09/2026) — Fallback nur fuer alte Antworten
  const fotoUrl = (k) => (l.photo_urls || []).find((p) => p.key === k)?.url || `${backend}/api/files/${k}`;
  const mode = l.photos?.mode || "einkauf";

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="inserat-page">
      <Link to={`/app/akte/${l.vehicle_id}`} className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
        <ArrowLeft size={14} /> Zur Fahrzeugakte
      </Link>
      {unterlagen && (unterlagen.protocols.length > 0 || unterlagen.contracts.length > 0) && (
        <div className="mt-3 rounded-xl border px-4 py-3" data-testid="inserat-unterlagen"
             style={{ borderColor: "var(--border-default)" }}>
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">
            Unterlagen zum Auto — nur für dich, nie im Marktplatz sichtbar
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {unterlagen.protocols.map((p) => (
              <button key={p.id} type="button" data-testid={`inserat-protokoll-${p.id}`}
                      onClick={() => openAuthedFile(`/protocols/${p.id}.pdf`)
                        .catch(() => toast.error("Protokoll konnte nicht geladen werden"))}
                      className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-zinc-200 hover:bg-white/5"
                      style={{ borderColor: "var(--border-default)" }}>
                <PenLine size={13} className="text-[color:var(--accent-green,#34c759)]" />
                Abhol-Protokoll (unterschrieben){p.version > 1 ? ` v${p.version}` : ""}
              </button>
            ))}
            {unterlagen.contracts.map((c, i) => (
              <button key={c.id} type="button" data-testid={`inserat-vertrag-${c.id}`}
                      onClick={() => openContractPdf(c.id)
                        .catch((e) => toast.error(errMsg(e, "Kaufvertrag konnte nicht geladen werden")))}
                      className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs text-zinc-200 hover:bg-white/5"
                      style={{ borderColor: "var(--border-default)" }}>
                <FileText size={13} />
                Kaufvertrag {c.contract_no || c.id.slice(0, 8)}{i === 0 ? " · aktuelle Fassung" : ""}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="overline">Verkaufsinserat · {STATUS_LABELS[l.status] || l.status}</div>
          <h1 className="font-display font-black text-2xl tracking-tighter mt-1">Inserat bearbeiten</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          {l.status === "entwurf" && (
            <button onClick={() => setStatus("verkaufsbereit")} disabled={busy}
                    className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                    style={{ background: "var(--accent-red)" }}>
              <CheckCircle2 size={16} /> Verkaufsbereit machen
            </button>
          )}
          {l.status === "verkaufsbereit" && (
            <>
              <button onClick={() => publish("public")} disabled={busy}
                      className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                      style={{ background: "var(--accent-red)" }}>
                <Globe size={16} /> Öffentlich veröffentlichen
              </button>
              <button onClick={() => publish("private")} disabled={busy}
                      title="Nur für eingeladene Netzwerk-Partner sichtbar"
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold border"
                      style={st}>
                <EyeOff size={14} /> Nur Netzwerk (privat)
              </button>
              <button onClick={() => setStatus("reserviert")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Reservieren</button>
              <button onClick={() => {
                        verkauftMelden();
                      }}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold text-white"
                      style={{ background: "var(--st-gruen)" }}>
                <Tag size={13} /> Verkauft
              </button>
              <button onClick={() => setStatus("entwurf")} className="rounded-xl px-3 py-2 text-xs text-zinc-400 hover:text-white inline-flex items-center gap-1"><Undo2 size={13} /> Zurück zu Entwurf</button>
            </>
          )}
          {l.status === "veroeffentlicht" && (
            <>
              <span className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold"
                    style={{ background: "#34c75920", color: "var(--st-gruen)", border: "1px solid #34c75955" }}>
                <Globe size={14} /> Live auf dem Marktplatz
              </span>
              <button onClick={() => setStatus("reserviert")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Reservieren</button>
              <button onClick={() => {
                        verkauftMelden();
                      }}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold text-white"
                      style={{ background: "var(--st-gruen)" }}>
                <Tag size={13} /> Verkauft
              </button>
              <button onClick={() => setStatus("zurueckgezogen")} className="rounded-xl px-3 py-2 text-xs text-zinc-400 hover:text-white inline-flex items-center gap-1">
                <EyeOff size={13} /> Vom Marktplatz nehmen
              </button>
            </>
          )}
          {l.status === "zurueckgezogen" && (
            <>
              <button onClick={() => publish("public")} disabled={busy}
                      className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2.5 text-sm font-semibold text-white"
                      style={{ background: "var(--accent-red)" }}>
                <Globe size={16} /> Erneut veröffentlichen
              </button>
              <button onClick={() => setStatus("verkaufsbereit")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Auf „verkaufsbereit" setzen</button>
            </>
          )}
          {l.status === "reserviert" && (
            <>
              <button onClick={() => {
                        verkauftMelden();
                      }}
                      className="rounded-xl px-4 py-2.5 text-sm font-semibold text-white" style={{ background: "var(--st-gruen)" }}>
                Als verkauft markieren
              </button>
              <button onClick={() => setStatus("verkaufsbereit")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Reservierung aufheben</button>
            </>
          )}
        </div>
      </div>

      {(l.auto_notes || []).length > 0 && (
        <div className="mt-3 rounded-xl border px-4 py-3 text-xs space-y-0.5"
             style={{ borderColor: "#0ea5e955", background: "#0ea5e914", color: "var(--tx-cyan)" }}>
          {l.auto_notes.map((n, i) => <div key={i}>ℹ {n}</div>)}
        </div>
      )}

      <div className="grid lg:grid-cols-3 gap-4 mt-4">
        {/* Linke Spalte: Inhalt */}
        <div className="lg:col-span-2 space-y-4">
          <div className="tactical-card p-4">
            <label className="text-[11px] text-zinc-500">Titel</label>
            <input value={l.title || ""} onChange={set("title")} className={inputCls} style={st}
                   disabled={l.status === "verkauft"} />
            <div className="mt-3 flex items-baseline justify-between">
              <label className="text-[11px] text-zinc-500">Beschreibung</label>
              <span className={`text-[11px] ${(l.description || "").length > BESCHREIBUNG_MAX
                ? "text-red-400 font-semibold" : "text-zinc-500"}`}
                    data-testid="beschreibung-zaehler">
                {(l.description || "").length} / {BESCHREIBUNG_MAX}
              </span>
            </div>
            <textarea value={l.description || ""} onChange={set("description")} rows={7}
                      maxLength={BESCHREIBUNG_MAX}
                      className={inputCls} style={st} disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-3 block">Bekannte Mängel (eine je Zeile)</label>
            <textarea value={(l.known_defects || []).join("\n")}
                      onChange={(e) => setL((s) => ({ ...s, known_defects: e.target.value.split("\n") }))}
                      rows={4} className={inputCls} style={st} disabled={l.status === "verkauft"} />
          </div>

          {/* Fahrzeugdaten: 1:1 aus der Akte übernommen — vor Veröffentlichung
              prüfbar/korrigierbar; erscheinen so beim B2B-Käufer. */}
          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-3">Fahrzeugdaten</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
              {[
                ["make_label", "Marke"], ["model_label", "Modell"],
                ["first_registration", "Erstzulassung"], ["mileage", "Kilometerstand"],
                ["power_ps", "PS"], ["fuel_label", "Kraftstoff"],
                ["gearbox_label", "Getriebe"], ["color", "Farbe"],
                ["previous_owners", "Halter-Anzahl"],
              ].map(([k, label]) => (
                <div key={k}>
                  <label className="text-[11px] text-zinc-500">{label}</label>
                  {k === "first_registration" ? (
                    <MonatJahrEingabe value={String(l.data?.[k] ?? "")} disabled={l.status === "verkauft"}
                                      onChange={(v) => setL((s) => ({ ...s, data: { ...s.data, [k]: v } }))}
                                      art="ez" className={inputCls} style={st} />
                  ) : (
                    <input value={l.data?.[k] ?? ""} disabled={l.status === "verkauft"}
                           onChange={(e) => setL((s) => ({ ...s, data: { ...s.data, [k]: e.target.value } }))}
                           className={inputCls} style={st} />
                  )}
                </div>
              ))}
              <div>
                <label className="text-[11px] text-zinc-500">Unfallfrei</label>
                <select value={l.data?.accident_free ?? ""} disabled={l.status === "verkauft"}
                        onChange={(e) => setL((s) => ({ ...s, data: { ...s.data, accident_free: e.target.value } }))}
                        className={inputCls + " bg-[var(--bg-elevated)]"} style={st}>
                  <option value="">— bitte angeben —</option>
                  <option value="Ja">Ja</option>
                  <option value="Nein">Nein</option>
                </select>
              </div>
            </div>
            <div className="mt-2 text-[10px] text-zinc-600">
              Wird 1:1 aus dem Einkauf übernommen — bitte vor der Veröffentlichung prüfen.
            </div>
          </div>

          {/* Fotos */}
          <div className="tactical-card p-4">
            <div className="flex items-center justify-between">
              <div className="text-sm font-bold uppercase tracking-wide">Fotos</div>
              <button onClick={() => fileRef.current?.click()}
                      className="inline-flex items-center gap-1.5 text-xs text-zinc-300 hover:text-white">
                <Camera size={14} /> Neue Fotos hochladen
              </button>
              <input ref={fileRef} type="file" accept="image/*" multiple className="hidden"
                     disabled={ladeHoch}
                     onChange={(e) => { const f = e.target.files; uploadPhotos(f ? [...f] : []); e.target.value = ""; }} />
            </div>
            {/* 20.09.2026 (Ahmad): Der Umschalter Einkauf/Neu/Beide ist weg.
                Fotos aus dem urspruenglichen Inserat werden nicht mehr
                uebernommen — sie gehoeren dem Verkaeufer bzw. dem Portal.
                Jedes Inserat braucht eigene Fotos. */}
            <div className="mt-2 text-[11px] text-zinc-500" data-testid="fotos-regel">
              {uploadedKeys.length} / {FOTOS_MAX} eigene Fotos
              {einkaufFotos.length > 0 && ` · ${einkaufFotos.length} aus dem alten Bestand`}
              {" · "}Fotos aus dem ursprünglichen Inserat werden nicht übernommen.
            </div>
            {(l.abholfotos || []).length > 0 && !["verkauft", "geloescht"].includes(l.status) && (
              <div className="mt-3 rounded-lg p-2.5 flex flex-wrap items-center gap-2" data-testid="abholfotos-hinweis"
                   style={{ background: "rgba(56,189,248,0.06)", border: "1px solid rgba(56,189,248,0.25)" }}>
                <button onClick={fahrerfotosUebernehmen} data-testid="abholfotos-uebernehmen" disabled={abholBusy}
                        className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-300 hover:text-sky-200">
                  <Camera size={14} /> {abholBusy ? "Wird übernommen…" : `${l.abholfotos.length} Foto${l.abholfotos.length === 1 ? "" : "s"} vom Fahrer übernehmen`}
                </button>
                <span className="text-[11px] text-zinc-500">
                  Aus dem Abholbericht, z.B. Schäden. Übernommene Fotos bleiben im Inserat, auch wenn der
                  Abholbericht seine Fotos nach {l.fahrerfoto_tage || 90} Tagen löscht.
                </span>
              </div>
            )}
            <div className="mt-3 grid grid-cols-4 sm:grid-cols-6 gap-2">
              {(mode !== "neu") && einkaufFotos.slice(0, 12).map((u, i) => (
                <div key={`e${i}`} className="relative group">
                  <a href={u} target="_blank" rel="noreferrer" title="Foto in Originalgröße öffnen">
                    <img src={thumbSrc(l.einkauf_thumbs?.[i], u)} alt="" loading="lazy" referrerPolicy="no-referrer"
                         onError={(e) => thumbFehler(e, u)}
                         className="aspect-square w-full object-cover rounded-lg opacity-90 hover:opacity-100 cursor-zoom-in" />
                  </a>
                  <button onClick={() => removePhoto({ url: u })}
                          data-testid={`foto-del-e${i}`}
                          title="Bild aus dem Inserat entfernen (Original bleibt in der Akte)"
                          aria-label="Bild aus dem Inserat entfernen"
                          className="foto-aktion absolute top-1 right-1 w-9 h-9 rounded-full flex items-center justify-center text-white transition"
                          style={{ background: "rgba(0,0,0,0.7)" }}>
                    <X size={13} />
                  </button>
                </div>
              ))}
              {(mode !== "einkauf") && uploadedKeys.map((k) => (
                <div key={k} className="relative group">
                  <a href={fotoUrl(k)} target="_blank" rel="noreferrer" title="Foto in Originalgröße öffnen">
                    <img src={fotoUrl(k)} alt="" onError={fotoFehler}
                         className="aspect-square w-full object-cover rounded-lg hover:opacity-90 cursor-zoom-in" />
                  </a>
                  <button onClick={() => removePhoto({ key: k })}
                          data-testid={`foto-del-${k.slice(-8)}`}
                          title="Bild endgültig löschen"
                          aria-label="Bild endgültig löschen"
                          className="foto-aktion absolute top-1 right-1 w-9 h-9 rounded-full flex items-center justify-center text-white transition"
                          style={{ background: "rgba(0,0,0,0.7)" }}>
                    <X size={13} />
                  </button>
                </div>
              ))}
              {mode === "neu" && uploadedKeys.length === 0 && (
                <div className="col-span-full text-xs text-zinc-500 py-4">Noch keine neuen Fotos hochgeladen.</div>
              )}
            </div>
          </div>
        </div>

        {/* Rechte Spalte: Preise + Marge */}
        <div className="space-y-4">
          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-2">Preise</div>
            <label className="text-[11px] text-zinc-500">Verkaufspreis (öffentlich) *</label>
            <input type="text" inputMode="decimal" value={preisEingabe.public ?? ""} onChange={setPrice("public")}
                   aria-invalid={preisFehler.includes("public")}
                   className={inputCls} style={st} placeholder="20.900" disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-2 block">B2B-Preis (optional)</label>
            <input type="text" inputMode="decimal" value={preisEingabe.b2b ?? ""} onChange={setPrice("b2b")}
                   aria-invalid={preisFehler.includes("b2b")}
                   className={inputCls} style={st} disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-2 block">Privater Netzwerkpreis (optional)</label>
            <input type="text" inputMode="decimal" value={preisEingabe.network ?? ""} onChange={setPrice("network")}
                   aria-invalid={preisFehler.includes("network")}
                   className={inputCls} style={st} disabled={l.status === "verkauft"} />
          </div>

          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-2">Kalkulation</div>
            <div className="space-y-1 text-sm">
              <div className="flex justify-between">
                <span className="text-zinc-500">
                  Einkaufspreis
                  {margin.purchase_price_quelle === "vertrag" && <span className="ml-1 text-[11px] text-zinc-500">(aus dem Kaufvertrag)</span>}
                  {margin.purchase_price_quelle === "abgeholt" && <span className="ml-1 text-[11px] text-zinc-500">(bei Abholung)</span>}
                  {margin.purchase_price_quelle === "fahrzeug" && <span className="ml-1 text-[11px] text-zinc-500">(Fahrzeugakte)</span>}
                </span>
                <span data-testid="kalkulation-einkaufspreis">{fmtEur(margin.purchase_price)}</span>
              </div>
              <div className="flex justify-between"><span className="text-zinc-500">Kosten gesamt</span><span>{fmtEur(margin.costs_total)}</span></div>
              <div className="flex justify-between border-t pt-1" style={st}><span className="text-zinc-500">Gesamtkosten</span><span>{fmtEur(margin.total_cost)}</span></div>
              <div className="flex justify-between text-base font-bold pt-1">
                <span>Erwartete Marge</span>
                {(() => {
                  // M39: Farbe und angezeigter Wert aus DERSELBEN Zahl — vorher
                  // war ein Verlustgeschaeft nach dem Verkauf gruen.
                  const marge = l.status === "verkauft" && l.sold_price != null
                    ? l.sold_price - (margin.total_cost || 0)
                    : margin.expected_margin;
                  return (
                    <span style={{ color: (marge ?? 0) >= 0 ? "var(--st-gruen)" : "var(--st-rot)" }}>
                      {fmtEur(marge)}
                    </span>
                  );
                })()}
              </div>
              {l.status === "verkauft" && (
                <div className="flex justify-between text-xs text-zinc-500">
                  <span>Verkauft für</span><span>{fmtEur(l.sold_price)}</span>
                </div>
              )}
            </div>
            <div className="mt-2 text-[10px] text-zinc-600">
              Kosten werden in der Fahrzeugakte gepflegt (Transport, Aufbereitung, …).
            </div>
          </div>

          {l.status !== "verkauft" && (
            <button onClick={() => save()} disabled={busy}
                    className="w-full rounded-xl py-3 text-sm font-semibold border disabled:opacity-50" style={st}>
              {busy ? "Speichert…" : "Änderungen speichern"}
            </button>
          )}
          <AnfragenKarte listingId={id} />

          {l.status !== "verkauft" && (
            <button onClick={removeListing}
                    className="w-full rounded-xl py-2.5 text-xs text-zinc-500 hover:text-red-400 inline-flex items-center justify-center gap-1.5">
              <Trash2 size={13} /> Inserat löschen
            </button>
          )}
        </div>
      </div>
    </div>
  );
}


/** Eingehende Marktplatz-Anfragen zu DIESEM Inserat (beantwortet werden
 *  sie zentral unter /app/anfragen). Sucher bekommen auf dem dealer-only
 *  Endpunkt 403 — die Karte bleibt dann einfach leer. */
function AnfragenKarte({ listingId }) {
  const [anfragen, setAnfragen] = useState(null);
  useEffect(() => {
    api.get("/dealer/interessen", { params: { listing_id: listingId } })
      .then((r) => setAnfragen(Array.isArray(r.data) ? r.data : []))
      .catch(() => setAnfragen(null));
  }, [listingId]);
  if (!anfragen || anfragen.length === 0) return null;
  const offen = anfragen.filter((a) => a.status === "offen").length;
  return (
    <div className="tactical-card p-4" data-testid="inserat-anfragen">
      <div className="text-sm font-bold uppercase tracking-wide mb-2">Kaufanfragen</div>
      <div className="space-y-1.5 text-sm">
        {anfragen.slice(0, 4).map((a) => (
          <div key={a.id} className="flex items-center justify-between gap-2">
            <span className="truncate" style={{ color: "var(--text-secondary)" }}>{a.buyer_name}</span>
            <span className="shrink-0 text-[12px]" style={{ color: "var(--text-muted)" }}>
              {a.offer != null ? `${Number(a.offer).toLocaleString("de-DE")} €` : "ohne Angebot"} · {a.status}
            </span>
          </div>
        ))}
      </div>
      <Link to="/app/anfragen"
            className="mt-3 inline-block text-[12.5px] font-semibold hover:underline"
            style={{ color: "var(--accent-red)" }}>
        {offen > 0 ? `${offen} offene Anfrage(n) beantworten ›` : "Alle Anfragen ansehen ›"}
      </Link>
    </div>
  );
}
