import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, ExternalLink, FilePenLine } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { blobOeffnen } from "@/lib/dateiOeffnen";
import SignaturePad from "@/components/SignaturePad";

/**
 * Abänderungsvertrag online unterschreiben (Wunsch Ahmad 24.11.2026).
 *
 * Der Verkäufer bekommt per E-Mail einen Link …/abaenderung#<schlüssel>. Der Schlüssel steht hinter dem „#“:
 * er geht nie an den Server-Protokollen vorbei, sondern reist nur in der Kopfzeile X-Portal-Sitzung.
 * Ablauf wie im Kundenportal: Änderungen ansehen, PDF lesen, Name + Unterschrift, absenden.
 * Eigenes, helles Erscheinungsbild wie die Firmenseite — unabhängig von der Hell/Dunkel-Einstellung der App.
 */
const F = {
  navy: "#0f2b4c", rot: "#d62828", rotDunkel: "#b91c1c", text: "#122033",
  textGrau: "#5b6b80", rahmen: "#d5dce6", hintergrund: "#f4f6fa", weiss: "#ffffff",
};
const eingabe = {
  background: F.weiss, border: `1px solid ${F.rahmen}`, color: F.text, borderRadius: 12, padding: "12px 14px",
  fontSize: 15, outline: "none", width: "100%",
};
const knopf = { background: F.rot, color: "#fff", borderRadius: 12, padding: "13px 26px", fontWeight: 700, fontSize: 15 };

/** Schlüssel aus der Adresse (#…), nur erlaubte Zeichen. */
export function linkSchluessel(hash) {
  const roh = String(hash || "").replace(/^#/, "").trim();
  return /^[A-Za-z0-9_-]{20,100}$/.test(roh) ? roh : "";
}

function datum(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString("de-DE", {
    day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function AbaenderungUnterschreiben() {
  const [schluessel] = useState(() => linkSchluessel(typeof window !== "undefined" ? window.location.hash : ""));
  const [info, setInfo] = useState(null);
  const [ladeFehler, setLadeFehler] = useState("");
  const [pdfBlob, setPdfBlob] = useState(null);
  const [seitenStand, setSeitenStand] = useState("");
  const [name, setName] = useState("");
  const [unterschrift, setUnterschrift] = useState("");
  const [einverstanden, setEinverstanden] = useState(false);
  const [fehler, setFehler] = useState("");
  const [busy, setBusy] = useState(false);
  const [fertig, setFertig] = useState(null);
  const seitenRef = useRef(null);
  const kopf = { headers: { "X-Portal-Sitzung": schluessel } };

  useEffect(() => {
    if (!schluessel) { setLadeFehler("Der Link ist unvollständig. Bitte den Link aus der E-Mail vollständig öffnen."); return; }
    api.get("/public/abaenderung", kopf)
      .then(({ data }) => { setInfo(data.abaenderung); if (data.abaenderung?.verkaeufer) setName(data.abaenderung.verkaeufer); })
      .catch((e) => setLadeFehler(errMsg(e, "Der Link ist ungültig oder abgelaufen.")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schluessel]);

  const pdfLaden = useCallback(async () => {
    if (!schluessel) return;
    setSeitenStand("laedt");
    try {
      const { data } = await api.get("/public/abaenderung/pdf", { responseType: "blob", ...kopf });
      setPdfBlob(data);
      const { pdfSeitenRendern } = await import("@/lib/pdfAnzeige");
      const breite = Math.min(820, Math.max(320, seitenRef.current?.clientWidth || 720));
      const seiten = await pdfSeitenRendern(data, breite);
      if (seitenRef.current) seitenRef.current.replaceChildren(...seiten);
      setSeitenStand(seiten.length ? "fertig" : "fehlgeschlagen");
    } catch {
      setSeitenStand("fehlgeschlagen");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schluessel]);
  useEffect(() => { if (info) pdfLaden(); }, [info, pdfLaden, fertig?.unterschrieben_am]);

  const absenden = async () => {
    if (!pdfBlob) { setFehler("Das Dokument wurde noch nicht geladen — bitte „Erneut laden“ drücken."); return; }
    if (!unterschrift) { setFehler("Bitte im Feld unterschreiben."); return; }
    if (!einverstanden) { setFehler("Bitte bestätigen, dass Sie die Änderungen gelesen haben."); return; }
    if (name.trim().length < 2) { setFehler("Bitte Ihren Namen eintragen."); return; }
    setBusy(true); setFehler("");
    try {
      const { data } = await api.post("/public/abaenderung/unterschreiben",
        { signature_b64: unterschrift, name: name.trim(), einverstanden: true }, kopf);
      setFertig(data);
      try { window.scrollTo({ top: 0, behavior: "smooth" }); } catch { /* jsdom */ }
    } catch (err) {
      setFehler(errMsg(err, "Die Unterschrift konnte nicht gesendet werden — bitte erneut versuchen."));
    } finally { setBusy(false); }
  };

  const pdfOeffnen = () => {
    if (pdfBlob) blobOeffnen(pdfBlob, { titel: "Der Abänderungsvertrag", mime: "application/pdf",
      dateiname: `Abaenderungsvertrag-${info?.contract_no || ""}.pdf` });
  };

  const schonUnterschrieben = fertig || info?.status === "unterschrieben";
  return (
    <main className="min-h-screen px-4 py-6 sm:py-10" style={{ background: F.hintergrund, color: F.text }}
          data-testid="abaenderung-seite">
      <div className="max-w-3xl mx-auto rounded-2xl p-5 sm:p-8" style={{ background: F.weiss, border: `1px solid ${F.rahmen}` }}>
        <h1 className="font-display font-black text-2xl sm:text-3xl flex items-center gap-2" style={{ color: F.navy }}>
          <FilePenLine size={26} /> Abänderungsvertrag
        </h1>
        {info?.firma && <div className="text-[14px] mt-1" style={{ color: F.textGrau }}>{info.firma}</div>}

        {ladeFehler && <div className="mt-4 text-[15px]" style={{ color: F.rotDunkel }} data-testid="abaenderung-link-fehler">{ladeFehler}</div>}

        {info && (
          <>
            <p className="mt-4 text-[15px]">
              Zum Kaufvertrag {info.contract_no ? `Nr. ${info.contract_no} ` : ""}vom {info.vertrag_vom}
              {info.fahrzeug ? ` über ${info.fahrzeug}` : ""} haben sich diese Punkte geändert:
            </p>
            <ul className="mt-2 flex flex-col gap-1 text-[15px] rounded-xl p-3" data-testid="abaenderung-zeilen"
                style={{ background: F.hintergrund, border: `1px solid ${F.rahmen}` }}>
              {(info.zeilen || []).map((z, i) => <li key={i}>• {z}</li>)}
            </ul>
            <p className="mt-2 text-[13.5px]" style={{ color: F.textGrau }}>Alles andere im Kaufvertrag bleibt, wie es ist.</p>
          </>
        )}

        {schonUnterschrieben && (
          <div className="mt-4 rounded-2xl p-4" style={{ background: "#e9f8ee", border: "1px solid #9ad8ac" }} data-testid="abaenderung-fertig">
            <div className="flex items-center gap-2 font-semibold" style={{ color: "#146c2e" }}>
              <CheckCircle2 size={18} /> Vielen Dank — der Abänderungsvertrag ist unterschrieben.
            </div>
            {(fertig?.unterschrieben_am || info?.unterschrieben_am) && (
              <div className="text-[13.5px] mt-1">Unterschrieben am {datum(fertig?.unterschrieben_am || info?.unterschrieben_am)}.</div>
            )}
          </div>
        )}

        {info && (
          <div className="mt-5">
            <div className="flex items-center justify-between gap-2">
              <div className="text-[13.5px]" style={{ color: F.textGrau }}>
                {seitenStand === "laedt" && "Dokument wird geladen…"}
                {seitenStand === "fertig" && (schonUnterschrieben ? "Ihr unterschriebener Abänderungsvertrag:" : "Bitte lesen Sie das Dokument:")}
                {seitenStand === "fehlgeschlagen" && pdfBlob && "Die Vorschau ist hier nicht möglich — bitte das PDF öffnen."}
                {seitenStand === "fehlgeschlagen" && !pdfBlob && (
                  <span>Das Dokument konnte nicht geladen werden.{" "}
                    <button type="button" onClick={pdfLaden} className="underline" style={{ color: F.navy }} data-testid="abaenderung-pdf-erneut">Erneut laden</button>
                  </span>
                )}
              </div>
              <button type="button" onClick={pdfOeffnen} disabled={!pdfBlob} data-testid="abaenderung-pdf-oeffnen"
                      className="text-[13px] underline inline-flex items-center gap-1 disabled:opacity-50" style={{ color: F.navy }}>
                <ExternalLink size={12} /> PDF öffnen
              </button>
            </div>
            <div ref={seitenRef} className="mt-2 space-y-2 rounded-xl overflow-hidden" style={{ background: "#e5e8ee", padding: 6 }} />
          </div>
        )}

        {info && !schonUnterschrieben && (
          <div className="mt-6" data-testid="abaenderung-unterschrift">
            <h2 className="font-display font-bold text-xl mb-2" style={{ color: F.navy }}>Unterschreiben</h2>
            <label className="text-[12.5px] font-semibold" style={{ color: F.textGrau }} htmlFor="abaenderung-name">Ihr Name</label>
            <input id="abaenderung-name" value={name} onChange={(e) => setName(e.target.value)} className="mt-1 mb-3"
                   style={eingabe} autoComplete="name" data-testid="abaenderung-name" />
            <SignaturePad label="Unterschrift (mit Finger oder Maus)" onChange={setUnterschrift} height={180} />
            <label className="flex items-start gap-2 mt-3 text-[13.5px] cursor-pointer">
              <input type="checkbox" checked={einverstanden} onChange={(e) => setEinverstanden(e.target.checked)}
                     className="mt-1" data-testid="abaenderung-einverstanden" />
              <span>Ich habe die Änderungen gelesen und stimme ihnen zu. Meine Unterschrift wird mit Datum, Uhrzeit und Geräteadresse festgehalten.</span>
            </label>
            {fehler && <div className="text-sm mt-2" style={{ color: F.rotDunkel }} data-testid="abaenderung-fehler">{fehler}</div>}
            <button type="button" onClick={absenden} disabled={busy || !pdfBlob} className="mt-4 w-full sm:w-auto disabled:opacity-50"
                    style={knopf} data-testid="abaenderung-absenden">
              {busy ? "Sende…" : "Unterschreiben und absenden"}
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
