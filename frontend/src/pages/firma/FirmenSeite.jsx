import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Building2, CheckCircle2, ExternalLink, Facebook, Instagram, Loader2, Mail, MapPin, Phone, Clock } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { blobOeffnen } from "@/lib/dateiOeffnen";
import SignaturePad from "@/components/SignaturePad";

/**
 * Öffentliche Firmenseite (Wunsch Ahmad 29.09.2026, Vorlage „Norden Autoankauf“): weiße Kopfzeile mit Logo
 * und Menü (Startseite · Kundenportal · Kontakt · Datenschutz), großes Titelbild mit Überschrift und Unterzeile,
 * darüber gelegt die weiße Karte „Kundenportal“ (Code eingeben → Vertrag öffnen), darunter „Über uns“ und
 * Bilder, unten die dunkle Fußzeile mit Kontakt, Impressum, Datenschutz und „Follow Us“.
 *
 * Der Kunde gibt den 6-stelligen Code aus der App ein, sieht seinen Kaufvertrag direkt auf der Seite (Handy oder
 * Laptop), unterschreibt mit Finger oder Maus und sendet ab — Sucher und Chef bekommen die Bestätigung in der App.
 *
 * Eigenes, helles Erscheinungsbild — unabhängig von der Hell/Dunkel-Einstellung der App (Farben unten fest).
 * Erreichbar über die Adresse der Firma (kfz-mueller.auto-schnellkauf.de, eigene Domain) oder /firma/<slug>.
 */
const BACKEND = process.env.REACT_APP_BACKEND_URL || "";
const CODE_LAENGE = 6;
const F = {
  navy: "#0f2b4c", navyTief: "#0b1f3a", rot: "#d62828", rotDunkel: "#b91c1c", text: "#122033",
  textGrau: "#5b6b80", rahmen: "#d5dce6", hintergrund: "#f4f6fa", weiss: "#ffffff", fussText: "#c9d3e2",
};

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

const eingabe = {
  background: F.weiss, border: `1px solid ${F.rahmen}`, color: F.text, borderRadius: 12, padding: "12px 14px",
  fontSize: 15, outline: "none", width: "100%",
};

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
    return <div className="min-h-screen flex items-center justify-center" style={{ background: F.hintergrund, color: F.navy }}><Loader2 className="animate-spin" /></div>;
  }
  if (!firma) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6 text-center" style={{ background: F.hintergrund, color: F.text }} data-testid="firmenseite-fehler">
        <div>
          <Building2 size={28} className="mx-auto mb-3" style={{ color: F.textGrau }} />
          <div className="font-display font-bold text-xl">{fehler || "Firmenseite nicht gefunden"}</div>
        </div>
      </div>
    );
  }
  const k = firma.kontakt || {};
  const social = firma.social || {};
  const plattform = firma.plattform_url || "";
  const datenschutzUrl = `${plattform}/datenschutz`;
  const bilderRest = (firma.bilder || []).slice(1);
  return (
    <div className="min-h-screen" style={{ background: F.hintergrund, color: F.text }} data-testid="firmenseite">
      {/* Kopfzeile */}
      <header className="sticky top-0 z-20" style={{ background: F.weiss, borderBottom: `1px solid ${F.rahmen}` }} data-testid="firmenseite-kopf">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between gap-4">
          <a href="#start" className="flex items-center gap-3 min-w-0">
            <div className="w-11 h-11 rounded-lg overflow-hidden flex items-center justify-center shrink-0" style={{ background: F.weiss, border: `1px solid ${F.rahmen}` }}>
              {firma.logo_url ? <img src={bildSrc(firma.logo_url)} alt={`Logo ${firma.firma}`} className="w-full h-full object-contain p-0.5" data-testid="firmenseite-logo" />
                : <Building2 size={22} style={{ color: F.navy }} />}
            </div>
            <span className="font-display font-black text-lg sm:text-xl truncate" style={{ color: F.navy }} data-testid="firmenseite-name">{firma.firma}</span>
          </a>
          <nav className="hidden sm:flex items-center gap-6 text-[14px] font-semibold" style={{ color: F.navy }} aria-label="Seitenmenü">
            <a href="#start" className="hover:underline">Startseite</a>
            <a href="#kundenportal" className="hover:underline" data-testid="nav-kundenportal">Kundenportal</a>
            <a href="#kontakt" className="hover:underline">Kontakt</a>
            <a href={datenschutzUrl} className="hover:underline">Datenschutz</a>
          </nav>
        </div>
      </header>

      {/* Titelbild mit Überschrift */}
      <section id="start" className="relative overflow-hidden" style={{ background: F.navy }} data-testid="firmenseite-titel">
        {firma.titelbild && <img src={bildSrc(firma.titelbild)} alt="" className="absolute inset-0 w-full h-full object-cover" />}
        <div className="absolute inset-0" style={{ background: firma.titelbild
          ? "linear-gradient(90deg, rgba(10,30,58,0.88) 0%, rgba(10,30,58,0.6) 50%, rgba(10,30,58,0.3) 100%)"
          : "linear-gradient(135deg, #0f2b4c 0%, #163a63 60%, #1f4f86 100%)" }} />
        <div className="relative max-w-6xl mx-auto px-4 sm:px-6 pt-14 pb-36 sm:pt-24 sm:pb-44">
          <h1 className="font-display font-black text-white text-3xl sm:text-5xl leading-tight max-w-2xl" data-testid="firmenseite-ueberschrift">{firma.titel}</h1>
          <p className="text-lg sm:text-2xl mt-3" style={{ color: "rgba(255,255,255,0.88)" }} data-testid="firmenseite-unterzeile">{firma.untertitel}</p>
        </div>
      </section>

      {/* Kundenportal-Karte, über das Titelbild gelegt */}
      <section id="kundenportal" className="relative -mt-24 sm:-mt-28 px-4 sm:px-6">
        <div className="max-w-3xl mx-auto rounded-2xl" style={{ background: F.weiss, boxShadow: "0 18px 50px rgba(11,31,58,0.25)" }}>
          <Kundenportal firma={firma} slug={slug} host={host} />
        </div>
      </section>

      {/* Über uns + Bilder */}
      {(firma.ueber_uns || bilderRest.length > 0) && (
        <section className="max-w-6xl mx-auto px-4 sm:px-6 pt-12 pb-4">
          {firma.ueber_uns && (
            <div data-testid="firmenseite-ueber-uns">
              <h2 className="font-display font-bold text-2xl mb-2" style={{ color: F.navy }}>Über uns</h2>
              <p className="text-[15.5px] leading-relaxed whitespace-pre-line max-w-3xl" style={{ color: F.text }}>{firma.ueber_uns}</p>
            </div>
          )}
          {bilderRest.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-6" data-testid="firmenseite-bilder">
              {bilderRest.map((b) => (
                <div key={b} className="aspect-[4/3] rounded-xl overflow-hidden" style={{ background: F.rahmen }}>
                  <img src={bildSrc(b)} alt="" loading="lazy" className="w-full h-full object-cover" />
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Fußzeile */}
      <footer id="kontakt" className="mt-12" style={{ background: F.navyTief, color: F.fussText }} data-testid="firmenseite-kontakt">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 py-10 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8">
          <div>
            <div className="font-display font-bold text-lg text-white mb-3">Kontakt</div>
            <div className="space-y-2 text-[14px]">
              {(k.adresse || k.plz || k.ort) && <div className="flex items-start gap-2"><MapPin size={15} className="mt-0.5 shrink-0" /><span>{k.adresse}{k.adresse && (k.plz || k.ort) ? ", " : ""}{[k.plz, k.ort].filter(Boolean).join(" ")}</span></div>}
              {k.telefon && <div className="flex items-center gap-2"><Phone size={15} className="shrink-0" /><a href={`tel:${k.telefon.replace(/\s+/g, "")}`} className="underline">{k.telefon}</a></div>}
              {k.email && <div className="flex items-center gap-2"><Mail size={15} className="shrink-0" /><a href={`mailto:${k.email}`} className="underline break-all">{k.email}</a></div>}
              {k.oeffnungszeiten && <div className="flex items-start gap-2"><Clock size={15} className="mt-0.5 shrink-0" /><span className="whitespace-pre-line">{k.oeffnungszeiten}</span></div>}
            </div>
          </div>
          <div>
            <div className="font-display font-bold text-lg text-white mb-3">Impressum</div>
            <div className="text-[14px] space-y-1">
              <div className="text-white font-semibold">{firma.firma}</div>
              {(k.adresse || k.plz || k.ort) && <div>{k.adresse}{k.adresse ? ", " : ""}{[k.plz, k.ort].filter(Boolean).join(" ")}</div>}
              {k.telefon && <div>Telefon: {k.telefon}</div>}
              {k.email && <div>E-Mail: {k.email}</div>}
              <div className="pt-2 text-[12.5px]" style={{ color: "#9fb0c8" }}>
                Verantwortlich für die Inhalte dieser Seite ist {firma.firma}. Technisch bereitgestellt über AutoSchnell
                {plattform && <> (<a href={`${plattform}/impressum`} className="underline">Impressum der Plattform</a>)</>}.
              </div>
            </div>
          </div>
          <div>
            <div className="font-display font-bold text-lg text-white mb-3">Datenschutz</div>
            <div className="text-[14px] space-y-2">
              <div>Ihre Angaben und Ihre Unterschrift werden nur für Ihren Kaufvertrag verwendet und verschlüsselt übertragen.</div>
              <a href={datenschutzUrl} className="underline inline-flex items-center gap-1" data-testid="firmenseite-datenschutz">Datenschutzerklärung <ExternalLink size={12} /></a>
            </div>
          </div>
          {(social.facebook || social.instagram) && (
            <div data-testid="firmenseite-social">
              <div className="font-display font-bold text-lg text-white mb-3">Follow Us</div>
              <div className="flex items-center gap-2">
                {social.facebook && <a href={social.facebook} target="_blank" rel="noreferrer" aria-label="Facebook" className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: "rgba(255,255,255,0.12)", color: "#fff" }}><Facebook size={18} /></a>}
                {social.instagram && <a href={social.instagram} target="_blank" rel="noreferrer" aria-label="Instagram" className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: "rgba(255,255,255,0.12)", color: "#fff" }}><Instagram size={18} /></a>}
              </div>
            </div>
          )}
        </div>
        <div className="text-center text-[12px] pb-6" style={{ color: "#7f91ab" }}>© {new Date().getFullYear()} {firma.firma}</div>
      </footer>
    </div>
  );
}

/** Die Karte "Kundenportal": Code → Vertrag lesen → unterschreiben → fertig. */
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
      // 429: der Server sagt, wie lange zu warten ist (2 Minuten bei zu vielen Anfragen, 10 bei falschen Codes)
      setFehler(errMsg(err, err?.response?.status === 429 ? "Zu viele Versuche — bitte später erneut." : "Code ungültig oder abgelaufen."));
    } finally { setBusy(false); }
  };

  // Vertrag laden und Seiten rendern, sobald die Sitzung steht.
  // Prüfliste 30.09.2026: die Sitzung reist in einer Kopfzeile — nie in der Adresse (Adressen landen in
  // Server- und Proxy-Protokollen).
  const pdfLaden = useCallback(async () => {
    if (!sitzung?.sitzung) return;
    setSeitenStand("laedt");
    try {
      const { data } = await api.get("/public/portal/vertrag/pdf",
        { responseType: "blob", headers: { "X-Portal-Sitzung": sitzung.sitzung } });
      setPdfBlob(data);
      const { pdfSeitenRendern } = await import("@/lib/pdfAnzeige");
      // mindestens 320 px (lesbar), hoechstens 820 px — ein noch nicht gezeichneter Kasten meldet sonst 0
      const breite = Math.min(820, Math.max(320, seitenRef.current?.clientWidth || 720));
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
    // Prüfliste 01.10.2026 (Nr. 2): ohne geladenes Vertragsdokument keine Unterschrift (der Server prüft das auch)
    if (!pdfBlob) { setFehler("Der Vertrag wurde noch nicht geladen — bitte „Vertrag erneut laden“ drücken."); return; }
    if (!unterschrift) { setFehler("Bitte im Feld unterschreiben."); return; }
    if (!einverstanden) { setFehler("Bitte bestätigen, dass Sie den Vertrag gelesen haben."); return; }
    if (name.trim().length < 2) { setFehler("Bitte Ihren Namen eintragen."); return; }
    setBusy(true); setFehler("");
    try {
      const { data } = await api.post("/public/portal/vertrag/unterschreiben",
        { signature_b64: unterschrift, name: name.trim(), einverstanden: true },
        { headers: { "X-Portal-Sitzung": sitzung.sitzung } });
      setFertig(data);
      try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { /* jsdom / alte Browser */ }
    } catch (err) {
      setFehler(errMsg(err, "Die Unterschrift konnte nicht gesendet werden — bitte erneut versuchen."));
    } finally { setBusy(false); }
  };

  const v = sitzung?.vertrag;
  const knopf = { background: F.rot, color: "#fff", borderRadius: 12, padding: "13px 26px", fontWeight: 700, fontSize: 15 };
  return (
    <section className="p-5 sm:p-8" data-testid="kundenportal">
      <h2 className="font-display font-black text-2xl sm:text-3xl text-center" style={{ color: F.navy }}>Kundenportal</h2>

      {!sitzung && (
        <form onSubmit={oeffnen} className="mt-3">
          <p className="text-[15px] text-center max-w-xl mx-auto" style={{ color: F.text }}>
            Geben Sie Ihren 6-stelligen Code ein, um Ihren Kaufvertrag sicher online einzusehen und zu unterschreiben.
          </p>
          <div className="flex flex-col sm:flex-row gap-3 mt-5 max-w-xl mx-auto">
            <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, CODE_LAENGE))}
                   inputMode="text" autoCapitalize="characters" autoComplete="one-time-code" spellCheck={false}
                   placeholder="Code eingeben" aria-label="Code"
                   className="flex-1 text-center font-display font-black text-xl tracking-[0.3em]" style={eingabe} data-testid="portal-code" />
            <button type="submit" disabled={busy || code.length !== CODE_LAENGE} className="disabled:opacity-50" style={knopf} data-testid="portal-oeffnen">
              {busy ? "Prüfe…" : "Vertrag öffnen"}
            </button>
          </div>
          {fehler && <div className="text-sm mt-3 text-center" style={{ color: F.rotDunkel }} data-testid="portal-fehler">{fehler}</div>}
          <p className="text-[13.5px] text-center mt-4" style={{ color: F.navy }}>Vertrag digital einsehen und unterschreiben – sicher und bequem.</p>
        </form>
      )}

      {sitzung && fertig && (
        <div className="mt-4 rounded-2xl p-4" style={{ background: "#e9f8ee", border: "1px solid #9ad8ac" }} data-testid="portal-fertig">
          <div className="flex items-center gap-2 font-semibold" style={{ color: "#146c2e" }}><CheckCircle2 size={18} /> Vielen Dank — der Kaufvertrag ist unterschrieben.</div>
          <div className="text-[13.5px] mt-1" style={{ color: F.text }}>Unterschrieben am {datum(fertig.unterschrieben_am)}. {firma.firma} wurde benachrichtigt.</div>
          <button type="button" onClick={pdfOeffnen} disabled={!pdfBlob} className="inline-flex items-center gap-2 mt-3 disabled:opacity-50"
                  style={{ ...knopf, padding: "10px 18px", fontSize: 14 }} data-testid="portal-pdf-fertig">
            <ExternalLink size={14} /> Unterschriebenen Vertrag herunterladen
          </button>
        </div>
      )}

      {sitzung && v && (
        <div className="mt-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[13px]" data-testid="portal-vertrag">
            <Feld label="Vertrag" wert={v.contract_no} />
            <Feld label="Fahrzeug" wert={`${v.marke || ""} ${v.modell || ""}`.trim()} />
            <Feld label="Kaufpreis" wert={eur(v.kaufpreis)} />
            <Feld label="Verkäufer" wert={v.verkaeufer} />
          </div>
          <div className="mt-4 flex items-center justify-between gap-2">
            <div className="text-[13.5px]" style={{ color: F.textGrau }}>
              {seitenStand === "laedt" && "Vertrag wird geladen…"}
              {seitenStand === "fertig" && (fertig ? "Ihr unterschriebener Vertrag:" : "Bitte lesen Sie den Vertrag vollständig:")}
              {seitenStand === "fehlgeschlagen" && pdfBlob && "Die Vorschau ist hier nicht möglich — bitte das PDF öffnen."}
              {seitenStand === "fehlgeschlagen" && !pdfBlob && (
                <span data-testid="portal-pdf-fehlt">Der Vertrag konnte nicht geladen werden.{" "}
                  <button type="button" onClick={pdfLaden} className="underline" style={{ color: F.navy }} data-testid="portal-pdf-erneut">Vertrag erneut laden</button>
                </span>
              )}
            </div>
            <button type="button" onClick={pdfOeffnen} disabled={!pdfBlob} className="text-[13px] underline inline-flex items-center gap-1 disabled:opacity-50" style={{ color: F.navy }} data-testid="portal-pdf-oeffnen">
              <ExternalLink size={12} /> PDF öffnen
            </button>
          </div>
          <div ref={seitenRef} className="mt-2 space-y-2 rounded-xl overflow-hidden" data-testid="portal-seiten" style={{ background: "#e5e8ee", padding: 6 }} />
        </div>
      )}

      {sitzung && v && !fertig && (
        <div className="mt-6" data-testid="portal-unterschrift">
          <h3 className="font-display font-bold text-xl mb-2" style={{ color: F.navy }}>Unterschreiben</h3>
          <label className="text-[12.5px] font-semibold" style={{ color: F.textGrau }}>Ihr Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} className="mt-1 mb-3" style={eingabe} autoComplete="name" data-testid="portal-name" />
          <div style={{ color: F.text }}>
            <SignaturePad label="Unterschrift (mit Finger oder Maus)" onChange={setUnterschrift} height={180} />
          </div>
          <label className="flex items-start gap-2 mt-3 text-[13.5px] cursor-pointer" style={{ color: F.text }}>
            <input type="checkbox" checked={einverstanden} onChange={(e) => setEinverstanden(e.target.checked)} className="mt-1" data-testid="portal-einverstanden" />
            <span>Ich habe den Kaufvertrag gelesen und stimme seinem Inhalt zu. Meine Unterschrift wird mit Datum, Uhrzeit und Geräteadresse im Vertrag festgehalten.</span>
          </label>
          {fehler && <div className="text-sm mt-2" style={{ color: F.rotDunkel }} data-testid="portal-fehler">{fehler}</div>}
          <button type="button" onClick={absenden} disabled={busy || !pdfBlob} className="mt-4 w-full sm:w-auto disabled:opacity-50" style={knopf} data-testid="portal-absenden">
            {busy ? "Sende…" : "Unterschreiben und absenden"}
          </button>
        </div>
      )}
    </section>
  );
}

function Feld({ label, wert }) {
  return (
    <div className="rounded-xl px-3 py-2" style={{ background: F.hintergrund, border: `1px solid ${F.rahmen}` }}>
      <div className="text-[10.5px] uppercase tracking-wider" style={{ color: F.textGrau }}>{label}</div>
      <div className="font-semibold truncate" style={{ color: F.text }}>{wert || "—"}</div>
    </div>
  );
}
