import { useEffect, useState } from "react";
import { api, openAuthedFile } from "@/lib/api";
import { toast } from "sonner";
import { ImageOff } from "lucide-react";

/**
 * Vorschaubild eines Fahrerfotos (Runde 21).
 *
 * Fahrerfotos sind geschuetzt (nur Chef und der zustaendige Sucher) und
 * werden deshalb mit Anmeldung geladen, nicht ueber eine offene Adresse.
 * Vorher gab es in der Akte nur einen kleinen Text-Link "Foto"; jetzt sieht
 * man das Bild direkt, ein Klick oeffnet es gross in einem neuen Tab.
 */
export default function AbholFoto({ photoKey, label = "", size = 64 }) {
  const [url, setUrl] = useState(null);
  const [fehler, setFehler] = useState(false);

  useEffect(() => {
    if (!photoKey) return undefined;
    let aktiv = true;
    let objUrl = null;
    setUrl(null);
    setFehler(false);
    api.get(`/pickup-fotos/${photoKey}`, { responseType: "blob" })
      .then((r) => {
        if (!aktiv) return;
        objUrl = URL.createObjectURL(r.data);
        setUrl(objUrl);
      })
      .catch(() => { if (aktiv) setFehler(true); });
    return () => {
      aktiv = false;
      if (objUrl) URL.revokeObjectURL(objUrl);
    };
  }, [photoKey]);

  const gross = (e) => {
    e?.stopPropagation?.();
    openAuthedFile(`/pickup-fotos/${photoKey}`, "image/jpeg")
      .catch(() => toast.error("Foto konnte nicht geladen werden"));
  };

  const box = { width: size, height: size };
  if (fehler) {
    return (
      <span className="inline-flex items-center justify-center rounded-md border text-zinc-500"
            style={{ ...box, borderColor: "var(--border-default)" }}
            title="Foto nicht mehr verfügbar" data-testid="abholfoto-fehlt">
        <ImageOff size={Math.max(14, Math.round(size / 3))} />
      </span>
    );
  }
  return (
    <button type="button" onClick={gross} title={label ? `${label} — groß öffnen` : "Foto groß öffnen"}
            className="inline-block rounded-md overflow-hidden border hover:opacity-90 align-middle"
            style={{ ...box, borderColor: "var(--border-default)", background: "rgba(255,255,255,0.04)" }}
            data-testid="abholfoto">
      {url
        ? <img src={url} alt={label} className="w-full h-full object-cover" draggable={false} />
        : <span className="block w-full h-full animate-pulse" />}
    </button>
  );
}
