import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { ArrowLeft, Camera, CheckCircle2, Undo2, Tag, Globe, EyeOff } from "lucide-react";

/**
 * Inserats-Editor: automatisch vorausgefüllter Entwurf aus der Fahrzeugakte
 * (inkl. Abholungs-Abweichungen), Foto-Modus, Preise mit Margen-Rechner,
 * Workflow Entwurf → Verkaufsbereit → Reserviert/Verkauft.
 */

const fmtEur = (n) => (n == null ? "—" : `${Number(n).toLocaleString("de-DE", { minimumFractionDigits: 0 })} €`);

const STATUS_LABELS = {
  entwurf: "Entwurf", verkaufsbereit: "Verkaufsbereit",
  veroeffentlicht: "Veröffentlicht", reserviert: "Reserviert",
  verkauft: "Verkauft", zurueckgezogen: "Zurückgezogen",
};

export default function Inserat() {
  const { id } = useParams();
  const [l, setL] = useState(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);
  const backend = process.env.REACT_APP_BACKEND_URL;

  const load = useCallback(async () => {
    try {
      const r = await api.get(`/resale/${id}`);
      setL(r.data);
    } catch (e) { toast.error(errMsg(e, "Inserat konnte nicht geladen werden")); }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  if (!l) return <div className="p-10 text-zinc-500 text-sm">lade…</div>;

  const set = (k) => (e) => setL((s) => ({ ...s, [k]: e.target.value }));
  const setPrice = (k) => (e) =>
    setL((s) => ({ ...s, prices: { ...s.prices, [k]: e.target.value === "" ? null : parseFloat(e.target.value) } }));

  const save = async (extra = {}) => {
    setBusy(true);
    try {
      const r = await api.put(`/resale/${l.id}`, {
        title: l.title, description: l.description,
        known_defects: l.known_defects,
        photo_mode: l.photos?.mode,
        price_public: l.prices?.public, price_b2b: l.prices?.b2b,
        price_network: l.prices?.network,
        costs: l.costs,
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

  const uploadPhotos = async (files) => {
    if (!files?.length) return;
    const toB64 = (f) => new Promise((res, rej) => {
      const r = new FileReader(); r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(f);
    });
    try {
      const photos = await Promise.all([...files].slice(0, 20).map(toB64));
      await api.post(`/resale/${l.id}/photos`, { photos_b64: photos });
      toast.success(`${photos.length} Foto(s) hochgeladen`);
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const margin = l.margin || {};
  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40";
  const st = { borderColor: "var(--border-default)" };
  const einkaufFotos = l.photos?.einkauf_urls || [];
  const uploadedKeys = l.photos?.uploaded_keys || [];
  const mode = l.photos?.mode || "einkauf";

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="inserat-page">
      <Link to={`/app/akte/${l.vehicle_id}`} className="inline-flex items-center gap-1.5 text-xs text-zinc-400 hover:text-white">
        <ArrowLeft size={14} /> Zur Fahrzeugakte
      </Link>
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
                <Globe size={16} /> Auf Marktplatz veröffentlichen
              </button>
              <button onClick={() => setStatus("reserviert")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Reservieren</button>
              <button onClick={() => {
                        const p = window.prompt("Verkaufspreis (€):", l.prices?.public || "");
                        if (p !== null) setStatus("verkauft", parseFloat(p || 0) || null);
                      }}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold text-white"
                      style={{ background: "#34c759" }}>
                <Tag size={13} /> Verkauft
              </button>
              <button onClick={() => setStatus("entwurf")} className="rounded-xl px-3 py-2 text-xs text-zinc-400 hover:text-white inline-flex items-center gap-1"><Undo2 size={13} /> Zurück zu Entwurf</button>
            </>
          )}
          {l.status === "veroeffentlicht" && (
            <>
              <span className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold"
                    style={{ background: "#34c75920", color: "#34c759", border: "1px solid #34c75955" }}>
                <Globe size={14} /> Live auf dem Marktplatz
              </span>
              <button onClick={() => setStatus("reserviert")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Reservieren</button>
              <button onClick={() => {
                        const p = window.prompt("Verkaufspreis (€):", l.prices?.public || "");
                        if (p !== null) setStatus("verkauft", parseFloat(p || 0) || null);
                      }}
                      className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-semibold text-white"
                      style={{ background: "#34c759" }}>
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
                        const p = window.prompt("Verkaufspreis (€):", l.prices?.public || "");
                        if (p !== null) setStatus("verkauft", parseFloat(p || 0) || null);
                      }}
                      className="rounded-xl px-4 py-2.5 text-sm font-semibold text-white" style={{ background: "#34c759" }}>
                Als verkauft markieren
              </button>
              <button onClick={() => setStatus("verkaufsbereit")} className="rounded-xl px-3 py-2 text-xs border" style={st}>Reservierung aufheben</button>
            </>
          )}
        </div>
      </div>

      {(l.auto_notes || []).length > 0 && (
        <div className="mt-3 rounded-xl border px-4 py-3 text-xs space-y-0.5"
             style={{ borderColor: "#0ea5e955", background: "#0ea5e914", color: "#7dd3fc" }}>
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
            <label className="text-[11px] text-zinc-500 mt-3 block">Beschreibung</label>
            <textarea value={l.description || ""} onChange={set("description")} rows={14}
                      className={inputCls} style={st} disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-3 block">Bekannte Mängel (eine je Zeile)</label>
            <textarea value={(l.known_defects || []).join("\n")}
                      onChange={(e) => setL((s) => ({ ...s, known_defects: e.target.value.split("\n") }))}
                      rows={4} className={inputCls} style={st} disabled={l.status === "verkauft"} />
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
                     onChange={(e) => uploadPhotos(e.target.files)} />
            </div>
            <div className="mt-2 flex gap-1.5">
              {[["einkauf", "Einkaufsfotos"], ["neu", "Nur neue Fotos"], ["beide", "Einkauf + neue"]].map(([k, label]) => (
                <button key={k}
                        onClick={() => { setL((s) => ({ ...s, photos: { ...s.photos, mode: k } })); }}
                        className={`px-3 py-1.5 rounded-lg text-xs border ${mode === k ? "bg-white/10 font-semibold" : "text-zinc-400"}`}
                        style={st}>
                  {label}
                </button>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-4 sm:grid-cols-6 gap-2">
              {(mode !== "neu") && einkaufFotos.slice(0, 12).map((u, i) => (
                <img key={`e${i}`} src={u} alt="" className="aspect-square object-cover rounded-lg opacity-90" />
              ))}
              {(mode !== "einkauf") && uploadedKeys.map((k) => (
                <img key={k} src={`${backend}/api/files/${k}`} alt="" className="aspect-square object-cover rounded-lg" />
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
            <input type="number" value={l.prices?.public ?? ""} onChange={setPrice("public")}
                   className={inputCls} style={st} placeholder="20900" disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-2 block">B2B-Preis (optional)</label>
            <input type="number" value={l.prices?.b2b ?? ""} onChange={setPrice("b2b")}
                   className={inputCls} style={st} disabled={l.status === "verkauft"} />
            <label className="text-[11px] text-zinc-500 mt-2 block">Privater Netzwerkpreis (optional)</label>
            <input type="number" value={l.prices?.network ?? ""} onChange={setPrice("network")}
                   className={inputCls} style={st} disabled={l.status === "verkauft"} />
          </div>

          <div className="tactical-card p-4">
            <div className="text-sm font-bold uppercase tracking-wide mb-2">Kalkulation</div>
            <div className="space-y-1 text-sm">
              <div className="flex justify-between"><span className="text-zinc-500">Einkaufspreis</span><span>{fmtEur(margin.purchase_price)}</span></div>
              <div className="flex justify-between"><span className="text-zinc-500">Kosten gesamt</span><span>{fmtEur(margin.costs_total)}</span></div>
              <div className="flex justify-between border-t pt-1" style={st}><span className="text-zinc-500">Gesamtkosten</span><span>{fmtEur(margin.total_cost)}</span></div>
              <div className="flex justify-between text-base font-bold pt-1">
                <span>Erwartete Marge</span>
                <span style={{ color: (margin.expected_margin ?? 0) >= 0 ? "#34c759" : "#ff3b30" }}>
                  {l.status === "verkauft" && l.sold_price != null
                    ? fmtEur(l.sold_price - (margin.total_cost || 0))
                    : fmtEur(margin.expected_margin)}
                </span>
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
        </div>
      </div>
    </div>
  );
}
