import { useEffect, useState } from "react";
import { api, openAuthedFile } from "@/lib/api";
import { toast } from "sonner";
import { ImageOff, X } from "lucide-react";

/**
 * Vorschaubild eines Fahrerfotos (Runde 21).
 *
 * Fahrerfotos sind geschuetzt (nur Chef und der zustaendige Sucher) und
 * werden deshalb mit Anmeldung geladen, nicht ueber eine offene Adresse.
 * Vorher gab es in der Akte nur einen kleinen Text-Link "Foto"; jetzt sieht
 * man das Bild direkt, ein Klick oeffnet es gross in einem neuen Tab.
 *
 * Schadenfotos im Protokoll (Wunsch Ahmad 06.10.2026): `pfad` = API-Weg des Fotos, `client` = die
 * Anmeldung dazu (Fahrer-App: driverApi). Dann oeffnet ein Tipp das Foto gross in der App (auch am Handy).
 */
export default function AbholFoto({ photoKey, pfad, client, label = "", size = 64 }) {
  const quelle = pfad || (photoKey ? `/pickup-fotos/${photoKey}` : null);
  const [url, setUrl] = useState(null);
  const [fehler, setFehler] = useState(false);
  const [gross, setGross] = useState(false);

  useEffect(() => {
    if (!quelle) return undefined;
    let aktiv = true;
    let objUrl = null;
    setUrl(null);
    setFehler(false);
    (client || api).get(quelle, { responseType: "blob" })
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
  }, [quelle, client]);

  const oeffnen = (e) => {
    e?.stopPropagation?.();
    if (pfad) {
      if (url) setGross(true);
      return;
    }
    openAuthedFile(quelle, "image/jpeg")
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
    <>
      <button type="button" onClick={oeffnen} title={label ? `${label} — groß öffnen` : "Foto groß öffnen"}
              className="inline-block rounded-md overflow-hidden border hover:opacity-90 align-middle"
              style={{ ...box, borderColor: "var(--border-default)", background: "var(--wa-04)" }}
              data-testid="abholfoto">
        {url
          ? <img src={url} alt={label} className="w-full h-full object-cover" draggable={false} />
          : <span className="block w-full h-full animate-pulse" />}
      </button>
      {gross && url && (
        <div role="dialog" aria-label={label || "Foto"} data-testid="abholfoto-gross"
             className="fixed inset-0 z-[100] flex items-center justify-center p-3 bg-black/85"
             onClick={() => setGross(false)}>
          <img src={url} alt={label} className="max-w-full max-h-full object-contain rounded-md" draggable={false} />
          <button type="button" aria-label="Schließen" onClick={() => setGross(false)}
                  className="absolute top-3 right-3 w-11 h-11 rounded-full flex items-center justify-center text-white bg-black/60">
            <X size={20} />
          </button>
        </div>
      )}
    </>
  );
}
