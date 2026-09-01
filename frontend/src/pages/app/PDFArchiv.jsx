import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Search, Trash2, Eye, X, Car, ChevronLeft, ChevronRight } from "lucide-react";
import { openContractPdf } from "@/lib/pdf";
import SnapshotCard from "@/components/SnapshotCard";

const DAY_FILTERS = [
  { v: 0, l: "Alle" },
  { v: 14, l: "14 Tage" },
  { v: 30, l: "30 Tage" },
  { v: 60, l: "60 Tage" },
  { v: 90, l: "90 Tage" },
];

export default function PDFArchiv() {
  const [items, setItems] = useState([]);
  const [q, setQ] = useState("");
  const [days, setDays] = useState(0);
  const [loading, setLoading] = useState(true);
  const [gallery, setGallery] = useState(null); // {item, urls, index}

  const load = async () => {
    setLoading(true);
    try {
      const params = {};
      if (q) params.q = q;
      if (days) params.days = days;
      const { data } = await api.get("/contracts", { params });
      setItems(Array.isArray(data) ? data : []);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [days]);

  const openPdf = (id) => openContractPdf(id);

  const remove = async (id) => {
    if (!window.confirm("Vertrag wirklich löschen?")) return;
    await api.delete(`/contracts/${id}`);
    toast.success("Gelöscht");
    load();
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-6xl mx-auto" data-testid="pdfs-page">
      <div className="overline">Verträge / PDFs</div>
      <h1 className="font-display font-black text-4xl lg:text-5xl tracking-tighter mt-1">Vertragsarchiv</h1>

      {/* Suche + Zeitraum als Apple-Segmentleiste */}
      <div className="mt-8 flex flex-wrap gap-3 items-center">
        <div className="flex-1 min-w-[260px] flex items-center gap-3 apple-input !rounded-full !py-3 !px-5">
          <Search size={17} className="shrink-0" style={{ color: "var(--text-muted)" }} />
          <input data-testid="pdf-search-input" value={q} onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && load()}
                 placeholder="Suche nach Marke / Modell / Verkäufer"
                 className="bg-transparent outline-none w-full text-[15px]" />
        </div>
        <div className="flex items-center gap-1 rounded-full p-1"
             style={{ background: "var(--apple-btn-secondary-bg)" }}>
          {DAY_FILTERS.map((d) => (
            <button key={d.v} onClick={() => setDays(d.v)} data-testid={`filter-days-${d.v}`}
                    className={`px-4 py-2 rounded-full text-[13px] font-semibold transition-colors ${
                      days === d.v ? "bg-white/15 text-white shadow-sm" : "hover:text-white"
                    }`}
                    style={days === d.v ? {} : { color: "var(--text-muted)" }}>
              {d.l}
            </button>
          ))}
        </div>
        <button onClick={load}
                className="apple-btn apple-btn-secondary !rounded-full !px-5 !py-2.5">
          Aktualisieren
        </button>
      </div>

      {/* Vertragsliste als grosse Apple-Karten */}
      <div className="mt-8 space-y-4" data-testid="pdfs-table">
        {loading && (
          <div className="apple-surface p-12 text-center text-[15px]"
               style={{ color: "var(--text-muted)" }}>Lade…</div>
        )}
        {!loading && items.length === 0 && (
          <div className="apple-surface p-12 text-center text-[15px]"
               style={{ color: "var(--text-muted)" }}>Noch keine Verträge erstellt.</div>
        )}
        {items.map((it) => (
          <div key={it.id}
               className="apple-surface p-5 sm:p-6 transition-colors hover:border-white/20"
               data-testid={`pdf-card-${it.id}`}>
            <div className="flex flex-col sm:flex-row sm:items-center gap-5">
              {/* Grosses Fahrzeugbild */}
              <VehicleThumb
                item={it}
                onOpen={(urls, index) => setGallery({ item: it, urls, index })}
              />

              {/* Hauptbereich */}
              <div className="flex-1 min-w-0">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-3 flex-wrap">
                      <span className="font-display font-bold text-xl tracking-tight truncate">
                        {it.make || "—"} {it.model || ""}
                      </span>
                      <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full"
                            style={{ background: "rgba(52,199,89,0.12)",
                                     border: "1px solid rgba(52,199,89,0.25)",
                                     color: "var(--accent-green)" }}>
                        {it.status}
                      </span>
                    </div>
                    <div className="mt-1.5 text-[13.5px] leading-relaxed"
                         style={{ color: "var(--text-secondary)" }}>
                      {it.seller_name}
                      {it.pickup_date && <> · Abholung {it.pickup_date}</>}
                      <span style={{ color: "var(--text-muted)" }}>
                        {" "}· erstellt {new Date(it.created_at).toLocaleString("de-DE",
                          { day: "2-digit", month: "2-digit", year: "numeric",
                            hour: "2-digit", minute: "2-digit" })}
                        {it.created_by_name && <> von{" "}
                          <span className={it.created_by_role === "sucher" ? "text-sky-400" : ""}>
                            {it.created_by_name}
                          </span></>}
                      </span>
                    </div>
                    <div className="mt-3">
                      {it.vehicle_id ? (
                        <SnapshotCard vehicleId={it.vehicle_id} compact />
                      ) : (
                        <span className="text-xs" style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </div>
                  </div>

                  {/* Preis + Aktionen rechts */}
                  <div className="shrink-0 flex flex-col items-end gap-3">
                    <div className="font-display font-black text-2xl tracking-tight whitespace-nowrap">
                      {it.purchase_price
                        ? `${Number(it.purchase_price).toLocaleString("de-DE")} €` : "—"}
                    </div>
                    <div className="flex items-center gap-2">
                      <button onClick={() => openPdf(it.id)} data-testid={`open-pdf-${it.id}`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-white/10"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-primary)" }}
                              title="Vertrag öffnen">
                        <Eye size={16} />
                      </button>
                      <button onClick={() => remove(it.id)} data-testid={`del-pdf-${it.id}`}
                              className="w-10 h-10 rounded-full flex items-center justify-center transition-colors hover:bg-red-500/20"
                              style={{ background: "var(--apple-btn-secondary-bg)",
                                       color: "var(--text-secondary)" }}
                              title="Löschen">
                        <Trash2 size={16} />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Foto-Galerie: großes Bild, blättern mit Pfeilen / Tastatur / Wischen */}
      {gallery && (
        <GalleryViewer
          item={gallery.item}
          urls={gallery.urls}
          startIndex={gallery.index || 0}
          onClose={() => setGallery(null)}
        />
      )}
    </div>
  );
}

/* ------------------------------ Sub-components ----------------------------- */

function VehicleThumb({ item, onOpen }) {
  // Foto-URLs kommen normalerweise direkt mit der Vertragsliste
  // (vehicle_image_urls). Fehlen sie (z.B. sehr alte Verträge, deren
  // Fahrzeug inzwischen gelöscht ist), einmal still beim Fahrzeug nachsehen.
  const [urls, setUrls] = useState(item.vehicle_image_urls || []);

  useEffect(() => {
    let aktiv = true;
    setUrls(item.vehicle_image_urls || []);
    if ((item.vehicle_image_urls || []).length === 0 && item.vehicle_id) {
      api.get(`/vehicles/${item.vehicle_id}`)
        .then(({ data }) => {
          const d = data?.data || data || {};
          const fb = (d.image_urls || d.images || [])
            .filter((u) => typeof u === "string" && u.startsWith("http"));
          if (aktiv && fb.length > 0) setUrls(fb);
        })
        .catch(() => {});
    }
    return () => { aktiv = false; };
  }, [item]);

  if (urls.length === 0) {
    return (
      <div
        className="h-24 w-full sm:w-36 shrink-0 rounded-xl flex items-center justify-center"
        style={{ background: "rgba(255,255,255,0.03)",
                 border: "1px solid var(--border-default)",
                 color: "var(--text-muted)" }}
        title="Keine Fotos zum Inserat gespeichert"
      >
        <Car size={26} />
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(urls, 0)}
      className="relative shrink-0 group w-full sm:w-36"
      data-testid={`photos-open-${item.id}`}
      title={`${urls.length} Fotos ansehen`}
    >
      <img
        src={urls[0]}
        alt=""
        className="h-40 sm:h-24 w-full object-cover rounded-xl transition-transform duration-200 group-hover:scale-[1.03]"
        style={{ border: "1px solid var(--border-default)" }}
        loading="lazy"
      />
      {urls.length > 1 && (
        <span
          className="absolute bottom-1.5 right-1.5 inline-flex items-center justify-center rounded-full bg-black/75 backdrop-blur px-2 py-0.5 text-[11px] font-semibold text-white"
        >
          {urls.length} Fotos
        </span>
      )}
    </button>
  );
}

function GalleryViewer({ item, urls, startIndex = 0, onClose }) {
  const [index, setIndex] = useState(Math.min(startIndex, urls.length - 1));
  const title = `${item.make || ""} ${item.model || ""}`.trim() || "Fahrzeugfotos";

  const prev = useCallback(
    () => setIndex((i) => (i - 1 + urls.length) % urls.length), [urls.length]);
  const next = useCallback(
    () => setIndex((i) => (i + 1) % urls.length), [urls.length]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, prev, next]);

  // Wisch-Geste (Touch / Trackpad-Drag)
  const [touchX, setTouchX] = useState(null);
  const onTouchStart = (e) => setTouchX(e.touches[0].clientX);
  const onTouchEnd = (e) => {
    if (touchX == null) return;
    const dx = e.changedTouches[0].clientX - touchX;
    if (Math.abs(dx) > 50) (dx < 0 ? next() : prev());
    setTouchX(null);
  };

  return (
    <div
      className="fixed inset-0 z-[70] flex flex-col"
      style={{ background: "rgba(0,0,0,0.94)", backdropFilter: "blur(14px)" }}
      onClick={onClose}
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      data-testid="photos-gallery"
    >
      {/* Kopfzeile */}
      <div
        className="flex items-center justify-between gap-3 px-4 sm:px-6 py-3"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="min-w-0">
          <div className="overline">Fotos vom Inserat</div>
          <div className="font-display font-bold text-lg tracking-tight truncate">
            {title} <span className="text-zinc-500 font-normal">· {index + 1} / {urls.length}</span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="w-10 h-10 shrink-0 rounded-full flex items-center justify-center text-white hover:bg-white/10"
          data-testid="photos-gallery-close"
          aria-label="Schließen"
        >
          <X size={20} />
        </button>
      </div>

      {/* Großes Bild + Pfeile */}
      <div className="relative flex-1 min-h-0 flex items-center justify-center px-14 sm:px-20">
        {urls.length > 1 && (
          <>
            <button
              onClick={(e) => { e.stopPropagation(); prev(); }}
              data-testid="photos-gallery-prev"
              className="absolute left-3 sm:left-6 w-11 h-11 rounded-full flex items-center justify-center text-white bg-white/5 hover:bg-white/15 backdrop-blur"
              aria-label="Vorheriges Foto"
            >
              <ChevronLeft size={24} />
            </button>
            <button
              onClick={(e) => { e.stopPropagation(); next(); }}
              data-testid="photos-gallery-next"
              className="absolute right-3 sm:right-6 w-11 h-11 rounded-full flex items-center justify-center text-white bg-white/5 hover:bg-white/15 backdrop-blur"
              aria-label="Nächstes Foto"
            >
              <ChevronRight size={24} />
            </button>
          </>
        )}
        <img
          src={urls[index]}
          alt=""
          onClick={(e) => e.stopPropagation()}
          className="max-w-full max-h-full object-contain select-none rounded-md"
          draggable={false}
          data-testid="photos-gallery-image"
        />
      </div>

      {/* Filmstreifen zum direkten Anspringen */}
      {urls.length > 1 && (
        <div
          className="px-4 sm:px-6 py-3 flex gap-2 overflow-x-auto justify-start sm:justify-center"
          onClick={(e) => e.stopPropagation()}
        >
          {urls.map((u, i) => (
            <button
              key={`${u}-${i}`}
              onClick={() => setIndex(i)}
              data-testid={`photos-gallery-thumb-${i}`}
              className={`h-14 w-20 shrink-0 rounded-md overflow-hidden border-2 transition-opacity ${
                i === index ? "border-white opacity-100" : "border-transparent opacity-50 hover:opacity-90"
              }`}
            >
              <img src={u} alt="" loading="lazy" className="h-full w-full object-cover" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
