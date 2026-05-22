import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowRight, ExternalLink, Activity, Gauge, Calendar as CalendarIcon, Fuel,
  Cog, Hash, FileText, Send, Loader2, MapPin, Sparkles, Eye, Image as ImageIcon,
  X as XIcon,
} from "lucide-react";
import ContractDialog from "@/components/ContractDialog";
import SendDialog from "@/components/SendDialog";
import SnapshotCard from "@/components/SnapshotCard";
import ProfileBadge from "@/components/ProfileBadge";
import { openContractPdf } from "@/lib/pdf";
import { openInPopup, openMultiple } from "@/lib/popup";

const SAMPLE_URLS = [
  "https://m.mobile.de/fahrzeuge/details.html?id=448228023",
  "https://suchen.mobile.de/fahrzeuge/details.html?id=391155421",
  "https://www.kleinanzeigen.de/s-anzeige/...",
];

const STORAGE_KEY = "ah_vergleich_state";

export default function Vergleich() {
  // Restore last comparison so the user can navigate to PDFs / Fahrer
  // and come back without losing their result. Cleared on logout.
  const restored = (() => {
    try {
      const raw = sessionStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  })();

  const [url, setUrl] = useState(restored?.url || "");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(restored?.result || null);
  const [counter, setCounter] = useState(restored?.counter || null);
  const [showContract, setShowContract] = useState(false);
  const [contract, setContract] = useState(restored?.contract || null);
  const [showSend, setShowSend] = useState(false);
  // Portal-Toggles — Zustand wird in localStorage gespeichert
  const [portalMobile, setPortalMobile] = useState(() => {
    try { const v = localStorage.getItem("ah_portal_mobile"); return v === null ? true : v === "1"; }
    catch { return true; }
  });
  const [portalAutoscout, setPortalAutoscout] = useState(() => {
    try { const v = localStorage.getItem("ah_portal_autoscout"); return v === null ? true : v === "1"; }
    catch { return true; }
  });

  const toggleMobile = (v) => {
    setPortalMobile(v);
    try { localStorage.setItem("ah_portal_mobile", v ? "1" : "0"); } catch { /* ignore */ }
  };
  const toggleAutoscout = (v) => {
    setPortalAutoscout(v);
    try { localStorage.setItem("ah_portal_autoscout", v ? "1" : "0"); } catch { /* ignore */ }
  };

  // Persist on every meaningful state change.
  useEffect(() => {
    try {
      if (result) {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ url, result, counter, contract }));
      }
    } catch { /* quota/private mode — silent */ }
  }, [url, result, counter, contract]);

  const startCompare = async (e) => {
    e?.preventDefault?.();
    if (!url.trim()) return;
    setLoading(true);
    setResult(null);
    setCounter(null);
    setContract(null);
    try {
      const t0 = Date.now();
      const { data } = await api.post("/mobile/compare", { url });
      const t1 = Date.now();
      setResult({ ...data, ms: t1 - t0 });
      try {
        const { data: cnt } = await api.get(`/mobile/live-counter/${data.ad_id}`);
        setCounter(cnt);
      } catch (_) { /* ignore */ }
    } catch (err) {
      toast.error(errMsg(err, "Vergleich fehlgeschlagen"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!result?.ad_id) return;
    const t = setInterval(async () => {
      try {
        const { data } = await api.get(`/mobile/live-counter/${result.ad_id}`);
        setCounter(data);
      } catch (_) { /* ignore */ }
    }, 30000);
    return () => clearInterval(t);
  }, [result?.ad_id]);

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-[1480px] mx-auto" data-testid="vergleich-page">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="overline">Vergleich · Hauptansicht</div>
          <h1 className="font-display font-black text-3xl lg:text-5xl tracking-tighter mt-2">
            URL einfügen. <span style={{ color: "var(--accent-red)" }}>Vergleich starten.</span>
          </h1>
          <p className="mt-3 max-w-2xl" style={{ color: "var(--text-secondary)" }}>
            Mobile.de- oder Kleinanzeigen-Link einfügen — Daten laden, Regeln anwenden, mobile.de mit fertigem Filter öffnen.
          </p>
        </div>
        <ProfileBadge onChange={(p) => setResult((r) => r ? { ...r, active_profile: p } : r)} />
      </div>

      {/* Search bar */}
      <form onSubmit={startCompare} className="mt-8">
        {/* Zeile 1: URL-Input + Buttons */}
        <div className="apple-surface !rounded-2xl !p-1.5 max-w-4xl flex items-stretch gap-1.5 flex-wrap">
          {/* URL-Input */}
          <div className="flex-1 min-w-0 flex items-center pl-4" style={{ minWidth: 200 }}>
            <Sparkles size={15} className="text-[var(--accent-red)] shrink-0 mr-2.5" />
            <input
              data-testid="vergleich-url-input"
              required
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="mobile.de- oder kleinanzeigen.de-URL einfügen…"
              className="flex-1 bg-transparent py-3 text-base font-mono outline-none truncate"
              style={{ color: "var(--text-primary)" }}
              autoFocus
            />
            {url && (
              <button
                type="button"
                onClick={() => setUrl("")}
                data-testid="vergleich-url-clear"
                title="URL löschen"
                className="p-2 mr-1 rounded-md hover:bg-white/5 text-zinc-400 hover:text-white shrink-0"
              >
                <XIcon size={16} />
              </button>
            )}
          </div>

          {/* Trennlinie */}
          <div className="w-px self-stretch my-1" style={{ background: "var(--divider)" }} />

          {/* Auslesen */}
          <button
            data-testid="vergleich-start-btn"
            type="submit"
            disabled={loading}
            className="apple-btn apple-btn-primary !px-5 !py-2.5 disabled:opacity-60 disabled:cursor-not-allowed shrink-0"
          >
            {loading ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
            <span>{loading ? "Lade…" : "Auslesen"}</span>
          </button>

          {/* Trennlinie */}
          <div className="w-px self-stretch my-1" style={{ background: "var(--divider)" }} />

          {/* Portal-Toggles (Logos statt Text) */}
          <button
            type="button"
            data-testid="toggle-mobile"
            onClick={() => toggleMobile(!portalMobile)}
            title="mobile.de ein-/ausschalten"
            aria-label="mobile.de ein-/ausschalten"
            aria-pressed={portalMobile}
            className="shrink-0 flex items-center justify-center px-4 py-2 rounded-xl transition-all"
            style={{
              background: portalMobile ? "#ffffff" : "var(--hover-bg)",
              border: portalMobile ? "2px solid #e8472a" : "2px solid var(--divider)",
              boxShadow: portalMobile ? "0 0 0 2px rgba(232,71,42,0.15)" : "none",
              opacity: portalMobile ? 1 : 0.45,
              filter: portalMobile ? "none" : "grayscale(1)",
              height: 56,
            }}
          >
            <img
              src="/logos/mobile-de.png"
              alt="mobile.de"
              style={{ height: 36, width: "auto", display: "block" }}
            />
          </button>

          <button
            type="button"
            data-testid="toggle-autoscout"
            onClick={() => toggleAutoscout(!portalAutoscout)}
            title="AutoScout24 ein-/ausschalten"
            aria-label="AutoScout24 ein-/ausschalten"
            aria-pressed={portalAutoscout}
            className="shrink-0 flex items-center justify-center px-4 py-2 rounded-xl transition-all"
            style={{
              background: portalAutoscout ? "#2b2b2b" : "var(--hover-bg)",
              border: portalAutoscout ? "2px solid #ffe600" : "2px solid var(--divider)",
              boxShadow: portalAutoscout ? "0 0 0 2px rgba(255,230,0,0.20)" : "none",
              opacity: portalAutoscout ? 1 : 0.45,
              filter: portalAutoscout ? "none" : "grayscale(1)",
              height: 56,
            }}
          >
            <img
              src="/logos/autoscout24.png"
              alt="AutoScout24"
              style={{ height: 48, width: "auto", display: "block" }}
            />
          </button>

          {/* Filter öffnen */}
          <button
            type="button"
            data-testid="open-filter-btn"
            disabled={!result || (!portalMobile && !portalAutoscout)}
            onClick={() => {
              openMultiple([
                portalMobile    && result?.search_url    && { url: result.search_url,    name: "mobileFilterWindow" },
                portalAutoscout && result?.autoscout_url && { url: result.autoscout_url, name: "autoscoutFilterWindow" },
              ].filter(Boolean));
            }}
            className="shrink-0 apple-btn apple-btn-secondary !px-4 !py-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
            title={result ? "Filter der aktiven Portale öffnen" : "Erst Vergleich auslesen"}
          >
            <Eye size={14} />
            <span>Filter öffnen</span>
            <ExternalLink size={11} />
          </button>
        </div>

        {/* Demo-URLs */}
        <div className="mt-3 text-xs flex flex-wrap gap-2 items-center" style={{ color: "var(--text-muted)" }}>
          <span style={{ color: "var(--text-secondary)" }}>Demo:</span>
          {SAMPLE_URLS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setUrl(s)}
              data-testid={`sample-url-${s.split("=").pop()}`}
              className="apple-btn apple-btn-secondary !py-1 !px-2.5 !text-[11px] !rounded-full font-mono"
            >
              ID: {s.split(/[=/]/).pop()}
            </button>
          ))}
        </div>
      </form>

      {/* Loading skeleton */}
      {loading && !result && (
        <div className="mt-10 grid lg:grid-cols-12 gap-5">
          <div className="lg:col-span-8 space-y-5">
            <div className="apple-surface p-6 animate-pulse">
              <div className="h-4 w-24 rounded mb-3" style={{ background: "var(--apple-btn-secondary-bg)" }} />
              <div className="h-8 w-2/3 rounded mb-2" style={{ background: "var(--apple-btn-secondary-bg)" }} />
              <div className="h-4 w-1/2 rounded" style={{ background: "var(--apple-btn-secondary-bg)" }} />
              <div className="grid grid-cols-3 gap-3 mt-6">
                {[...Array(6)].map((_, i) => (
                  <div key={i} className="h-14 rounded-lg" style={{ background: "var(--apple-btn-secondary-bg)" }} />
                ))}
              </div>
            </div>
          </div>
          <div className="lg:col-span-4 space-y-5">
            <div className="apple-surface p-5 h-32 animate-pulse" />
            <div className="apple-surface p-5 h-40 animate-pulse" />
          </div>
        </div>
      )}

      {/* RESULT */}
      {result && (
        <div className="mt-10 grid lg:grid-cols-12 gap-5">
          {/* Left — vehicle */}
          <div className="lg:col-span-8 space-y-5">
            <div className="apple-surface p-6">
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div className="min-w-0">
                  <div className="overline">Fahrzeug erkannt</div>
                  <h2 className="font-display font-bold text-2xl lg:text-3xl tracking-tight mt-1" data-testid="vehicle-title">
                    {result.vehicle.make_label} {result.vehicle.model_label}
                  </h2>
                  <div className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
                    {result.vehicle.model_description}
                  </div>
                  {(result.vehicle.seller_zip || result.vehicle.seller_city || result.vehicle.location) && (
                    <div className="text-xs mt-2 inline-flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
                      <MapPin size={11} className="text-[var(--accent-red)]" />
                      Standort: {result.vehicle.location || [result.vehicle.seller_zip, result.vehicle.seller_city].filter(Boolean).join(" ")}
                    </div>
                  )}
                </div>
                <div className="text-right shrink-0">
                  <div className="font-display font-black text-3xl">
                    {result.vehicle.list_price ? `${result.vehicle.list_price.toLocaleString("de-DE")} €` : "—"}
                  </div>
                  <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>Listenpreis · nicht im Vertrag</div>
                </div>
              </div>

              <div className="mt-6 grid grid-cols-2 md:grid-cols-3 gap-3">
                <Stat icon={CalendarIcon} label="Erstzulassung" value={result.vehicle.first_registration} />
                <Stat icon={Gauge} label="Kilometer" value={result.vehicle.mileage ? `${result.vehicle.mileage.toLocaleString("de-DE")} km` : "—"} />
                <Stat icon={Activity} label="Leistung" value={result.vehicle.power_kw ? `${result.vehicle.power_kw} kW · ${result.vehicle.power_ps} PS` : "—"} />
                <Stat icon={Fuel} label="Kraftstoff" value={result.vehicle.fuel_label} />
                <Stat icon={Cog} label="Getriebe" value={result.vehicle.gearbox_label} />
                <Stat icon={Hash} label="Hubraum" value={result.vehicle.displacement ? `${result.vehicle.displacement} ccm` : "—"} />
              </div>

              {result.vehicle.images?.length > 0 && (
                <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
                  <div className="overline mb-3 flex items-center gap-1.5">
                    <ImageIcon size={11} /> Fotos vom Inserat ({result.vehicle.images.length})
                  </div>
                  <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2" data-testid="kleinanzeigen-gallery">
                    {result.vehicle.images.slice(0, 10).map((src, idx) => (
                      <a key={src} href={src} target="_blank" rel="noopener noreferrer"
                         className="block aspect-[4/3] rounded-lg overflow-hidden border hover:opacity-80 transition"
                         style={{ borderColor: "var(--hairline)" }}
                         data-testid={`gallery-thumb-${idx}`}>
                        <img src={src} alt="" loading="lazy" className="w-full h-full object-cover" />
                      </a>
                    ))}
                    {result.vehicle.images.length > 10 && (
                      <div className="aspect-[4/3] rounded-lg flex items-center justify-center text-xs font-semibold"
                           style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-secondary)" }}>
                        +{result.vehicle.images.length - 10} weitere
                      </div>
                    )}
                  </div>
                </div>
              )}

              {result.vehicle.features?.length > 0 && (
                <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
                  <div className="overline mb-3">Ausstattung ({result.vehicle.features.length})</div>
                  <div className="flex flex-wrap gap-1.5">
                    {result.vehicle.features.map((f) => (
                      <span key={f}
                            className="text-[11px] px-2.5 py-1 rounded-full"
                            style={{
                              background: "var(--apple-btn-secondary-bg)",
                              border: "1px solid var(--apple-btn-secondary-border)",
                              color: "var(--text-secondary)",
                            }}>
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {result.vehicle.description && (
                <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
                  <div className="overline mb-3">Beschreibung</div>
                  <p className="text-sm leading-relaxed whitespace-pre-line" data-testid="vehicle-description"
                     style={{ color: "var(--text-secondary)" }}>
                    {result.vehicle.description}
                  </p>
                </div>
              )}
            </div>

            <div className="apple-surface p-6">
              <div className="flex items-start justify-between gap-3 mb-3">
                <div>
                  <div className="overline">Mobile.de Filter</div>
                  <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Generierter Such-Link auf Basis deiner Vergleichsregeln</div>
                </div>
                <button
                  type="button"
                  onClick={() => openInPopup(result.search_url, "mobileFilterWindow")}
                  data-testid="open-mobile-btn"
                  className="apple-btn apple-btn-primary"
                >
                  <Eye size={14} /> Öffnen <ExternalLink size={12} />
                </button>
              </div>
            </div>

            {result.autoscout_url && (
              <div className="apple-surface p-6">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div>
                    <div className="overline">AutoScout24 Filter</div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                      Gleicher Filter, zweite Plattform — doppelte Reichweite
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => openInPopup(result.autoscout_url, "autoscoutFilterWindow")}
                    data-testid="open-autoscout-btn"
                    className="apple-btn apple-btn-secondary"
                  >
                    <Eye size={14} /> Öffnen <ExternalLink size={12} />
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Right — actions */}
          <div className="lg:col-span-4 space-y-5">
            <div className="apple-surface p-5" data-testid="live-counter-card">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <span className="live-dot" />
                  <span className="overline">live</span>
                </div>
                <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>aktualisiert alle 30s</span>
              </div>
              <div className="font-display font-black text-4xl mt-3 tracking-tight">
                {counter?.active_now ?? 0}
              </div>
              <div className="text-sm mt-0.5 font-medium" style={{ color: "var(--text-primary)" }}>
                {counter?.active_now === 1 ? "Händler prüft dieses Fahrzeug" :
                 counter?.active_now > 1 ? `Händler prüfen dieses Fahrzeug` :
                 "Du bist allein hier"}
              </div>
              <div className="text-[11px] mt-3 pt-3 border-t" style={{ color: "var(--text-muted)", borderColor: "var(--hairline)" }}>
                Heute insg.: <span className="font-semibold" style={{ color: "var(--text-primary)" }}>{counter?.today ?? 1}</span> Vergleiche
              </div>
            </div>

            <div className="apple-surface p-5">
              <div className="overline mb-3">Aktionen</div>
              <button onClick={() => setShowContract(true)} data-testid="create-contract-btn"
                      className="apple-btn apple-btn-primary w-full !py-3">
                <FileText size={15} /> Kaufvertrag erstellen
              </button>
              {contract && (
                <div className="mt-3 space-y-2">
                  <button
                    onClick={() => openContractPdf(contract.id)}
                    data-testid="open-pdf-btn"
                    className="apple-btn apple-btn-secondary w-full"
                  >
                    <FileText size={14} /> PDF öffnen
                  </button>
                  <button onClick={() => setShowSend(true)} data-testid="send-pdf-btn"
                          className="apple-btn apple-btn-secondary w-full">
                    <Send size={14} /> Versenden
                  </button>
                </div>
              )}
            </div>

            {result.snapshot_id && (
              <SnapshotCard snapshotId={result.snapshot_id} />
            )}

            <div className="text-[11px] leading-relaxed px-1" style={{ color: "var(--text-muted)" }}>
              <strong style={{ color: "var(--text-primary)" }}>Hinweis:</strong> Der Kaufpreis wird nie automatisch übernommen.
              Verhandelten Preis im nächsten Schritt manuell eintragen.
            </div>
          </div>
        </div>
      )}

      {showContract && result && (
        <ContractDialog
          open={showContract}
          onClose={() => setShowContract(false)}
          vehicle={result.vehicle}
          vehicleId={result.vehicle_id}
          onCreated={(c) => {
            setContract(c);
            if (c.appointment_id) {
              toast.success("PDF erstellt – Termin automatisch im Terminplaner angelegt");
            } else {
              toast.success("PDF erstellt");
            }
            setShowContract(false);
            setShowSend(true);
          }}
        />
      )}

      {showSend && contract && (
        <SendDialog
          open={showSend}
          contract={contract}
          onClose={() => setShowSend(false)}
        />
      )}
    </div>
  );
}

function Stat({ icon: Icon, label, value }) {
  return (
    <div className="apple-card p-3">
      <div className="flex items-center gap-1.5 text-[10px] uppercase font-bold tracking-wider"
           style={{ color: "var(--text-muted)" }}>
        <Icon size={11} className="text-[var(--accent-red)]" /> {label}
      </div>
      <div className="text-base font-semibold mt-1.5 truncate">{value || "—"}</div>
    </div>
  );
}
