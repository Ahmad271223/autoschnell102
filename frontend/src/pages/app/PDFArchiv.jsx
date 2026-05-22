import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { Search, Trash2, Eye, ImageIcon, X } from "lucide-react";
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
  const [photoView, setPhotoView] = useState(null); // {item, urls}
  const [lightbox, setLightbox] = useState(null);   // url string

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
              <th className="px-4 py-3">Fotos</th>
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
              <tr><td colSpan={9} className="px-4 py-10 text-center text-zinc-500">Lade…</td></tr>
            )}
            {!loading && items.length === 0 && (
              <tr><td colSpan={9} className="px-4 py-10 text-center text-zinc-500">Noch keine Verträge erstellt.</td></tr>
            )}
            {items.map((it) => (
              <tr key={it.id} className="border-t" style={{ borderColor: "var(--border-default)" }}>
                <td className="px-4 py-3 text-zinc-400 font-mono text-xs">{new Date(it.created_at).toLocaleString("de-DE")}</td>
                <td className="px-4 py-3"><div className="font-semibold">{it.make}</div><div className="text-xs text-zinc-500">{it.model}</div></td>
                <td className="px-4 py-3">
                  <PhotoCell
                    item={it}
                    onOpen={(urls) => setPhotoView({ item: it, urls })}
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

      {/* Photos gallery modal */}
      {photoView && (
        <PhotosModal
          item={photoView.item}
          urls={photoView.urls}
          onClose={() => setPhotoView(null)}
          onZoom={(u) => setLightbox(u)}
        />
      )}
      {lightbox && (
        <Lightbox url={lightbox} onClose={() => setLightbox(null)} />
      )}
    </div>
  );
}

/* ------------------------------ Sub-components ----------------------------- */

function PhotoCell({ item, onOpen }) {
  // Try cached vehicle as fallback if contract has no snapshot URLs.
  const [urls, setUrls] = useState(item.vehicle_image_urls || []);
  const [tried, setTried] = useState(false);

  const ensureLoaded = async () => {
    if (urls.length > 0 || tried) return urls;
    setTried(true);
    if (item.vehicle_id) {
      try {
        const { data } = await api.get(`/vehicles/${item.vehicle_id}`);
        // Vehicle endpoint returns {id, dealer_id, data: {…vehicle fields}}.
        const fb = data?.data?.image_urls || data?.image_urls || [];
        setUrls(fb);
        return fb;
      } catch {
        return [];
      }
    }
    return [];
  };

  if (urls.length === 0 && tried) {
    return <span className="text-xs text-zinc-500">—</span>;
  }
  if (urls.length === 0) {
    return (
      <button
        type="button"
        onClick={async () => {
          const got = await ensureLoaded();
          if (got.length > 0) onOpen(got);
        }}
        className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-white"
        data-testid={`photos-load-${item.id}`}
        title="Fotos laden"
      >
        <ImageIcon size={14} /> laden
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpen(urls)}
      className="inline-flex items-center gap-2 group"
      data-testid={`photos-open-${item.id}`}
      title={`${urls.length} Fotos ansehen`}
    >
      <span className="relative inline-flex items-center">
        <img
          src={urls[0]}
          alt=""
          className="h-10 w-14 object-cover rounded-sm border transition-transform group-hover:scale-105"
          style={{ borderColor: "var(--border-default)" }}
          loading="lazy"
        />
        {urls.length > 1 && (
          <span
            className="absolute -bottom-1 -right-1 inline-flex items-center justify-center rounded-full bg-black/80 px-1.5 py-0.5 text-[10px] font-semibold text-white border"
            style={{ borderColor: "var(--border-default)" }}
          >
            {urls.length}
          </span>
        )}
      </span>
    </button>
  );
}

function PhotosModal({ item, urls, onClose, onZoom }) {
  const title = `${item.make || ""} ${item.model || ""}`.trim() || "Fahrzeugfotos";
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 apple-modal-backdrop"
      onClick={onClose}
      data-testid="photos-modal"
    >
      <div
        className="relative w-full max-w-5xl max-h-[88vh] overflow-y-auto rounded-xl border bg-[#0e0e10] p-5"
        style={{ borderColor: "var(--border-default)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 mb-4">
          <div>
            <div className="overline">Fotos vom Inserat</div>
            <h2 className="font-display font-bold text-xl tracking-tight">
              {title} <span className="text-zinc-500 font-normal">({urls.length})</span>
            </h2>
            {item.created_at && (
              <div className="text-[11px] text-zinc-500 mt-0.5">
                Vertrag erstellt: {new Date(item.created_at).toLocaleString("de-DE")}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-sm hover:bg-white/5 text-zinc-300"
            data-testid="photos-modal-close"
            aria-label="Schließen"
          >
            <X size={16} />
          </button>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
          {urls.map((u, i) => (
            <button
              key={`${u}-${i}`}
              type="button"
              onClick={() => onZoom(u)}
              className="group relative overflow-hidden rounded-md border bg-black/30 aspect-[4/3]"
              style={{ borderColor: "var(--border-default)" }}
              data-testid={`photos-thumb-${i}`}
            >
              <img
                src={u}
                alt=""
                loading="lazy"
                className="absolute inset-0 h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.04]"
              />
            </button>
          ))}
        </div>

        <div className="text-[11px] text-zinc-500 mt-4">
          Hinweis: Die Foto-URLs verweisen auf das Original-Inserat. Falls das
          Inserat zwischenzeitlich gelöscht wurde, können einzelne Bilder nicht
          mehr geladen werden.
        </div>
      </div>
    </div>
  );
}

function Lightbox({ url, onClose }) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-6 bg-black/90"
      onClick={onClose}
      data-testid="photos-lightbox"
    >
      <button
        onClick={onClose}
        className="absolute top-4 right-4 p-2 rounded-sm bg-black/40 hover:bg-white/10 text-white"
        aria-label="Schließen"
      >
        <X size={18} />
      </button>
      <img
        src={url}
        alt=""
        className="max-h-[92vh] max-w-[92vw] object-contain rounded-md shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      />
    </div>
  );
}
