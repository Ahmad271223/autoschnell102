import { useEffect, useState } from "react";

// Pruefbericht 20.09.2026 (K-11): "Lade…" stand ohne Ausweg, wenn eine
// Seite nicht kam. Ab LANGE_MS erscheint ein Hinweis mit "Neu laden".
export const LANGE_MS = 10000;

// Anzeige, solange eine nachgeladene Seite kommt (Vite, 09/2026). Dieselbe
// Anzeige wie in ProtectedRoute — sofort, damit kein leerer Zwischenzustand
// sichtbar wird. Innerhalb der Layouts bleiben Seitenleiste und Kopf stehen.
export default function SeiteLaedt({ ganzeSeite = false }) {
  const [lange, setLange] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setLange(true), LANGE_MS);
    return () => clearTimeout(t);
  }, []);
  return (
    <div className={`${ganzeSeite ? "min-h-screen" : "min-h-[40vh]"} flex flex-col items-center justify-center gap-3 text-zinc-500`}
         data-testid="seite-laedt">
      <span>Lade…</span>
      {lange && (
        <>
          <span className="text-[13px] text-center px-6" data-testid="seite-laedt-lange">
            Das dauert ungewöhnlich lange – bitte die Verbindung prüfen oder neu laden.
          </span>
          <button type="button" className="apple-btn apple-btn-primary" data-testid="seite-laedt-neu"
                  onClick={() => window.location.reload()}>
            Neu laden
          </button>
        </>
      )}
    </div>
  );
}
