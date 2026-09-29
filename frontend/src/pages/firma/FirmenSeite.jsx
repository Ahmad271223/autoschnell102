import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Building2, CheckCircle2, ExternalLink, KeyRound, Loader2, MapPin, Phone, Mail, Clock } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { blobOeffnen } from "@/lib/dateiOeffnen";
import SignaturePad from "@/components/SignaturePad";

/**
 * Öffentliche Firmenseite (Wunsch Ahmad 29.09.2026): Logo, „Über uns“, Bilder, Kontakt/Impressum
 * und das Kundenportal. Der Kunde gibt den 6-stelligen Code aus der App ein, sieht seinen
 * Kaufvertrag direkt auf der Seite (Handy oder Laptop), unterschreibt mit Finger oder Maus und
 * sendet ab — Sucher und Chef bekommen die Bestätigung in der App.
 *
 * Erreichbar über die Adresse der Firma (kfz-mueller.auto-schnellkauf.de, eigene Domain) oder
 * /firma/<slug> auf der Hauptadresse. Keine Anmeldung; jede Anfrage trägt Host bzw. Slug.
 */
const BACKEND = process.env.REACT_APP_BACKEND_URL || "";
const CODE_LAENGE = 6;

function bildSrc(url) {
  return url?.startsWith("http") ? url : `${BACKEND}${url}`;
}

function eur(w) {
  if (w == null || w === "") return "";
  const n = Number(w);
  return Number.isNaN(n) ? String(w) : n.toLocaleString("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
}

function datum(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function FirmenSeite() {
  const { slug } = useParams();
  const host = typeof window !== "undefined" ? window.location.hostname : "";
  const [firma, setFirma] = useState(null);
  const [fehler, setFehler] = useState("");
  const [laedt, setLaedt] = useState(true);

  useEffect(() => {
    let aktiv = true;
    (async () => {
      try {
        const { data } = await api.get("/public/firma", { params: slug ? { slug } : { host } });
        if (aktiv) { setFirma(data); document.title = `${data.firma} – Kundenportal`; }
      } catch (e) {
        if (aktiv) setFehler(e?.response?.status === 404 ? "Zu dieser Adresse gibt es keine Firmenseite." : errMsg(e, "Die Seite konnte nicht geladen werden"));
      } finally { if (aktiv) setLaedt(false); }
    })();
    return () => { aktiv = false; };
  }, [slug, host]);

  if (laedt) {
    return <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg-deep)", color: "var(--text-strong)" }}><Loader2 className="animate-spin" /></div>;
  }
  if (!firma) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6 text-center" style={{ background: "var(--bg-deep)", color: "var(--text-strong)" }} data-testid="firmenseite-fehler">
        <div>
          <Building2 size={28} className="mx-auto mb-3 text-zinc-500" />
          <div className="font-display font-bold text-xl">{fehler || "Firmenseite nicht gefunden"}</div>
        </div>
      </div>
    );
  }
  const k = firma.kontakt || {};
  return (
    <div className="min-h-screen" style={{ background: "var(--bg-deep)", color: "var(--text-strong)" }} data-testid="firmenseite">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 sm:py-12">
        {/* Kopf: Logo + Name */}
        <header className="flex items-center gap-4 mb-8">
          <div className="w-20 h-20 sm:w-24 sm:h-24 rounded-2xl overflow-hidden flex items-center justify-center shrink-0"
               style={{ background: "#fff", border: "1px solid var(--divider)" }}>
            {firma.logo_url ? <img src={bildSrc(firma.logo_url)} alt={`Logo ${firma.firma}`} className="w-full h-full object-contain p-1" data-testid="firmenseite-logo" />
              : <Building2 size={30} className="text-zinc-400" />}
          </div>
          <div className="min-w-0">
            <h1 className="font-display font-black text-2xl sm:text-3xl tracking-tight leading-tight" data-testid="firmenseite-name">{firma.firma}</h1>
            {(k.plz || k.ort) && <div className="text-sm text-zinc-400 mt-1">{[k.plz, k.ort].filter(Boolean).join(" ")}</div>}
          </div>
        </header>

        {/* Kundenportal — ganz oben, das ist der Grund des Besuchs */}
        <Kundenportal firma={firma} slug={slug} host={host} />

        {firma.ueber_uns && (
          <section className="mt-10" data-testid="firmenseite-ueber-uns">
            <h2 className="font-display font-bold text-xl mb-2">Über uns</h2>
            <p className="text-[15px] leading-relaxed text-zinc-300 whitespace-pre-line">{firma.ueber_uns}</p>
          </section>
        )}

        {firma.bilder?.length > 0 && (
          <section className="mt-8" data-testid="firmenseite-bilder">
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {firma.bilder.map((b) => (
                <div key={b} className="aspect-[4/3] rounded-xl overflow-hidden" style={{ background: "var(--wa-05)" }}>
                  <img src={bildSrc(b)} alt="" loading="lazy" className="w-full h-full object-cover" />
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="mt-10 rounded-2xl p-5" style={{ background: "var(--wa-05)", border: "1px solid var(--divider)" }} data-testid="firmenseite-kontakt">
          <h2 className="font-display font-bold text-lg mb-3">Kontakt &amp; Anbieter</h2>
          <div className="space-y-1.5 text-[14px] text-zinc-300">
            <div className="font-semibold text-white">{firma.firma}</div>
            {(k.adresse || k.plz || k.ort) && <div className="flex items-start gap-2"><MapPin size={15} className="mt-0.5 shrink-0" /><span>{k.adresse}{k.adresse && (k.plz || k.ort) ? ", " : ""}{[k.plz, k.ort].filter(Boolean).join(" ")}</span></div>}
            {k.telefon && <div className="flex items-center gap-2"><Phone size={15} /><a href={`tel:${k.telefon.replace(/\s+/g, "")}`} className="underline">{k.telefon}</a></div>}
            {k.email && <div className="flex items-center gap-2"><Mail size={15} /><a href={`mailto:${k.email}`} className="underline">{k.email}</a></div>}
            {k.oeffnungszeiten && <div className="flex items-start gap-2"><Clock size={15} className="mt-0.5 shrink-0" /><span className="whitespace-pre-line">{k.oeffnungszeiten}</span></div>}
          </div>
        </section>

        <footer className="mt-10 pt-5 border-t text-[12px] text-zinc-500" style={{ borderColor: "var(--wa-08)" }}>
          Diese Seite wird von {firma.firma} über die Plattform AutoSchnell betrieben. Verantwortlich für die Inhalte ist {firma.firma} (Angaben oben).
        </footer>
      </div>
    </div>
  );
}

/** Der Kasten "Kundenportal": Code -> Vertrag lesen -> unterschreiben -> fertig. */
function Kundenportal({ firma, slug, host }) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [fehler, setFehler] = useState("");
  const [sitzung, setSitzung] = useState(null);       // {sitzung, vertrag}
  const [pdfBlob, setPdfBlob] = useState(null);
  const [seitenStand, setSeitenStand] = useState("leer");   // leer | laedt | fertig | fehlgeschlagen
  const seitenRef = useRef(null);
  const [name, setName] = useState("");
  const [unterschrift, setUnterschrift] = useState(null);
  const [einverstanden, setEinverstanden] = useState(false);
  const [fertig, setFertig] = useState(null);

  const oeffnen = async (e) => {
    e?.preventDefault?.();
    const c = code.toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (c.length !== CODE_LAENGE) { setFehler(`Der Code hat ${CODE_LAENGE} Zeichen.`); return; }
    setBusy(true); setFehler("");
    try {
      const { data } = await api.post("/public/portal/oeffnen", { code: c, ...(slug ? { slug } : { host }) });
      setSitzung(data);
      setName(data?.vertrag?.verkaeufer || "");
      if (data?.vertrag?.status === "unterschrieben") setFertig({ unterschrieben_am: data.vertrag.unterschrieben_am, contract_no: data.vertrag.contract_no });
    } catch (err) {
      setFehler(err?.response?.status === 429 ? "Zu viele Versuche — bitte in 10 Minuten erneut." : errMsg(err, "Code ungültig oder abgelaufen."));
    } finally { setBusy(false); }
  };

  // Vertrag laden und Seiten rendern, sobald die Sitzung steht
  const pdfLaden = useCallback(async () => {
    if (!sitzung?.sitzung) return;
    setSeitenStand("laedt");
    try {
      const { data } = await api.get(`/public/portal/${sitzung.sitzung}/pdf`, { responseType: "blob" });
      setPdfBlob(data);
      const { pdfSeitenRendern } = await import("@/lib/pdfAnzeige");
      const breite = Math.min(820, (seitenRef.current?.clientWidth || 720));
      const seiten = await pdfSeitenRendern(data, breite);
      if (seitenRef.current) {
        seitenRef.current.replaceChildren(...seiten);
      }
      setSeitenStand(seiten.length ? "fertig" : "fehlgeschlagen");
    } catch {
      setSeitenStand("fehlgeschlagen");
    }
  }, [sitzung?.sitzung]);
  useEffect(() => { pdfLaden(); }, [pdfLaden, fertig?.unterschrieben_am]);

  const pdfOeffnen = () => {
    if (!pdfBlob) return;
    blobOeffnen(pdfBlob, { titel: "Der Kaufvertrag", dateiname: `Kaufvertrag-${sitzung?.vertrag?.contract_no || ""}.pdf`, mime: "application/pdf" });
  };

  const absenden = async () => {
    if (!unterschrift) { setFehler("Bitte im Feld unterschreiben."); return; }
    if (!einverstanden) { setFehler("Bitte bestätigen, dass Sie den Vertrag gelesen haben."); return; }
    if (name.trim().length < 2) { setFehler("Bitte Ihren Namen eintragen."); return; }
    setBusy(true); setFehler("");
    try {
      const { data } = await api.post(`/public/portal/${sitzung.sitzung}/unterschreiben`, { signature_b64: unterschrift, name: name.trim(), einverstanden: true });
      setFertig(data);
      try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { /* jsdom / alte Browser */ }
    } catch (err) {
      setFehler(errMsg(err, "Die Unterschrift konnte nicht gesendet werden — bitte erneut versuchen."));
    } finally { setBusy(false); }
  };

  const v = sitzung?.vertrag;
  return (
    <section className="rounded-2xl p-5 sm:p-6" style={{ background: "var(--wa-05)", border: "1px solid var(--divider)" }} data-testid="kundenportal">
      <div className="flex items-center gap-2 mb-1">
        <KeyRound size={18} style={{ color: "var(--accent-red)" }} />
        <h2 className="font-display font-bold text-xl">Kundenportal</h2>
      </div>

      {!sitzung && (
        <form onSubmit={oeffnen} className="mt-2">
          <p className="text-[14px] text-zinc-400 mb-3">
            Sie haben von {firma.firma} einen 6-stelligen Code erhalten? Hier geben Sie ihn ein, sehen Ihren Kaufvertrag und
            unterschreiben ihn digital — am Handy oder am Computer.
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, CODE_LAENGE))}
                   inputMode="text" autoCapitalize="characters" autoComplete="one-time-code" spellCheck={false}
                   placeholder="Code, z. B. K7M3XP" aria-label="Code"
                   className="apple-input flex-1 text-center font-display font-black text-2xl tracking-[0.35em]" data-testid="portal-code" />
            <button type="submit" disabled={busy || code.length !== CODE_LAENGE} className="apple-btn !rounded-full !px-6 !py-3 text-sm disabled:opacity-50" data-testid="portal-oeffnen">
              {busy ? "Prüfe…" : "Vertrag öffnen"}
            </button>
          </div>
          {fehler && <div className="text-sm mt-2" style={{ color: "var(--tx-rot, #ff6b6b)" }} data-testid="portal-fehler">{fehler}</div>}
        </form>
      )}

      {sitzung && fertig && (
        <div className="mt-3 rounded-2xl p-4" style={{ background: "rgba(48,209,88,0.10)", border: "1px solid rgba(48,209,88,0.35)" }} data-testid="portal-fertig">
          <div className="flex items-center gap-2 font-semibold"><CheckCircle2 size={18} style={{ color: "#30d158" }} /> Vielen Dank — der Kaufvertrag ist unterschrieben.</div>
          <div className="text-[13px] text-zinc-400 mt-1">Unterschrieben am {datum(fertig.unterschrieben_am)}. {firma.firma} wurde benachrichtigt.</div>
          <button type="button" onClick={pdfOeffnen} disabled={!pdfBlob} className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2 mt-3 disabled:opacity-50" data-testid="portal-pdf-fertig">
            <ExternalLink size={14} /> Unterschriebenen Vertrag herunterladen
          </button>
        </div>
      )}

      {sitzung && v && (
        <div className="mt-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[13px]" data-testid="portal-vertrag">
            <Feld label="Vertrag" wert={v.contract_no} />
            <Feld label="Fahrzeug" wert={`${v.marke || ""} ${v.modell || ""}`.trim()} />
            <Feld label="Kaufpreis" wert={eur(v.kaufpreis)} />
            <Feld label="Verkäufer" wert={v.verkaeufer} />
          </div>
          <div className="mt-4 flex items-center justify-between gap-2">
            <div className="text-[13px] text-zinc-400">
              {seitenStand === "laedt" && "Vertrag wird geladen…"}
              {seitenStand === "fertig" && (fertig ? "Ihr unterschriebener Vertrag:" : "Bitte lesen Sie den Vertrag vollständig:")}
              {seitenStand === "fehlgeschlagen" && "Die Vorschau ist hier nicht möglich — bitte das PDF öffnen."}
            </div>
            <button type="button" onClick={pdfOeffnen} disabled={!pdfBlob} className="text-[12.5px] underline inline-flex items-center gap-1 disabled:opacity-50" data-testid="portal-pdf-oeffnen">
              <ExternalLink size={12} /> PDF öffnen
            </button>
          </div>
          <div ref={seitenRef} className="mt-2 space-y-2 rounded-xl overflow-hidden" data-testid="portal-seiten" style={{ background: "#e5e5e5", padding: 6 }} />
        </div>
      )}

      {sitzung && v && !fertig && (
        <div className="mt-5" data-testid="portal-unterschrift">
          <h3 className="font-display font-bold text-lg mb-2">Unterschreiben</h3>
          <label className="text-[12px] font-semibold text-zinc-400">Ihr Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} className="apple-input w-full mt-1 mb-3" autoComplete="name" data-testid="portal-name" />
          <SignaturePad label="Unterschrift (mit Finger oder Maus)" onChange={setUnterschrift} height={180} />
          <label className="flex items-start gap-2 mt-3 text-[13.5px] cursor-pointer">
            <input type="checkbox" checked={einverstanden} onChange={(e) => setEinverstanden(e.target.checked)} className="mt-1" data-testid="portal-einverstanden" />
            <span>Ich habe den Kaufvertrag gelesen und stimme seinem Inhalt zu. Meine Unterschrift wird mit Datum, Uhrzeit und Geräteadresse im Vertrag festgehalten.</span>
          </label>
          {fehler && <div className="text-sm mt-2" style={{ color: "var(--tx-rot, #ff6b6b)" }} data-testid="portal-fehler">{fehler}</div>}
          <button type="button" onClick={absenden} disabled={busy} className="apple-btn !rounded-full !px-6 !py-3 text-sm mt-4 w-full sm:w-auto disabled:opacity-50" data-testid="portal-absenden">
            {busy ? "Sende…" : "Unterschreiben und absenden"}
          </button>
        </div>
      )}
    </section>
  );
}

function Feld({ label, wert }) {
  return (
    <div className="rounded-xl px-3 py-2" style={{ background: "var(--wa-06, rgba(255,255,255,0.06))" }}>
      <div className="text-[10.5px] uppercase tracking-wider text-zinc-500">{label}</div>
      <div className="font-semibold truncate">{wert || "—"}</div>
    </div>
  );
}
