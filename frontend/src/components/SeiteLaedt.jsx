// Anzeige, solange eine nachgeladene Seite kommt (Vite, 09/2026). Dieselbe
// Anzeige wie in ProtectedRoute — sofort, damit kein leerer Zwischenzustand
// sichtbar wird. Innerhalb der Layouts bleiben Seitenleiste und Kopf stehen.
export default function SeiteLaedt({ ganzeSeite = false }) {
  return (
    <div className={`${ganzeSeite ? "min-h-screen" : "min-h-[40vh]"} flex items-center justify-center text-zinc-500`}
         data-testid="seite-laedt">
      Lade…
    </div>
  );
}
