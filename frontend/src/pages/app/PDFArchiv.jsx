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
      setItems(data);
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
    <div className="p-3 sm:p-6 lg:p-10 max-w-7xl mx-auto" data-testid="pdfs-page">
      <div className="overline">Verträge / PDFs</div>
      <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">Vertragsarchiv</h1>

      <div className="mt-6 flex flex-wrap gap-3 items-center">
        <div className="flex-1 min-w-[220px] flex items-center gap-2 input-base">
          <Search size={15} className="text-zinc-500" />
          <input data-testid="pdf-search-input" value={q} onChange={(e) => setQ(e.target.value)}
                 onKeyDown={(e) => e.key === "Enter" && load()}
                 placeholder="Suche nach Marke / Modell / Verkäufer"
                 className="bg-transparent outline-none w-full text-sm" />
        </div>
        <div className="flex gap-1">
          {DAY_FILTERS.map((d) => (
            <button key={d.v} onClick={() => setDays(d.v)} data-testid={`filter-days-${d.v}`}
                    className={`px-3 py-2 rounded-sm text-xs border transition-colors ${
                      days === d.v ? "bg-white/10 text-white" : "text-zinc-400 hover:text-white"
                    }`}
                    style={{ borderColor: "var(--border-default)" }}>
              {d.l}
            </button>
          ))}
        </div>
        <button onClick={load} className="px-4 py-2 rounded-sm border text-sm hover:bg-white/5"
                style={{ borderColor: "var(--border-default)" }}>
          Aktualisieren
        </button>
      </div>

      <div className="mt-6 tactical-card overflow-hidden">
        <table className="w-full text-sm" data-testid="pdfs-table">
          <thead>
            <tr className="text-left overline" style={{ background: "rgba(255,255,255,0.02)" }}>
              <th className="px-4 py-3">Datum</th>
              <th className="px-4 py-3">Fahrzeug</th>
              <th className="px-4 py-3">Verkäufer</th>
              <th className="px-4 py-3">Abholung</th>
              <th className="px-4 py-3">Preis</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Beweis</th>
              <th className="px-4 py-3 text-right">Aktion</th>
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr><td colSpan={8} className="px-4 py-10 text-center text-zinc-500">Lade…</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={8} className="px-4 py-10 text-center text-zinc-500">Noch keine Verträge erstellt.</td></tr>
            )}
            {items.map((it) => (
              <tr key={it.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                <td className="px-4 py-3 text-zinc-400 font-mono text-xs">
                  {new Date(it.created_at).toLocaleString("de-DE")}
                  {it.created_by_name && (
                    <div className="mt-0.5 font-sans text-[10px] text-zinc-500">
                      von <span className={it.created_by_role === "sucher" ? "text-sky-400" : "text-zinc-400"}>{it.created_by_name}</span>
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <VehicleCell
                    item={it}
                    onOpen={(urls, index) => setGallery({ item: it, urls, index })}
                  />
                </td>
                <td className="px-4 py-3 text-zinc-300">{it.seller_name}</td>
                <td className="px-4 py-3 text-zinc-400">{it.pickup_date || "—"}</td>
                <td className="px-4 py-3 font-semibold">{it.purchase_price ? `${Number(it.purchase_price).toLocaleString("de-DE")} €` : "—"}</td>
                <td className="px-4 py-3">
                  <span className="text-xs px-2 py-0.5 rounded-sm border"
                        style={{ borderColor: "var(--border-default)", color: "var(--accent-green)" }}>
                    {it.status}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {it.vehicle_id ? (
                    <SnapshotCard vehicleId={it.vehicle_id} compact />
                  ) : (
                    <span className="text-xs text-zinc-500">—</span>
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="inline-flex items-center gap-1">
                    <button onClick={() => openPdf(it.id)} data-testid={`open-pdf-${it.id}`}
                            className="p-2 rounded-sm hover:bg-white/5 text-zinc-300" title="PDF öffnen">
                      <Eye size={14} />
                    </button>
                    <button onClick={() => remove(it.id)} data-testid={`del-pdf-${it.id}`}
                            className="p-2 rounded-sm hover:bg-white/5 text-zinc-300" title="Löschen">
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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

function VehicleCell({ item, onOpen }) {
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

  const name = (
    <div className="min-w-0">
      <div className="font-semibold truncate">{textOr(item.make)}</div>
      <div className="text-xs text-zinc-500 truncate">{textOr(item.model)}</div>
    </div>
  );

  if (urls.length === 0) {
    return (
      <div className="flex items-center gap-3">
        <div
          className="h-12 w-16 shrink-0 rounded-md border flex items-center justify-center text-zinc-600 bg-white/[0.02]"
          style={{ borderColor: "var(--border-default)" }}
          title="Keine Fotos zum Inserat gespeichert"
        >
          <Car size={18} />
        </div>
        {name}
      </div>
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(urls, 0)}
      className="flex items-center gap-3 group text-left"
      data-testid={`photos-open-${item.id}`}
      title={`${urls.length} Fotos ansehen`}
    >
      <span className="relative shrink-0">
        <img
          src={urls[0]}
          alt=""
          className="h-12 w-16 object-cover rounded-md border transition-transform group-hover:scale-105"
          style={{ borderColor: "var(--border-default)" }}
          loading="lazy"
        />
        {urls.length > 1 && (
          <span
            className="absolute -bottom-1.5 -right-1.5 inline-flex items-center justify-center rounded-full bg-black/85 px-1.5 py-0.5 text-[10px] font-semibold text-white border"
            style={{ borderColor: "var(--border-default)" }}
          >
            {urls.length}
          </span>
        )}
      </span>
      {name}
    </button>
  );
}

function textOr(v) { return v || "—"; }

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
