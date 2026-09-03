import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Check, Handshake, Inbox, MessageSquare, X } from "lucide-react";

/**
 * Kaufanfragen vom B2B-Marktplatz (Händler-Sicht).
 *
 * Zwischenhändler senden Interesse/Angebote zu veröffentlichten Inseraten —
 * hier beantwortet der Chef sie: akzeptieren (reserviert das Fahrzeug),
 * ablehnen oder Gegenangebot. Review 09/2026: die Endpunkte existierten,
 * es gab aber keinerlei Oberfläche dafür.
 */

const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE")} €`);
const fmtZeit = (iso) => {
  try {
    return new Date(iso).toLocaleString("de-DE", {
      day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso || "—"; }
};

const STATUS_META = {
  offen:        { label: "Offen",        fg: "#fbbf24", bg: "rgba(245,158,11,0.12)", bd: "rgba(245,158,11,0.35)" },
  gegenangebot: { label: "Gegenangebot", fg: "#60a5fa", bg: "rgba(59,130,246,0.12)", bd: "rgba(59,130,246,0.35)" },
  akzeptiert:   { label: "Akzeptiert",   fg: "#34c759", bg: "rgba(52,199,89,0.12)",  bd: "rgba(52,199,89,0.35)" },
  abgelehnt:    { label: "Abgelehnt",    fg: "#a1a1aa", bg: "rgba(255,255,255,0.05)", bd: "rgba(255,255,255,0.12)" },
};

const FILTERS = [
  { key: "", label: "Alle" },
  { key: "offen", label: "Offen" },
  { key: "gegenangebot", label: "Gegenangebot" },
  { key: "akzeptiert", label: "Akzeptiert" },
  { key: "abgelehnt", label: "Abgelehnt" },
];

function StatusBadge({ status }) {
  const m = STATUS_META[status] || STATUS_META.abgelehnt;
  return (
    <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
          style={{ color: m.fg, background: m.bg, border: `1px solid ${m.bd}` }}>
      {m.label}
    </span>
  );
}

export default function Anfragen() {
  const [items, setItems] = useState(null);
  const [filter, setFilter] = useState("");
  const [busyId, setBusyId] = useState(null);
  const [counterFor, setCounterFor] = useState(null); // interest_id mit offenem Gegenangebots-Formular
  const [counterVal, setCounterVal] = useState("");
  const [counterMsg, setCounterMsg] = useState("");

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/dealer/interessen", {
        params: filter ? { status: filter } : {},
      });
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      toast.error(errMsg(e, "Anfragen konnten nicht geladen werden"));
      setItems([]);
    }
  }, [filter]);
  useEffect(() => { load(); }, [load]);

  const antworten = async (it, action, extra = {}) => {
    if (busyId) return;
    setBusyId(it.id);
    try {
      await api.post(`/interessen/${it.id}/antwort`, { action, message: "", ...extra });
      toast.success(action === "akzeptieren"
        ? "Anfrage akzeptiert — das Fahrzeug ist jetzt für den Käufer reserviert"
        : action === "ablehnen" ? "Anfrage abgelehnt" : "Gegenangebot gesendet");
      setCounterFor(null); setCounterVal(""); setCounterMsg("");
      load();
    } catch (e) {
      toast.error(errMsg(e, "Antwort fehlgeschlagen"));
    } finally {
      setBusyId(null);
    }
  };

  const sendCounter = (it) => {
    const betrag = Number(counterVal);
    if (!betrag || betrag <= 0) { toast.error("Bitte einen Betrag eingeben"); return; }
    antworten(it, "gegenangebot", { counter_offer: betrag, message: counterMsg });
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="anfragen-page">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="overline">Marktplatz</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
            Kaufanfragen
          </h1>
        </div>
        {items && <span className="text-xs text-zinc-500">{items.length} Anfragen</span>}
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} onClick={() => setFilter(f.key)}
                  data-testid={`anfragen-filter-${f.key || "alle"}`}
                  className={`px-3 py-1.5 rounded-lg text-[13px] border transition ${
                    filter === f.key ? "text-white border-white/40 bg-white/10" : "text-zinc-500"}`}
                  style={filter === f.key ? {} : { borderColor: "var(--border-default)" }}>
            {f.label}
          </button>
        ))}
      </div>

      {items === null ? (
        <div className="mt-10 text-sm" style={{ color: "var(--text-muted)" }}>Lädt…</div>
      ) : items.length === 0 ? (
        <div className="mt-10 text-center py-16 tactical-card">
          <Inbox size={28} className="mx-auto mb-3" style={{ color: "var(--text-muted)" }} />
          <div className="text-[15px] font-semibold">Keine Anfragen{filter ? " mit diesem Status" : ""}</div>
          <div className="mt-1 text-[13px]" style={{ color: "var(--text-muted)" }}>
            Anfragen erscheinen hier, sobald Zwischenhändler Interesse an deinen veröffentlichten Inseraten melden.
          </div>
        </div>
      ) : (
        <div className="mt-5 space-y-3">
          {items.map((it) => (
            <div key={it.id} className="tactical-card p-4 sm:p-5" data-testid={`anfrage-${it.id}`}>
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Link to={`/app/inserat/${it.listing_id}`}
                          className="font-display font-bold text-lg tracking-tight hover:underline truncate">
                      {it.listing_title || "Inserat"}
                    </Link>
                    <StatusBadge status={it.status} />
                  </div>
                  <div className="mt-1 text-[13px]" style={{ color: "var(--text-secondary)" }}>
                    {it.buyer_name}{it.buyer_email ? ` · ${it.buyer_email}` : ""} · {fmtZeit(it.created_at)}
                  </div>
                  {it.message && (
                    <div className="mt-2 text-[13.5px] flex items-start gap-1.5" style={{ color: "var(--text-secondary)" }}>
                      <MessageSquare size={14} className="mt-0.5 shrink-0" style={{ color: "var(--text-muted)" }} />
                      <span className="whitespace-pre-line break-words">{it.message}</span>
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                    {it.offer != null ? "Angebot des Käufers" : "Ohne Preisangebot"}
                  </div>
                  <div className="font-display font-black text-2xl tracking-tight">{fmtEur(it.offer)}</div>
                  {it.status === "gegenangebot" && it.counter_offer != null && (
                    <div className="mt-1 text-[12px] text-sky-400">Dein Gegenangebot: {fmtEur(it.counter_offer)}</div>
                  )}
                </div>
              </div>

              {(it.history || []).length > 1 && (
                <div className="mt-3 pt-3 space-y-1 text-[12px]" style={{ borderTop: "1px solid var(--border-default)", color: "var(--text-muted)" }}>
                  {it.history.map((h, i) => (
                    <div key={i}>
                      {h.von === "haendler" ? "Du" : "Käufer"} · {h.aktion}
                      {h.angebot != null ? ` · ${fmtEur(h.angebot)}` : ""}
                      {h.nachricht ? ` · „${h.nachricht}"` : ""} · {fmtZeit(h.zeit)}
                    </div>
                  ))}
                </div>
              )}

              {it.status === "offen" && (
                <div className="mt-4 flex flex-wrap items-center gap-2">
                  <button onClick={() => antworten(it, "akzeptieren")} disabled={busyId === it.id}
                          data-testid={`anfrage-akzeptieren-${it.id}`}
                          className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
                          style={{ background: "#34c759" }}>
                    <Check size={15} /> Akzeptieren & reservieren
                  </button>
                  <button onClick={() => { setCounterFor(counterFor === it.id ? null : it.id); setCounterVal(""); setCounterMsg(""); }}
                          disabled={busyId === it.id}
                          data-testid={`anfrage-gegenangebot-${it.id}`}
                          className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold border disabled:opacity-50"
                          style={{ borderColor: "var(--border-default)" }}>
                    <Handshake size={15} /> Gegenangebot
                  </button>
                  <button onClick={() => antworten(it, "ablehnen")} disabled={busyId === it.id}
                          data-testid={`anfrage-ablehnen-${it.id}`}
                          className="inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm text-zinc-400 hover:text-red-400 disabled:opacity-50">
                    <X size={15} /> Ablehnen
                  </button>
                </div>
              )}
              {it.status === "gegenangebot" && (
                <div className="mt-3 text-[12.5px]" style={{ color: "var(--text-muted)" }}>
                  Warte auf die Antwort des Käufers auf dein Gegenangebot.
                </div>
              )}

              {counterFor === it.id && it.status === "offen" && (
                <div className="mt-3 rounded-xl p-3 flex flex-wrap items-end gap-2"
                     style={{ background: "rgba(255,255,255,0.03)", border: "1px solid var(--border-default)" }}>
                  <div>
                    <label className="block text-[10px] uppercase tracking-wide mb-1" style={{ color: "var(--text-muted)" }}>
                      Gegenangebot (€)
                    </label>
                    <input type="number" min="1" value={counterVal} onChange={(e) => setCounterVal(e.target.value)}
                           data-testid={`gegenangebot-betrag-${it.id}`} autoFocus
                           className="h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40 w-36"
                           style={{ borderColor: "var(--border-default)" }} />
                  </div>
                  <div className="flex-1 min-w-[180px]">
                    <label className="block text-[10px] uppercase tracking-wide mb-1" style={{ color: "var(--text-muted)" }}>
                      Nachricht (optional)
                    </label>
                    <input value={counterMsg} onChange={(e) => setCounterMsg(e.target.value)} maxLength={2000}
                           className="h-9 px-2.5 rounded-lg border bg-transparent text-sm outline-none focus:border-white/40 w-full"
                           style={{ borderColor: "var(--border-default)" }} />
                  </div>
                  <button onClick={() => sendCounter(it)} disabled={busyId === it.id}
                          data-testid={`gegenangebot-senden-${it.id}`}
                          className="h-9 rounded-lg px-4 text-sm font-semibold text-white disabled:opacity-50"
                          style={{ background: "var(--accent-red)" }}>
                    Senden
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
