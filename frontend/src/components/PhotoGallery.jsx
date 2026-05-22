import { useEffect, useState, useCallback } from "react";
import { ChevronLeft, ChevronRight, X, Image as ImageIcon } from "lucide-react";

/**
 * Responsive Foto-Galerie mit Lightbox.
 * - Thumbs-Grid zum Durchklicken
 * - Fullscreen-Viewer mit ← / → (Tastatur + Buttons) + Swipe (touch)
 * - Schließen per ESC oder Klick auf Backdrop
 */
export default function PhotoGallery({ photos = [], label = "Fotos" }) {
  const [open, setOpen] = useState(false);
  const [index, setIndex] = useState(0);

  const prev = useCallback(() => setIndex((i) => (i - 1 + photos.length) % photos.length), [photos.length]);
  const next = useCallback(() => setIndex((i) => (i + 1) % photos.length), [photos.length]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") setOpen(false);
      if (e.key === "ArrowLeft") prev();
      if (e.key === "ArrowRight") next();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, prev, next]);

  // Touch-Swipe
  const [touchStartX, setTouchStartX] = useState(null);
  const onTouchStart = (e) => setTouchStartX(e.touches[0].clientX);
  const onTouchEnd = (e) => {
    if (touchStartX == null) return;
    const dx = e.changedTouches[0].clientX - touchStartX;
    if (Math.abs(dx) > 50) {
      if (dx < 0) next();
      else prev();
    }
    setTouchStartX(null);
  };

  if (!photos || photos.length === 0) return null;

  return (
    <div data-testid="photo-gallery">
      <div className="flex items-center gap-1.5 mb-2 text-[10px] uppercase tracking-[0.15em] text-zinc-400 font-semibold">
        <ImageIcon size={11} /> {label} · {photos.length}
      </div>
      <div className="grid grid-cols-4 sm:grid-cols-5 md:grid-cols-6 gap-1.5">
        {photos.slice(0, 12).map((src, i) => (
          <button
            key={i}
            onClick={(e) => { e.stopPropagation(); setIndex(i); setOpen(true); }}
            data-testid={`gallery-thumb-${i}`}
            className="aspect-square rounded-md overflow-hidden bg-zinc-800 relative group"
          >
            <img src={src} alt="" loading="lazy"
                 className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" />
            {i === 11 && photos.length > 12 && (
              <div className="absolute inset-0 flex items-center justify-center font-bold text-lg text-white"
                   style={{ background: "rgba(0,0,0,0.6)" }}>
                +{photos.length - 12}
              </div>
            )}
          </button>
        ))}
      </div>

      {open && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center"
          style={{ background: "rgba(0,0,0,0.94)", backdropFilter: "blur(18px)" }}
          onClick={() => setOpen(false)}
          onTouchStart={onTouchStart}
          onTouchEnd={onTouchEnd}
          data-testid="lightbox"
        >
          <button onClick={(e) => { e.stopPropagation(); setOpen(false); }}
                  data-testid="lightbox-close"
                  className="absolute top-4 right-4 w-11 h-11 rounded-full flex items-center justify-center text-white hover:bg-white/10">
            <X size={22} />
          </button>

          {photos.length > 1 && (
            <>
              <button onClick={(e) => { e.stopPropagation(); prev(); }}
                      data-testid="lightbox-prev"
                      className="absolute left-3 md:left-6 w-12 h-12 rounded-full flex items-center justify-center text-white bg-white/5 hover:bg-white/15 backdrop-blur">
                <ChevronLeft size={26} />
              </button>
              <button onClick={(e) => { e.stopPropagation(); next(); }}
                      data-testid="lightbox-next"
                      className="absolute right-3 md:right-6 w-12 h-12 rounded-full flex items-center justify-center text-white bg-white/5 hover:bg-white/15 backdrop-blur">
                <ChevronRight size={26} />
              </button>
            </>
          )}

          <img
            src={photos[index]}
            alt=""
            onClick={(e) => e.stopPropagation()}
            className="max-w-[92vw] max-h-[85vh] object-contain select-none"
            draggable={false}
          />

          <div className="absolute bottom-6 left-0 right-0 flex justify-center">
            <div className="px-3 py-1.5 rounded-full text-xs font-mono text-white/90"
                 style={{ background: "rgba(255,255,255,0.08)", backdropFilter: "blur(8px)" }}>
              {index + 1} / {photos.length}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
