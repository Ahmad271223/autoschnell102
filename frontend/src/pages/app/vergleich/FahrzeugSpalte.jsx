import {
  Activity, Calendar as CalendarIcon, Cog, ExternalLink, Eye, Fuel, Gauge, Hash, Image as ImageIcon, MapPin,
} from "lucide-react";
import BilderNachholen from "@/components/BilderNachholen";
import PortalBadge from "@/components/PortalBadge";
import { thumbSrc } from "@/lib/bilder";
import { filterOeffnen } from "@/lib/filterOeffnen";
import { datenStand, filterEintraege } from "./anzeige";

/**
 * Linke Spalte des Ergebnisses: erkanntes Fahrzeug (Preis, Daten, Fotos, Ausstattung, Beschreibung) und die
 * Filter-Karten je Portal (08.10.2026 aus Vergleich.jsx herausgezogen — nur Anzeige).
 */
export default function FahrzeugSpalte({ result, url, setResult }) {
  const v = result.vehicle;
  const stand = datenStand(result);
  return (
    <div className="lg:col-span-8 space-y-5">
      <div className="apple-surface p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <div className="overline">Fahrzeug erkannt</div>
            <h2 className="font-display font-bold text-2xl lg:text-3xl tracking-tight mt-1" data-testid="vehicle-title">
              {v.make_label} {v.model_label}
            </h2>
            <div className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>
              {v.model_description}
            </div>
            {(v.seller_zip || v.seller_city || v.location) && (
              <div className="text-xs mt-2 inline-flex items-center gap-1.5" style={{ color: "var(--text-muted)" }}>
                <MapPin size={11} className="text-[var(--accent-red)]" />
                Standort: {v.location || [v.seller_zip, v.seller_city].filter(Boolean).join(" ")}
              </div>
            )}
          </div>
          <div className="text-right shrink-0">
            <div className="font-display font-black text-3xl">
              {v.list_price ? `${v.list_price.toLocaleString("de-DE")} €` : "—"}
            </div>
            <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>Listenpreis · nicht im Vertrag</div>
            {/* Prüfbericht 20.09.2026 (S-09): Preisart (VB) und MwSt-Ausweis aus dem Inserat */}
            {(v.price_negotiable || v.mwst_ausweisbar === true) && (
              <div className="text-[11px] font-semibold" data-testid="vergleich-preisart"
                   style={{ color: "var(--text-secondary)" }}>
                {[v.price_negotiable ? "VB (Verhandlungsbasis)" : null,
                  v.mwst_ausweisbar === true ? "MwSt. ausweisbar" : null]
                  .filter(Boolean).join(" · ")}
              </div>
            )}
            {stand && (
              <div className={`text-[11px] ${stand.alt ? "font-semibold" : ""}`}
                   data-testid="vergleich-datenstand"
                   style={{ color: stand.alt ? "var(--tx-amber)" : "var(--text-muted)" }}>
                {stand.text}
              </div>
            )}
          </div>
        </div>

        <div className="mt-6 grid grid-cols-2 md:grid-cols-3 gap-3">
          <Stat icon={CalendarIcon} label="Erstzulassung" value={v.first_registration} />
          <Stat icon={Gauge} label="Kilometer" value={v.mileage ? `${v.mileage.toLocaleString("de-DE")} km` : "—"} />
          <Stat icon={Activity} label="Leistung" value={v.power_kw ? `${v.power_kw} kW · ${v.power_ps} PS` : "—"} />
          <Stat icon={Fuel} label="Kraftstoff" value={v.fuel_label} />
          <Stat icon={Cog} label="Getriebe" value={v.gearbox_label} />
          <Stat icon={Hash} label="Hubraum" value={v.displacement ? `${v.displacement} ccm` : "—"} />
        </div>

        {v.images?.length > 0 && (
          <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
            <div className="overline mb-3 flex items-center gap-1.5">
              <ImageIcon size={11} /> Fotos vom Inserat ({v.images.length})
            </div>
            <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-2" data-testid="kleinanzeigen-gallery">
              {/* Prüfbericht 20.09. U-14: Index im Schlüssel — doppelte
                  Bildadressen ergaben doppelte React-Schlüssel. */}
              {v.images.slice(0, 10).map((src, idx) => (
                <a key={`${idx}-${src}`} href={src} target="_blank" rel="noopener noreferrer"
                   className="block aspect-[4/3] rounded-lg overflow-hidden border hover:opacity-80 transition"
                   style={{ borderColor: "var(--hairline)" }}
                   data-testid={`gallery-thumb-${idx}`}>
                  {/* 10.09.2026: Vorschaubild ueber den eigenen Bild-Proxy (klein,
                      zwischengespeichert); schlaegt es fehl, das Portalbild direkt. */}
                  <img src={thumbSrc(v.images_thumbs?.[idx], src)} alt="" loading="lazy"
                       referrerPolicy="no-referrer" className="w-full h-full object-cover"
                       onError={(e) => { if (e.currentTarget.src !== src) e.currentTarget.src = src; }} />
                </a>
              ))}
              {v.images.length > 10 && (
                <div className="aspect-[4/3] rounded-lg flex items-center justify-center text-xs font-semibold"
                     style={{ background: "var(--apple-btn-secondary-bg)", color: "var(--text-secondary)" }}>
                  +{v.images.length - 10} weitere
                </div>
              )}
            </div>
          </div>
        )}

        {/* Wunsch Ahmad 03.10.2026: Daten kamen, Fotos nicht -> komplett neu abrufen */}
        {!(v.images?.length > 0) && result.bilder_nachholen_moeglich !== false && (
          <BilderNachholen
            key={result.cache_key || result.vehicle_id}
            url={result.link || url}
            onBilder={(a) => setResult((r) => (r && r.vehicle_id === result.vehicle_id
              ? { ...r, vehicle: { ...r.vehicle, images: a.images, image_urls: a.images,
                                   image_count: a.bilder, images_thumbs: a.images_thumbs } }
              : r))} />
        )}

        {v.features?.length > 0 && (
          <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
            <div className="overline mb-3">Ausstattung ({v.features.length})</div>
            <div className="flex flex-wrap gap-1.5">
              {v.features.map((f, i) => (
                <span key={`${i}-${f}`}
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

        {v.description && (
          <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}>
            <div className="overline mb-3">Beschreibung</div>
            <p className="text-sm leading-relaxed whitespace-pre-line" data-testid="vehicle-description"
               style={{ color: "var(--text-secondary)" }}>
              {v.description}
            </p>
          </div>
        )}
      </div>

      {/* RP-439/RP-419: ohne erkannte Marke gibt es keinen mobile.de-Link
          (er hätte über alle Marken gesucht) — der Grund steht im Hinweis. */}
      {result.search_url && (
        <div className="apple-surface p-6">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div className="flex items-start gap-3">
              <PortalBadge kind="mobile" size="sm" />
              <div>
                <div className="overline">Mobile.de Filter</div>
                <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Generierter Such-Link auf Basis deiner Vergleichsregeln</div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => filterOeffnen(filterEintraege(result, { autoscout: false }))}
              data-testid="open-mobile-btn"
              className="apple-btn apple-btn-primary"
            >
              <Eye size={14} /> Öffnen <ExternalLink size={12} />
            </button>
          </div>
        </div>
      )}

      {result.autoscout_url && (
        <div className="apple-surface p-6">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div className="flex items-start gap-3">
              <PortalBadge kind="autoscout" size="sm" />
              <div>
                <div className="overline">AutoScout24 Filter</div>
                <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                  Gleicher Filter, zweite Plattform — doppelte Reichweite
                </div>
              </div>
            </div>
            <button
              type="button"
              onClick={() => filterOeffnen(filterEintraege(result, { mobile: false }))}
              data-testid="open-autoscout-btn"
              className="apple-btn apple-btn-secondary"
            >
              <Eye size={14} /> Öffnen <ExternalLink size={12} />
            </button>
          </div>
        </div>
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
