import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCheck, ChevronDown, ChevronUp, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { ART_TEXT, appLink, datumText, gelesenGemeldet } from "@/lib/neuigkeiten";

/**
 * „Was ist neu“ (Wunsch Ahmad 24.11.2026) — für Chef und Sucher. Die Einträge kommen vom
 * Server (backend/neuigkeiten.json), gelesen wird je Konto. Details lassen sich auf- und
 * zuklappen; ungelesene Einträge haben einen roten Rand.
 */
const ART_FARBE = { neu: "var(--st-gruen)", verbessert: "var(--st-blau)", behoben: "var(--st-amber)" };

export function ArtSchild({ art }) {
  const farbe = ART_FARBE[art] || "var(--st-grau)";
  return (
    <span className="text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded-md"
          style={{ color: farbe, background: `color-mix(in srgb, ${farbe} 14%, transparent)` }}>
      {ART_TEXT[art] || art}
    </span>
  );
}

function Eintrag({ e, onGelesen }) {
  const [offen, setOffen] = useState(!e.gelesen);
  const link = appLink(e.link);
  const hatDetails = (e.punkte && e.punkte.length > 0) || link;
  return (
    <div data-testid={`neuigkeit-${e.id}`} className="apple-surface p-4"
         style={{ borderLeft: e.gelesen ? "3px solid transparent" : "3px solid var(--accent-red)" }}>
      <div className="flex flex-wrap items-center gap-2 text-[12px]" style={{ color: "var(--text-dim)" }}>
        <ArtSchild art={e.art} />
        {e.bereich && <span>{e.bereich}</span>}
        <span>· {datumText(e.datum)}</span>
        {e.wichtig && <span className="font-semibold" style={{ color: "var(--st-rot)" }}>· wichtig</span>}
        {!e.gelesen && <span className="sr-only">ungelesen</span>}
      </div>
      <h2 className="mt-1 text-[15px] font-semibold leading-snug" style={{ color: "var(--text-strong)" }}>{e.titel}</h2>
      <p className="mt-1 text-[14px] leading-relaxed" style={{ color: "var(--text-primary)" }}>{e.text}</p>
      {hatDetails && offen && (
        <div className="mt-2" data-testid={`neuigkeit-details-${e.id}`}>
          {e.punkte?.length > 0 && (
            <ul className="list-disc pl-5 text-[13px] space-y-0.5" style={{ color: "var(--text-primary)" }}>
              {e.punkte.map((p, i) => <li key={i}>{p}</li>)}
            </ul>
          )}
          {link && (
            <Link to={link} onClick={() => onGelesen(e)} className="inline-block mt-2 text-[13px] font-semibold hover:underline"
                  style={{ color: "var(--tx-blau)" }} data-testid={`neuigkeit-link-${e.id}`}>
              Ansehen →
            </Link>
          )}
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {hatDetails && (
          <button type="button" onClick={() => setOffen((o) => !o)} aria-expanded={offen}
                  className="inline-flex items-center gap-1 text-[12px] min-h-[40px] px-2 -ml-2"
                  style={{ color: "var(--text-dim)" }} data-testid={`neuigkeit-umschalten-${e.id}`}>
            {offen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            {offen ? "Weniger" : "Details"}
          </button>
        )}
        {!e.gelesen && (
          <button type="button" onClick={() => onGelesen(e)}
                  className="ml-auto inline-flex items-center gap-1.5 text-[12px] font-semibold px-3 min-h-[40px] rounded-full"
                  style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" }}
                  data-testid={`neuigkeit-gelesen-${e.id}`}>
            <CheckCheck size={14} /> Als gelesen markieren
          </button>
        )}
      </div>
    </div>
  );
}

export default function Neuigkeiten() {
  const [liste, setListe] = useState(null);
  const [fehler, setFehler] = useState("");
  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/neuigkeiten");
      setListe(Array.isArray(data?.eintraege) ? data.eintraege : []);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Neuigkeiten konnten nicht geladen werden"));
      setListe([]);
    }
  }, []);
  useEffect(() => { laden(); }, [laden]);

  const gelesen = async (e) => {
    if (e.gelesen) return;
    try {
      const { data } = await api.post(`/neuigkeiten/${encodeURIComponent(e.id)}/gelesen`);
      setListe((l) => (l || []).map((x) => (x.id === e.id ? { ...x, gelesen: true } : x)));
      gelesenGemeldet(data);
    } catch (err) { toast.error(errMsg(err, "Konnte nicht als gelesen markieren")); }
  };
  const alleGelesen = async () => {
    try {
      const { data } = await api.post("/neuigkeiten/alle-gelesen");
      setListe((l) => (l || []).map((x) => ({ ...x, gelesen: true })));
      gelesenGemeldet(data);
    } catch (err) { toast.error(errMsg(err, "Konnte nicht als gelesen markieren")); }
  };
  const ungelesen = (liste || []).filter((x) => !x.gelesen).length;

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-4xl mx-auto">
      <div className="flex flex-wrap items-start justify-between gap-3 mb-5">
        <div className="min-w-0">
          <h1 className="font-display font-black text-2xl tracking-tight">Was ist neu</h1>
          <p className="text-sm mt-1" style={{ color: "var(--text-dim)" }}>
            Neue Funktionen und Verbesserungen in der App — das Neueste steht oben.
          </p>
        </div>
        {ungelesen > 0 && (
          <button onClick={alleGelesen} data-testid="neuigkeiten-alle-gelesen"
                  className="apple-btn apple-btn-secondary !rounded-full !px-4 !py-2 text-sm inline-flex items-center gap-2">
            <CheckCheck size={15} /> Alle als gelesen
          </button>
        )}
      </div>
      {fehler && <div className="text-sm mb-3" style={{ color: "var(--st-rot)" }} data-testid="neuigkeiten-fehler">{fehler}</div>}
      {liste === null && <div className="text-sm" style={{ color: "var(--text-dim)" }}>Lädt…</div>}
      {liste && liste.length === 0 && !fehler && (
        <div className="apple-surface p-8 text-center text-sm" style={{ color: "var(--text-dim)" }} data-testid="neuigkeiten-leer">
          <Sparkles size={22} className="mx-auto mb-2" />
          Gerade gibt es nichts Neues.
        </div>
      )}
      <div className="space-y-2">
        {(liste || []).map((e) => <Eintrag key={e.id} e={e} onGelesen={gelesen} />)}
      </div>
    </div>
  );
}
