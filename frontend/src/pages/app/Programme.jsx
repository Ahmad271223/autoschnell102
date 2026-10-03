import { useState } from "react";
import { Navigate } from "react-router-dom";
import { Download, Monitor } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { startseite } from "@/lib/rollen";
import { groesseText, useProgramme } from "@/lib/programme";

/**
 * Programme zum Herunterladen (03.10.2026). Was hier steht, liefert der Server —
 * nur für Firmen, die ein Programm freigeschaltet haben. Alle anderen landen auf
 * ihrer Startseite, als gäbe es die Seite nicht.
 */
export default function Programme() {
  const { user } = useAuth();
  const liste = useProgramme(Boolean(user) && (user.role === "dealer" || user.role === "sucher"), user?.id);
  const [laedt, setLaedt] = useState("");

  if (liste === null) return <div className="text-sm text-zinc-500">Lade…</div>;
  if (liste.length === 0) return <Navigate to={startseite(user)} replace />;

  const herunterladen = async (p) => {
    setLaedt(p.id);
    try {
      const { data } = await api.get(`/werkzeuge/${encodeURIComponent(p.id)}/download`, { responseType: "blob" });
      const url = URL.createObjectURL(data);
      const a = document.createElement("a");
      a.href = url;
      a.download = p.dateiname || "programm.exe";
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch {
      toast.error("Herunterladen hat nicht geklappt – bitte später noch einmal versuchen.");
    } finally {
      setLaedt("");
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6" data-testid="programme-seite">
      {liste.map((p) => (
        <section key={p.id} className="space-y-4" data-testid={`programm-${p.id}`}>
          <div className="flex items-start gap-3">
            <span className="w-10 h-10 rounded-md flex items-center justify-center shrink-0"
                  style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)" }}>
              <Monitor size={20} />
            </span>
            <div>
              <h1 className="font-display font-black text-2xl tracking-tight">{p.name}</h1>
              {p.beschreibung && <p className="text-sm text-zinc-500 mt-1">{p.beschreibung}</p>}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => herunterladen(p)}
              disabled={!p.vorhanden || laedt === p.id}
              data-testid={`programm-download-${p.id}`}
              className="apple-btn apple-btn-primary !rounded-full !px-5 !py-2.5 text-sm inline-flex items-center gap-2 disabled:opacity-50"
            >
              <Download size={16} />
              {laedt === p.id ? "Wird geladen…" : "Für Windows herunterladen"}
            </button>
            <span className="text-xs text-zinc-500" data-testid={`programm-info-${p.id}`}>
              {p.vorhanden
                ? [p.version && `Version ${p.version}`, groesseText(p.groesse)].filter(Boolean).join(" · ")
                : "Wird gerade bereitgestellt – bitte später noch einmal vorbeischauen."}
            </span>
          </div>

          {Array.isArray(p.schritte) && p.schritte.length > 0 && (
            <ol className="list-decimal pl-5 space-y-2 text-sm">
              {p.schritte.map((s) => <li key={s}>{s}</li>)}
            </ol>
          )}
        </section>
      ))}
    </div>
  );
}
