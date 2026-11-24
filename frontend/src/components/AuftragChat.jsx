/*
 * Chat Büro <-> Fahrer je Abholtermin (Wunsch Ahmad 24.11.2026).
 *
 * Eine Komponente für beide Seiten: rolle="buero" (Termin-Dialog, Fahrzeugakte, Meldungen) und
 * rolle="fahrer" (Fahrer-App: Fahrtkarte, Protokoll). Zugeklappt nur ein Knopf mit Zähler
 * („Chat · 2 neu“), aufgeklappt Verlauf, Schnellantworten mit einem Tipp, eigener Text und Foto.
 * Offen: alle 15 s nachladen (nur sichtbarer Tab); das Öffnen markiert die Nachrichten der
 * Gegenseite als gelesen. Rechte, Sichtfrist und Grenzen prüft der Server.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Camera, FileText, MessageCircle, Send, X } from "lucide-react";
import { toast } from "sonner";
import { errMsg, openAuthedFile } from "@/lib/api";
import { verkleinereBildDatei } from "@/lib/bilder";
import { chatClient, chatZaehlerNeuLaden, useChatZaehler } from "@/lib/chatZaehler";

export const CHAT_OFFEN_TAKT_MS = 15000;
export const TEXT_MAX = 1000;

export function chatPfad(rolle, terminId) {
  const id = encodeURIComponent(terminId);
  return rolle === "fahrer" ? `/driver/appointments/${id}/chat` : `/appointments/${id}/chat`;
}

/** "14:36" für heute, sonst "24.11. 14:36". */
export function zeitKurz(iso, jetzt = new Date()) {
  const t = new Date(iso || "");
  if (Number.isNaN(t.getTime())) return "";
  const uhr = t.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
  const heute = t.toDateString() === jetzt.toDateString();
  if (heute) return uhr;
  return `${t.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })} ${uhr}`;
}

/** Text unter der letzten eigenen Nachricht: "Gelesen 14:36" bzw. "Gesendet". */
export function gelesenText(nachrichten) {
  const eigene = (nachrichten || []).filter((m) => m.eigene);
  const letzte = eigene[eigene.length - 1];
  if (!letzte) return null;
  return { id: letzte.id, text: letzte.gelesen_am ? `Gelesen ${zeitKurz(letzte.gelesen_am)}` : "Gesendet" };
}

/** Zähler „2 neu“ (nichts bei 0) — für Karten und Listen. */
export function ChatNeu({ rolle, terminId, className = "" }) {
  const { termine } = useChatZaehler(rolle);
  const n = termine?.[terminId] || 0;
  if (!n) return null;
  return (
    <span data-testid={`chat-neu-${terminId}`}
          className={`inline-flex items-center gap-1 shrink-0 whitespace-nowrap text-[10px] font-bold px-2 py-0.5 rounded-full ${className}`}
          style={{ background: "var(--accent-red)", color: "#fff" }}
          title="Ungelesene Nachrichten im Chat">
      <MessageCircle size={10} /> {n} neu
    </span>
  );
}

function ChatFoto({ rolle, terminId, nachrichtId }) {
  const [url, setUrl] = useState("");
  const pfad = `${chatPfad(rolle, terminId)}/fotos/${encodeURIComponent(nachrichtId)}`;
  useEffect(() => {
    let weg = false;
    let objUrl = "";
    chatClient(rolle).get(pfad, { responseType: "blob" })
      .then((r) => {
        if (weg) return;
        objUrl = URL.createObjectURL(r.data);
        setUrl(objUrl);
      })
      .catch(() => { /* Platzhalter bleibt */ });
    return () => { weg = true; if (objUrl) URL.revokeObjectURL(objUrl); };
  }, [rolle, pfad]);
  const oeffnen = () => openAuthedFile(pfad, "image/jpeg", chatClient(rolle))
    .catch((e) => toast.error(errMsg(e, "Foto konnte nicht geöffnet werden")));
  return (
    <button type="button" onClick={oeffnen} className="block mt-1 rounded-lg overflow-hidden"
            style={{ background: "var(--wa-05)", minWidth: 80, minHeight: 60 }}
            aria-label="Foto groß öffnen" data-testid={`chat-foto-${nachrichtId}`}>
      {url
        ? <img src={url} alt="Foto im Chat" className="block max-h-48 max-w-[220px] object-cover" />
        : <span className="block px-3 py-4 text-[11px]" style={{ color: "var(--text-muted)" }}>Foto lädt …</span>}
    </button>
  );
}

export default function AuftragChat({ rolle = "buero", terminId, startOffen = false }) {
  const [offen, setOffen] = useState(!!startOffen);
  const [daten, setDaten] = useState(null);
  const [fehler, setFehler] = useState("");
  const [text, setText] = useState("");
  const [sendet, setSendet] = useState(false);
  const listeRef = useRef(null);
  const dateiRef = useRef(null);
  const anzahlRef = useRef(0);
  const { termine } = useChatZaehler(rolle);
  const neu = termine?.[terminId] || 0;
  const client = chatClient(rolle);
  const pfad = chatPfad(rolle, terminId);
  const gegenseite = rolle === "fahrer" ? "Büro" : "Fahrer";

  const laden = useCallback(async () => {
    try {
      const { data } = await client.get(pfad);
      setDaten(data);
      setFehler("");
      chatZaehlerNeuLaden(rolle);
    } catch (e) {
      setFehler(errMsg(e, "Chat konnte nicht geladen werden"));
    }
  }, [client, pfad, rolle]);

  useEffect(() => {
    if (!offen || !terminId) return undefined;
    laden();
    const t = setInterval(() => {
      if (typeof document === "undefined" || document.visibilityState === "visible") laden();
    }, CHAT_OFFEN_TAKT_MS);
    return () => clearInterval(t);
  }, [offen, terminId, laden]);

  // Neue Nachricht -> ans Ende scrollen
  useEffect(() => {
    const n = daten?.nachrichten?.length || 0;
    if (n !== anzahlRef.current && listeRef.current) {
      listeRef.current.scrollTop = listeRef.current.scrollHeight;
    }
    anzahlRef.current = n;
  }, [daten]);

  const senden = async ({ inhalt, schnell = false, foto = null }) => {
    if (sendet) return;
    const sauber = String(inhalt || "").trim();
    if (!sauber && !foto) return;
    if (sauber.length > TEXT_MAX) {
      toast.error(`Die Nachricht ist zu lang (höchstens ${TEXT_MAX} Zeichen).`);
      return;
    }
    setSendet(true);
    try {
      const { data } = await client.post(pfad, { text: sauber, schnell, ...(foto ? { foto_b64: foto } : {}) });
      setDaten((d) => ({ ...(d || {}), nachrichten: [...((d && d.nachrichten) || []), data] }));
      if (!schnell && !foto) setText("");
    } catch (e) {
      toast.error(errMsg(e, "Nachricht konnte nicht gesendet werden"));
    } finally {
      setSendet(false);
    }
  };

  const fotoGewaehlt = async (ev) => {
    const datei = ev.target.files?.[0];
    ev.target.value = "";
    if (!datei) return;
    try {
      const url = await verkleinereBildDatei(datei, { maxKante: 1600, qualitaet: 0.8 });
      await senden({ inhalt: text, foto: url });
      setText("");
    } catch (e) {
      toast.error(e?.message || "Foto konnte nicht gelesen werden");
    }
  };

  const pdfOeffnen = () => openAuthedFile(`${pfad}.pdf`, "application/pdf", client)
    .catch((e) => toast.error(errMsg(e, "PDF konnte nicht geladen werden")));

  if (!terminId) return null;

  if (!offen) {
    return (
      <button type="button" onClick={() => setOffen(true)} data-testid={`chat-oeffnen-${terminId}`}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold border"
              style={{ borderColor: "var(--border-default)", background: "var(--wa-02)", color: "var(--text-primary)" }}>
        <MessageCircle size={15} /> Chat mit {gegenseite}
        {neu > 0 && (
          <span className="text-[11px] font-bold px-2 py-0.5 rounded-full"
                style={{ background: "var(--accent-red)", color: "#fff" }}
                data-testid={`chat-zaehler-${terminId}`}>
            {neu} neu
          </span>
        )}
      </button>
    );
  }

  const nachrichten = daten?.nachrichten || [];
  const haken = gelesenText(nachrichten);
  const kannSchreiben = daten ? daten.kann_schreiben !== false : false;
  const schnellantworten = daten?.schnellantworten || [];

  return (
    <div className="rounded-xl border overflow-hidden" data-testid={`chat-${terminId}`}
         style={{ borderColor: "var(--border-default)", background: "var(--wa-02)" }}>
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: "var(--border-default)" }}>
        <MessageCircle size={14} style={{ color: "var(--text-secondary)" }} />
        <div className="text-xs font-bold uppercase tracking-wider flex-1 min-w-0 truncate"
             style={{ color: "var(--text-primary)" }}>
          Chat mit {gegenseite}{rolle === "buero" && daten?.fahrer?.name ? ` · ${daten.fahrer.name}` : ""}
        </div>
        {rolle === "buero" && nachrichten.length > 0 && (
          <button type="button" onClick={pdfOeffnen} title="Verlauf als PDF" aria-label="Verlauf als PDF"
                  data-testid={`chat-pdf-${terminId}`}
                  className="p-1.5 rounded-md" style={{ color: "var(--text-secondary)" }}>
            <FileText size={14} />
          </button>
        )}
        <button type="button" onClick={() => setOffen(false)} aria-label="Chat schließen"
                className="p-1.5 rounded-md" style={{ color: "var(--text-secondary)" }}
                data-testid={`chat-schliessen-${terminId}`}>
          <X size={14} />
        </button>
      </div>

      <div ref={listeRef} className="px-3 py-3 space-y-2 overflow-y-auto" style={{ maxHeight: 320 }}
           data-testid={`chat-verlauf-${terminId}`} aria-live="polite">
        {!daten && !fehler && <div className="text-xs" style={{ color: "var(--text-muted)" }}>Lädt …</div>}
        {fehler && <div className="text-xs" style={{ color: "var(--tx-rot)" }} data-testid="chat-fehler">{fehler}</div>}
        {daten && nachrichten.length === 0 && (
          <div className="text-xs text-center py-2" style={{ color: "var(--text-muted)" }}>
            Noch keine Nachrichten. Schreib kurz, was los ist — der Verlauf bleibt beim Auftrag.
          </div>
        )}
        {nachrichten.map((m) => (
          <div key={m.id} className={`flex flex-col ${m.eigene ? "items-end" : "items-start"}`}
               data-testid={`chat-nachricht-${m.id}`}>
            <div className="max-w-[85%] rounded-2xl px-3 py-2 text-sm whitespace-pre-wrap break-words"
                 style={m.eigene
                   ? { background: "color-mix(in srgb, var(--st-blau) 18%, transparent)", color: "var(--text-primary)" }
                   : { background: "var(--wa-06, var(--wa-05))", color: "var(--text-primary)" }}>
              {!m.eigene && (
                <div className="text-[10px] font-semibold mb-0.5" style={{ color: "var(--text-secondary)" }}>
                  {m.von === "buero" ? `Büro${m.von_name ? ` · ${m.von_name}` : ""}` : (m.von_name || "Fahrer")}
                </div>
              )}
              {m.text}
              {m.foto && <ChatFoto rolle={rolle} terminId={terminId} nachrichtId={m.id} />}
              <div className="text-[10px] mt-0.5 text-right" style={{ color: "var(--text-muted)" }}>
                {zeitKurz(m.erstellt_am)}
              </div>
            </div>
            {haken && haken.id === m.id && (
              <div className="text-[10px] mt-0.5" style={{ color: "var(--text-muted)" }} data-testid="chat-gelesen">
                {haken.text}
              </div>
            )}
          </div>
        ))}
      </div>

      {daten && !kannSchreiben && (
        <div className="px-3 pb-3 text-xs" style={{ color: "var(--text-secondary)" }} data-testid="chat-hinweis">
          {daten.hinweis || "Schreiben ist hier gerade nicht möglich."}
        </div>
      )}

      {daten && kannSchreiben && (
        <div className="px-3 pb-3 space-y-2 border-t pt-2" style={{ borderColor: "var(--border-default)" }}>
          <div className="flex flex-wrap gap-1.5" data-testid="chat-schnell">
            {schnellantworten.map((s) => (
              <button key={s} type="button" disabled={sendet} onClick={() => senden({ inhalt: s, schnell: true })}
                      className="text-xs px-3 py-1.5 rounded-full border disabled:opacity-50"
                      style={{ borderColor: "var(--border-default)", color: "var(--text-primary)", background: "var(--wa-04, var(--wa-05))" }}>
                {s}
              </button>
            ))}
          </div>
          <div className="flex items-end gap-2">
            <textarea value={text} onChange={(e) => setText(e.target.value.slice(0, TEXT_MAX))}
                      rows={1} maxLength={TEXT_MAX} placeholder="Nachricht schreiben …"
                      aria-label="Nachricht" data-testid={`chat-eingabe-${terminId}`}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); senden({ inhalt: text }); }
                      }}
                      className="flex-1 min-w-0 rounded-lg border px-3 py-2 text-sm resize-none outline-none"
                      style={{ borderColor: "var(--border-default)", background: "var(--bg-input-solid)",
                               color: "var(--text-primary)", minHeight: 40, maxHeight: 120 }} />
            <input ref={dateiRef} type="file" accept="image/*" capture="environment" className="hidden"
                   onChange={fotoGewaehlt} data-testid={`chat-foto-input-${terminId}`} />
            <button type="button" onClick={() => dateiRef.current?.click()} disabled={sendet}
                    aria-label="Foto senden" title="Foto senden"
                    className="shrink-0 rounded-lg border disabled:opacity-50 flex items-center justify-center"
                    style={{ borderColor: "var(--border-default)", color: "var(--text-primary)", width: 40, height: 40 }}>
              <Camera size={16} />
            </button>
            <button type="button" onClick={() => senden({ inhalt: text })} disabled={sendet || !text.trim()}
                    aria-label="Senden" data-testid={`chat-senden-${terminId}`}
                    className="shrink-0 apple-btn apple-btn-primary !p-0 disabled:opacity-50 flex items-center justify-center"
                    style={{ width: 40, height: 40 }}>
              <Send size={16} />
            </button>
          </div>
          {text.length > TEXT_MAX - 100 && (
            <div className="text-[10px] text-right" style={{ color: "var(--text-muted)" }}>
              {text.length} / {TEXT_MAX}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
