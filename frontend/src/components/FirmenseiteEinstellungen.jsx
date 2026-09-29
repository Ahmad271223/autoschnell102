import { useCallback, useEffect, useState } from "react";
import { Copy, ExternalLink, Globe, ImagePlus, PenLine, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, errMsg } from "@/lib/api";
import { inZwischenablage } from "@/components/KundenportalDialog";

/**
 * Einstellungen → "Firmenseite & Kundenportal" (Wunsch Ahmad 29.09.2026): Jede Firma bekommt
 * eine eigene Seite (Adresse per DNS), auf der Kunden mit einem 6-stelligen Code ihren Kaufvertrag
 * öffnen und digital unterschreiben. Hier pflegt der Chef: Adresse (Unterdomain), Ein/Aus,
 * "Über uns", bis zu sechs Bilder, eigene Kundendomains und das Bild seiner Unterschrift
 * (landet im Kasten "Käufer / Händler" jedes über das Portal unterschriebenen Vertrags).
 */
function dateiAlsDataUrl(file) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result);
    r.onerror = rej;
    r.readAsDataURL(file);
  });
}

const BACKEND = process.env.REACT_APP_BACKEND_URL || "";

export default function FirmenseiteEinstellungen({ user }) {
  const [stand, setStand] = useState(null);
  const [form, setForm] = useState({ slug: "", aktiv: false, ueber_uns: "", domains: "" });
  const [fehler, setFehler] = useState("");
  const [busy, setBusy] = useState(false);
  const [unterschriftUrl, setUnterschriftUrl] = useState(null);

  const uebernehmen = useCallback((data) => {
    setStand(data);
    const w = data?.webseite || {};
    setForm({ slug: w.slug || "", aktiv: !!w.aktiv, ueber_uns: w.ueber_uns || "", domains: (w.domains || []).join("\n") });
  }, []);
  const laden = useCallback(async () => {
    try {
      const { data } = await api.get("/dealer/webseite");
      if (data) uebernehmen(data);
      setFehler("");
    } catch (e) {
      setFehler(errMsg(e, "Firmenseite konnte nicht geladen werden"));
    }
  }, [uebernehmen]);
  useEffect(() => { laden(); }, [laden]);

  // Unterschrift-Vorschau: privater Pfad, nur angemeldet — als Blob laden
  useEffect(() => {
    let url = null;
    let aktiv = true;
    (async () => {
      if (!stand?.unterschrift_vorhanden) { setUnterschriftUrl(null); return; }
      try {
        const { data } = await api.get("/dealer/unterschrift", { responseType: "blob" });
        url = URL.createObjectURL(data);
        if (aktiv) setUnterschriftUrl(url);
      } catch { if (aktiv) setUnterschriftUrl(null); }
    })();
    return () => { aktiv = false; if (url) URL.revokeObjectURL(url); };
  }, [stand?.unterschrift_vorhanden]);

  const istChef = !!stand?.ist_chef;
  const speichern = async () => {
    setBusy(true);
    try {
      const { data } = await api.put("/dealer/webseite", {
        slug: form.slug.trim().toLowerCase(), aktiv: form.aktiv, ueber_uns: form.ueber_uns,
        domains: form.domains.split(/\r?\n|,/).map((d) => d.trim()).filter(Boolean),
      });
      uebernehmen(data);
      setFehler("");
      toast.success("Firmenseite gespeichert");
    } catch (e) {
      setFehler(errMsg(e, "Konnte nicht speichern"));
    } finally { setBusy(false); }
  };
  const bildHochladen = async (file) => {
    if (!file) return;
    if (file.size > 8 * 1024 * 1024) { toast.error("Bild zu groß (max. 8 MB)"); return; }
    setBusy(true);
    try {
      const { data } = await api.post("/dealer/webseite/bilder", { bild_b64: await dateiAlsDataUrl(file) });
      uebernehmen(data);
      toast.success("Bild hochgeladen");
    } catch (e) { toast.error(errMsg(e, "Bild konnte nicht hochgeladen werden")); } finally { setBusy(false); }
  };
  const bildEntfernen = async (key) => {
    setBusy(true);
    try {
      const { data } = await api.delete(`/dealer/webseite/bilder/${key}`);
      uebernehmen(data);
    } catch (e) { toast.error(errMsg(e, "Bild konnte nicht entfernt werden")); } finally { setBusy(false); }
  };
  const unterschriftHochladen = async (file) => {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { toast.error("Unterschrift zu groß (max. 2 MB)"); return; }
    setBusy(true);
    try {
      await api.post("/dealer/unterschrift", { bild_b64: await dateiAlsDataUrl(file) });
      setStand((s) => ({ ...(s || {}), unterschrift_vorhanden: true }));
      toast.success("Unterschrift hinterlegt");
    } catch (e) { toast.error(errMsg(e, "Unterschrift konnte nicht hochgeladen werden")); } finally { setBusy(false); }
  };
  const unterschriftEntfernen = async () => {
    setBusy(true);
    try {
      await api.delete("/dealer/unterschrift");
      setStand((s) => ({ ...(s || {}), unterschrift_vorhanden: false }));
    } catch (e) { toast.error(errMsg(e, "Unterschrift konnte nicht entfernt werden")); } finally { setBusy(false); }
  };
  const kopieren = async (text) => {
    if (await inZwischenablage(text)) toast.success("Adresse kopiert");
    else toast.error("Konnte nicht kopieren");
  };

  const bilder = stand?.webseite?.bilder || [];
  const bildSrc = (url) => (url?.startsWith("http") ? url : `${BACKEND}${url}`);

  return (
    <div className="apple-surface p-6 mt-4" data-testid="firmenseite-einstellungen">
      <div className="mb-4">
        <h2 className="font-display font-bold text-xl tracking-tight flex items-center gap-2"><Globe size={18} /> Firmenseite &amp; Kundenportal</h2>
        <p className="text-sm text-zinc-500 mt-1">
          Eure eigene Seite mit Logo, „Über uns“, Bildern und dem Kundenportal: Kunden geben dort den 6-stelligen Code
          aus der App ein, sehen ihren Kaufvertrag und unterschreiben digital — ihr bekommt die Bestätigung als Meldung.
        </p>
      </div>
      {fehler && <div className="text-sm text-red-400 mb-3" data-testid="firmenseite-fehler">{fehler}</div>}
      {stand && (
        <div className="space-y-5">
          {/* Adresse */}
          <div>
            <label className="text-[12px] font-semibold text-zinc-400">Adresse der Firmenseite</label>
            <div className="flex flex-col sm:flex-row sm:items-center gap-2 mt-1">
              <input value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value })} disabled={!istChef}
                     placeholder={stand.slug_vorschlag || "kfz-mueller"} className="apple-input flex-1" data-testid="firmenseite-slug"
                     maxLength={40} autoCapitalize="none" spellCheck={false} />
              {stand.unterdomain_moeglich && <span className="text-sm text-zinc-500">.{stand.firmen_domain}</span>}
              {istChef && !form.slug && stand.slug_vorschlag && (
                <button type="button" onClick={() => setForm({ ...form, slug: stand.slug_vorschlag })}
                        className="text-[12px] underline text-zinc-400" data-testid="firmenseite-vorschlag">Vorschlag übernehmen</button>
              )}
            </div>
            {stand.url && (
              <div className="text-[12.5px] mt-1.5 flex flex-wrap items-center gap-2" data-testid="firmenseite-url">
                <a href={stand.url} target="_blank" rel="noreferrer" className="underline inline-flex items-center gap-1">{stand.url} <ExternalLink size={12} /></a>
                <button type="button" onClick={() => kopieren(stand.url)} className="inline-flex items-center gap-1 text-zinc-400"><Copy size={12} /> kopieren</button>
                {stand.url_pfad && stand.url_pfad !== stand.url && <span className="text-zinc-500">· auch: {stand.url_pfad}</span>}
              </div>
            )}
            <div className="text-[11.5px] text-zinc-500 mt-1">3–40 Zeichen, Kleinbuchstaben, Ziffern, Bindestrich. Die Unterdomain ist sofort erreichbar.</div>
          </div>

          {/* Ein/Aus */}
          <label className="flex items-center gap-3 cursor-pointer">
            <input type="checkbox" checked={form.aktiv} onChange={(e) => setForm({ ...form, aktiv: e.target.checked })} disabled={!istChef}
                   className="w-4 h-4" data-testid="firmenseite-aktiv" />
            <span className="text-sm">Firmenseite und Kundenportal einschalten</span>
          </label>

          {/* Über uns */}
          <div>
            <label className="text-[12px] font-semibold text-zinc-400">Über uns (kurz)</label>
            <textarea value={form.ueber_uns} onChange={(e) => setForm({ ...form, ueber_uns: e.target.value.slice(0, 2000) })} disabled={!istChef}
                      rows={4} className="apple-input w-full mt-1" placeholder="Wer ihr seid, was ihr ankauft, seit wann …" data-testid="firmenseite-ueber-uns" />
            <div className="text-[11px] text-zinc-500 text-right">{form.ueber_uns.length}/2000</div>
          </div>

          {/* Bilder */}
          <div>
            <div className="flex items-center justify-between">
              <label className="text-[12px] font-semibold text-zinc-400">Bilder ({bilder.length}/{stand.bilder_max || 6})</label>
              {istChef && bilder.length < (stand.bilder_max || 6) && (
                <label className="inline-flex items-center gap-1.5 text-[12px] font-semibold px-3 py-1.5 rounded-full cursor-pointer"
                       style={{ background: "var(--apple-btn-secondary-bg)" }} data-testid="firmenseite-bild-hochladen">
                  <ImagePlus size={14} /> Bild hinzufügen
                  <input type="file" accept="image/*" className="hidden" disabled={busy}
                         onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; bildHochladen(f); }} />
                </label>
              )}
            </div>
            {bilder.length > 0 ? (
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mt-2">
                {bilder.map((b) => (
                  <div key={b.key} className="relative aspect-square rounded-xl overflow-hidden" style={{ background: "var(--wa-05)" }}>
                    <img src={bildSrc(b.url)} alt="" className="w-full h-full object-cover" />
                    {istChef && (
                      <button type="button" onClick={() => bildEntfernen(b.key)} aria-label="Bild entfernen" disabled={busy}
                              className="absolute top-1 right-1 w-7 h-7 rounded-full flex items-center justify-center"
                              style={{ background: "rgba(0,0,0,0.6)", color: "#fff" }} data-testid={`firmenseite-bild-entfernen-${b.key.split("/").pop()}`}>
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : <div className="text-[12px] text-zinc-500 mt-1">Noch keine Bilder — z. B. Hof, Team, Ankauf vor Ort.</div>}
          </div>

          {/* Eigene Domains */}
          <div>
            <label className="text-[12px] font-semibold text-zinc-400">Eigene Kundendomains (optional, eine je Zeile)</label>
            <textarea value={form.domains} onChange={(e) => setForm({ ...form, domains: e.target.value })} disabled={!istChef}
                      rows={2} className="apple-input w-full mt-1 font-mono text-[13px]" placeholder="kfz-mueller.de" data-testid="firmenseite-domains" />
            <div className="text-[11.5px] text-zinc-500 mt-1">
              Damit eine eigene Domain funktioniert, muss sie per DNS (CNAME) auf die Plattform zeigen und beim Betreiber
              freigeschaltet werden — bitte nach dem Speichern kurz Bescheid geben. Bis dahin gilt die Unterdomain.
            </div>
          </div>

          {/* Unterschrift des Chefs */}
          <div className="rounded-2xl p-4" style={{ background: "var(--wa-05)", border: "1px solid var(--divider)" }}>
            <div className="flex items-center gap-2 font-semibold text-sm"><PenLine size={15} /> Unterschrift des Chefs</div>
            <div className="text-[12px] text-zinc-500 mt-1">
              Ein Bild eurer Unterschrift (Foto oder Scan, weißer Hintergrund). Sie steht im Kasten „Käufer / Händler“ jedes
              Vertrags, den ein Kunde über das Portal unterschreibt — der Vertrag ist damit von beiden Seiten unterschrieben.
            </div>
            <div className="flex items-center gap-4 mt-3">
              <div className="h-16 w-40 rounded-xl flex items-center justify-center overflow-hidden" style={{ background: "#fff", border: "1px solid var(--divider)" }}>
                {unterschriftUrl ? <img src={unterschriftUrl} alt="Unterschrift" className="max-h-full max-w-full object-contain" data-testid="firmenseite-unterschrift-bild" />
                  : <span className="text-[11px] text-zinc-500">{stand.unterschrift_vorhanden ? "…" : "keine hinterlegt"}</span>}
              </div>
              {istChef && (
                <div className="flex flex-col gap-1.5">
                  <label className="inline-flex items-center gap-1.5 text-[12px] font-semibold px-3 py-1.5 rounded-full cursor-pointer text-white"
                         style={{ background: "var(--accent-red)" }} data-testid="firmenseite-unterschrift-hochladen">
                    {stand.unterschrift_vorhanden ? "Unterschrift ersetzen" : "Unterschrift hochladen"}
                    <input type="file" accept="image/*" className="hidden" disabled={busy}
                           onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; unterschriftHochladen(f); }} />
                  </label>
                  {stand.unterschrift_vorhanden && (
                    <button type="button" onClick={unterschriftEntfernen} disabled={busy} className="text-[11.5px] text-zinc-500 hover:text-red-400 text-left"
                            data-testid="firmenseite-unterschrift-entfernen">Unterschrift entfernen</button>
                  )}
                </div>
              )}
            </div>
          </div>

          {istChef ? (
            <div className="flex justify-end">
              <button onClick={speichern} disabled={busy} className="apple-btn !rounded-full !px-5 !py-2.5 text-sm" data-testid="firmenseite-speichern">
                {busy ? "Speichert…" : "Firmenseite speichern"}
              </button>
            </div>
          ) : (
            <div className="text-[12px] text-zinc-500" data-testid="firmenseite-nur-chef">Die Firmenseite pflegt der Chef. Du kannst Verträge über das Kundenportal freigeben (Verträge → Stift-Symbol).</div>
          )}
        </div>
      )}
    </div>
  );
}
