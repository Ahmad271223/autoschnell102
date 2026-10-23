/**
 * Firmenstempel mit Unterschrift erstellen (Wunsch Ahmad 02.10.2026, nach seiner Vorlage): Firmendaten
 * eintragen, eines von sechs Designs wählen, im Feld unterschreiben — die Unterschrift liegt über dem Stempel.
 * „Übernehmen“ hinterlegt das Bild als Unterschrift der Firma (Kasten „Käufer“ im Kaufvertrag, Kundenportal).
 * Nur Stempel und Unterschrift, keine Rechnung.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, Stamp } from "lucide-react";
import { toast } from "sonner";
import SignaturePad from "@/components/SignaturePad";
import { VARIANTEN, VARIANTE_STANDARD, stempelErzeugen, unterschriftZuschneiden } from "@/lib/stempel";

const FELDER = [
  ["name", "Firmenname", "Mustermann Autohandel"],
  ["zusatz", "Zusatz (z. B. Inhaber)", "Inh. Max Mustermann"],
  ["strasse", "Straße und Hausnummer", "Musterstraße 1"],
  ["ort", "PLZ und Ort", "12345 Musterstadt"],
  ["tel", "Telefon", ""],
  ["mail", "E-Mail", ""],
  ["ust", "Steuernummer / USt-IdNr.", ""],
];

export function datenAusFirma(firma) {
  const f = firma || {};
  return {
    name: f.name || "", zusatz: f.zusatz || "", strasse: f.strasse || "",
    ort: [f.plz, f.ort].filter(Boolean).join(" "), tel: f.tel || "", mail: f.mail || "", ust: f.ust || "",
  };
}

export default function StempelUnterschrift({ firma, onUebernehmen, busy = false }) {
  const [daten, setDaten] = useState(() => datenAusFirma(firma));
  const [variante, setVariante] = useState(VARIANTE_STANDARD);
  const [gleich, setGleich] = useState(false);
  const [schraeg, setSchraeg] = useState(false);
  const [abnutzung, setAbnutzung] = useState(false);
  const [sigAn, setSigAn] = useState(true);
  const [sigGr, setSigGr] = useState(55);
  const [sigX, setSigX] = useState(50);
  const [sigY, setSigY] = useState(65);
  const [unterschrift, setUnterschrift] = useState(null);     // {img, w, h, dataUrl}
  const [fehler, setFehler] = useState("");
  const vorschau = useRef(null);
  const thumbs = useRef({});

  const einst = useMemo(() => ({ an: sigAn, groesse: sigGr, x: sigX, y: sigY }), [sigAn, sigGr, sigX, sigY]);

  // Unterschrift aus dem Feld: zuschneiden, Hintergrund durchsichtig
  const unterschriftGezeichnet = useCallback(async (dataUrl) => {
    if (!dataUrl) { setUnterschrift(null); return; }
    try {
      setUnterschrift(await unterschriftZuschneiden(dataUrl));
    } catch {
      setUnterschrift(null);
    }
  }, []);

  // Alles neu zeichnen, gebündelt je Bildschirm-Frame
  useEffect(() => {
    let frame = 0;
    const zeichnen = () => {
      try {
        VARIANTEN.forEach((v) => {
          const t = thumbs.current[v.id];
          if (!t) return;
          const c = stempelErzeugen({ daten, variante: v.id, gleich, schraeg, abnutzung: false, unterschrift, einst });
          t.width = 360; t.height = Math.round(360 * c.height / c.width);
          const tx = t.getContext("2d");
          tx.fillStyle = "#FFFFFF"; tx.fillRect(0, 0, t.width, t.height);
          tx.drawImage(c, 0, 0, t.width, t.height);
        });
        const c = stempelErzeugen({ daten, variante, gleich, schraeg, abnutzung, unterschrift, einst });
        const ziel = vorschau.current;
        if (ziel) {
          ziel.width = c.width; ziel.height = c.height;
          ziel.getContext("2d").drawImage(c, 0, 0);
        }
        setFehler("");
      } catch (e) {
        setFehler(e?.message || "Vorschau konnte nicht gezeichnet werden");
      }
    };
    if (typeof requestAnimationFrame === "function") frame = requestAnimationFrame(zeichnen);
    else zeichnen();
    return () => { if (frame && typeof cancelAnimationFrame === "function") cancelAnimationFrame(frame); };
  }, [daten, variante, gleich, schraeg, abnutzung, unterschrift, einst]);

  const pngDataUrl = () => {
    const c = stempelErzeugen({ daten, variante, gleich, schraeg, abnutzung, unterschrift, einst });
    return c.toDataURL("image/png");
  };
  const uebernehmen = async () => {
    if (!unterschrift) { toast.error("Bitte zuerst im Feld unterschreiben."); return; }
    try {
      await onUebernehmen?.(pngDataUrl());
    } catch (e) {
      toast.error(e?.message || "Stempel konnte nicht übernommen werden");
    }
  };
  const herunterladen = () => {
    const a = document.createElement("a");
    a.href = pngDataUrl();
    a.download = "firmenstempel.png";
    a.click();
  };
  const setzen = (k, v) => setDaten((d) => ({ ...d, [k]: v }));

  return (
    <div className="mt-3 rounded-2xl p-4 space-y-4" style={{ background: "var(--wa-03)", border: "1px solid var(--divider)" }}
         data-testid="stempel-unterschrift">
      <div className="flex items-center gap-2 font-semibold text-sm"><Stamp size={15} /> Stempel &amp; Unterschrift erstellen</div>
      <div className="text-[12px] text-zinc-500">
        Firmendaten prüfen, ein Design antippen, im Feld unterschreiben. Die Unterschrift liegt über dem Stempel.
        „Übernehmen“ hinterlegt das Ergebnis als Unterschrift der Firma.
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-4">
        <div className="space-y-2">
          {FELDER.map(([k, label, platzhalter]) => (
            <label key={k} className="block text-[12px] text-zinc-500">
              {label}
              <input value={daten[k]} onChange={(e) => setzen(k, e.target.value)} placeholder={platzhalter} maxLength={120}
                     className="input-base w-full mt-0.5" data-testid={`stempel-feld-${k}`} />
            </label>
          ))}
          <div className="space-y-1 pt-1 text-[12.5px]">
            <label className="flex items-center gap-2"><input type="checkbox" checked={gleich} onChange={(e) => setGleich(e.target.checked)} data-testid="stempel-gleich" /> Alle Schrift gleich groß</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={schraeg} onChange={(e) => setSchraeg(e.target.checked)} data-testid="stempel-schraeg" /> Leicht schräg (wie von Hand gestempelt)</label>
            <label className="flex items-center gap-2"><input type="checkbox" checked={abnutzung} onChange={(e) => setAbnutzung(e.target.checked)} data-testid="stempel-abnutzung" /> Stempel-Abnutzung (Farbstruktur)</label>
          </div>
        </div>

        <div className="space-y-3 min-w-0">
          <div>
            <div className="text-[12px] text-zinc-500 mb-1">Design</div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
              {VARIANTEN.map((v) => (
                <button key={v.id} type="button" onClick={() => setVariante(v.id)} aria-pressed={variante === v.id}
                        data-testid={`stempel-design-${v.id}`}
                        className="rounded-xl p-1.5 text-center text-[12px]"
                        style={{ background: "#fff", border: `${variante === v.id ? 3 : 1}px solid ${variante === v.id ? "var(--accent-red)" : "var(--divider)"}`, color: "#111" }}>
                  <canvas ref={(el) => { thumbs.current[v.id] = el; }} className="w-full block" style={{ height: 80, objectFit: "contain" }} />
                  <span className="block mt-1">{v.name}</span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="text-[12px] text-zinc-500 mb-1">Dein Stempel</div>
            <div className="rounded-xl p-3 text-center" style={{ background: "#fff", border: "1px dashed var(--divider)" }}>
              <canvas ref={vorschau} className="max-w-full h-auto inline-block" style={{ maxHeight: 360 }} data-testid="stempel-vorschau" />
            </div>
            {fehler && <div className="text-[12px] mt-1" style={{ color: "var(--st-rot)" }} data-testid="stempel-fehler">{fehler}</div>}
          </div>

          <div>
            <div className="text-[12px] text-zinc-500 mb-1">Unterschrift (mit Finger, Stift oder Maus)</div>
            <SignaturePad label="" onChange={unterschriftGezeichnet} height={150} />
            <div className="mt-2 space-y-1 text-[12.5px]">
              <label className="flex items-center gap-2"><input type="checkbox" checked={sigAn} onChange={(e) => setSigAn(e.target.checked)} data-testid="stempel-sig-an" /> Unterschrift auf den Stempel setzen</label>
              <div className="grid grid-cols-[90px_1fr] gap-x-3 gap-y-1 items-center text-zinc-500">
                <span>Größe</span><input type="range" min={20} max={95} value={sigGr} onChange={(e) => setSigGr(Number(e.target.value))} data-testid="stempel-sig-groesse" />
                <span>Horizontal</span><input type="range" min={0} max={100} value={sigX} onChange={(e) => setSigX(Number(e.target.value))} data-testid="stempel-sig-x" />
                <span>Vertikal</span><input type="range" min={0} max={100} value={sigY} onChange={(e) => setSigY(Number(e.target.value))} data-testid="stempel-sig-y" />
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={uebernehmen} disabled={busy || !unterschrift} className="apple-btn apple-btn-primary disabled:opacity-60"
                    data-testid="stempel-uebernehmen">
              <Stamp size={14} /> {busy ? "Speichere…" : "Als Unterschrift der Firma übernehmen"}
            </button>
            <button type="button" onClick={herunterladen} className="apple-btn apple-btn-secondary" data-testid="stempel-herunterladen">
              <Download size={14} /> PNG herunterladen
            </button>
            {!unterschrift && <span className="text-[12px] text-zinc-500" data-testid="stempel-hinweis">Erst unterschreiben, dann übernehmen.</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
