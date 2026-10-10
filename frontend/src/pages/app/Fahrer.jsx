import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";
import { Plus, Trash2, Copy, User, Mail, KeyRound, Phone } from "lucide-react";

/**
 * Rollenprüfung 22.09.2026 (RP-041/RP-140): Rückfrage beim Entfernen nennt,
 * wie viele offene Fahrten danach ohne Fahrer dastehen (der Server trennt sie
 * alle). `offene` kommt aus GET /drivers (nur für den Hauptchef).
 */
export function entfernenRueckfrage(name, offene) {
  const wer = name ? `„${name}“` : "Diesen Fahrer";
  if (typeof offene !== "number") return `${wer} aus deiner Liste entfernen?`;
  if (offene === 0) return `${wer} aus deiner Liste entfernen?\n\nEr hat keine offenen Fahrten.`;
  return `${wer} aus deiner Liste entfernen?\n\n${offene === 1 ? "1 offene Fahrt verliert"
    : `${offene} offene Fahrten verlieren`} dabei den Fahrer und ${offene === 1 ? "muss" : "müssen"} `
    + "im Terminplaner neu zugeteilt werden.";
}

export default function Fahrer() {
  const { user } = useAuth();
  const navigate = useNavigate();
  // Fahrer hinzufuegen/entfernen ist Chefsache (Backend erzwingt 403);
  // Sucher sehen die Liste nur, um Termine zuweisen zu koennen.
  const chef = user?.role === "dealer";
  const [items, setItems] = useState([]);
  const [code, setCode] = useState("");
  const [loading, setLoading] = useState(false);
  // Pruefbericht 20.09.2026 (R1-40/U-167): Ladefehler wurden verschluckt —
  // die Seite sagte "Noch keine Fahrer", obwohl welche verknuepft waren.
  const [ladeZustand, setLadeZustand] = useState("laedt");
  const [ladeFehler, setLadeFehler] = useState("");
  // Prüfbericht 20.09. V-33/U-169: der Server kappt bei 500 Verknüpfungen und
  // meldet das per X-Truncated — vorher stand die Liste als vollständig da.
  const [gekuerzt, setGekuerzt] = useState(false);

  const load = () => api.get("/drivers")
    .then((r) => {
      setItems(Array.isArray(r.data) ? r.data : []);
      setGekuerzt(String(r.headers?.["x-truncated"] || "") === "1");
      setLadeZustand("ok");
      setLadeFehler("");
    })
    .catch((e) => { setLadeZustand("fehler"); setLadeFehler(errMsg(e, "Fahrer konnten nicht geladen werden")); });
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    const c = code.trim().toUpperCase();
    if (!c) return;
    setLoading(true);
    try {
      await api.post("/drivers/add", { driver_code: c });
      toast.success("Fahrer hinzugefügt");
      setCode("");
      load();
    } catch (err) {
      toast.error(errMsg(err, "Fahrer konnte nicht hinzugefügt werden"));
    } finally {
      setLoading(false);
    }
  };

  // R1-41: Fehler beim Entfernen (z. B. Fahrer hat offene Fahrten) wurden
  // verschluckt — kein Hinweis, keine Aktualisierung.
  const [entfernt, setEntfernt] = useState("");
  const remove = async (id) => {
    if (entfernt) return;
    const fahrer = items.find((d) => d.id === id);
    if (!window.confirm(entfernenRueckfrage(fahrer?.name, fahrer?.offene_fahrten))) return;
    setEntfernt(id);
    try {
      const { data } = await api.delete(`/drivers/${id}`);
      // Pruefbericht 20.09.2026 (U-160): "Termine konnten nicht vollstaendig
      // bereinigt werden" kam als Hinweis zurueck und ging verloren.
      if (data?.hinweis) toast.warning(data.hinweis, { duration: 10000 });
      else if (data?.offene_termine_getrennt > 0) {
        // RP-041/RP-140: sagen, dass Fahrten jetzt ohne Fahrer sind — mit Weg
        // zum Terminplaner (vorher nur "Entfernt").
        const n = data.offene_termine_getrennt;
        toast.warning(`Fahrer entfernt — ${n === 1 ? "1 offene Fahrt ist" : `${n} offene Fahrten sind`} `
                      + "jetzt ohne Fahrer. Bitte im Terminplaner neu zuteilen.", {
          duration: 15000,
          action: { label: "Zum Terminplaner", onClick: () => navigate("/app/termine") },
        });
      } else toast.success("Entfernt");
    } catch (e) {
      toast.error(errMsg(e, "Fahrer konnte nicht entfernt werden"));
    } finally {
      setEntfernt("");
      load();
    }
  };

  // U-166: Kopieren kann scheitern (keine Berechtigung, alter Browser).
  const copy = async (c) => {
    try { await navigator.clipboard.writeText(c); toast.success("Kopiert"); }
    catch { toast.error(`Kopieren nicht möglich — bitte von Hand abschreiben: ${c}`); }
  };

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-5xl mx-auto" data-testid="drivers-page">
      <div className="overline">Fahrer</div>
      <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
        Fahrer-Verwaltung
      </h1>
      <p className="text-sm text-zinc-400 mt-2 max-w-2xl">
        Fahrer legt der Betreiber an – jeder Fahrer hat ein eigenes Konto in der
        Fahrer-App und eine Fahrer-ID (z.B. <code className="px-1 rounded-sm bg-white/5">FD-A7K3M9X2</code>).
        Die Fahrer-ID bekommst du vom Fahrer oder Betreiber – damit fügst du ihn hier hinzu.
      </p>

      {!chef && (
        <div className="tactical-card p-4 mt-6 text-sm text-zinc-400" data-testid="drivers-readonly-hint">
          Fahrer hinzufügen oder entfernen kann nur der Händler-Hauptaccount. Du kannst
          die Fahrer hier einsehen und ihnen im Terminplaner Abholungen zuweisen.
        </div>
      )}
      {chef && (
      <form onSubmit={add} className="tactical-card p-5 mt-6 flex flex-col sm:flex-row gap-3 items-end">
        <div className="flex-1 w-full">
          <label className="text-xs text-zinc-400 flex items-center gap-2">
            <KeyRound size={11} /> Fahrer-ID
          </label>
          <input data-testid="driver-code-input" value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            placeholder="FD-XXXXXXXX"
            className="input-base w-full mt-1 font-mono tracking-[0.1em]" />
        </div>
        <button type="submit" data-testid="add-driver-btn" disabled={loading}
          className="kinetic-button px-5 py-2.5 rounded-sm font-bold flex items-center justify-center gap-2 w-full sm:w-auto disabled:opacity-50">
          <Plus size={15} /> Hinzufügen
        </button>
      </form>
      )}

      {gekuerzt && (
        <div className="mt-6 rounded-sm border px-4 py-2 text-sm" data-testid="drivers-gekuerzt"
             style={{ borderColor: "var(--border-default)", color: "var(--text-muted)" }}>
          Die Liste ist gekürzt — es werden nur 500 Fahrer angezeigt.
        </div>
      )}

      {/* RP-041/RP-140: am Handy quer scrollbar statt abgeschnitten — vorher
          lag die Spalte "Aktion" (Entfernen) bei langen E-Mails außerhalb. */}
      <div className={`${gekuerzt ? "mt-3" : "mt-6"} tactical-card overflow-x-auto`} data-testid="drivers-tabelle">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="text-left overline" style={{ background: "var(--wa-02)" }}>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Fahrer-ID</th>
              <th className="px-4 py-3">E-Mail</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3 text-right">Aktion</th>
            </tr>
          </thead>
          <tbody>
            {ladeZustand === "fehler" && (
              <tr><td colSpan={5} className="px-4 py-6 text-center" role="alert" data-testid="drivers-ladefehler">
                <span style={{ color: "var(--text-primary)" }}>{ladeFehler}</span>{" "}
                <button type="button" onClick={load} className="underline underline-offset-2 font-semibold">
                  Erneut versuchen
                </button>
              </td></tr>
            )}
            {ladeZustand === "laedt" && items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-zinc-500">lade…</td></tr>
            )}
            {ladeZustand === "ok" && items.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-10 text-center text-zinc-500">
                <User size={22} className="mx-auto mb-2 opacity-50" />
                Noch keine Fahrer. Frag deine Fahrer nach ihrer Fahrer-ID.
              </td></tr>
            )}
            {items.map((d) => (
              <tr key={d.id} className="border-t" style={{ borderColor: "var(--border-default)" }}
                  data-testid={`driver-row-${d.id}`}>
                <td className="px-4 py-3">
                  <div className="font-semibold">{d.name}</div>
                  {/* RP-542: Telefonnummer des Fahrers (nur Hauptchef, vom Betreiber erfasst) */}
                  {d.phone && (
                    <a href={`tel:${String(d.phone).replace(/[^\d+]/g, "")}`} data-testid={`driver-phone-${d.id}`}
                       className="mt-0.5 inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-white">
                      <Phone size={11} />{d.phone}
                    </a>
                  )}
                  {chef && typeof d.offene_fahrten === "number" && d.offene_fahrten > 0 && (
                    <div className="text-[11px] text-zinc-500">
                      {d.offene_fahrten} offene {d.offene_fahrten === 1 ? "Fahrt" : "Fahrten"}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  {/* Sucher sehen keine Fahrer-ID und keine E-Mail (Nachpruefung 15.09.2026) */}
                  {d.driver_code ? (
                    <button onClick={() => copy(d.driver_code)}
                      className="inline-flex items-center gap-1.5 font-mono text-xs px-2 py-1 rounded-sm bg-white/5 hover:bg-white/10"
                      title="Kopieren">
                      {d.driver_code} <Copy size={10} />
                    </button>
                  ) : <span className="text-xs text-zinc-500">—</span>}
                </td>
                <td className="px-4 py-3 text-zinc-400 text-xs">
                  <span className="inline-flex items-center gap-1">
                    <Mail size={11} />{d.email || "—"}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className="text-xs px-2 py-0.5 rounded-sm"
                    style={{ background: d.active ? "rgba(52,199,89,0.12)" : "var(--wa-04)",
                             color: d.active ? "var(--accent-green)" : "var(--text-muted)" }}>
                    {d.active ? "aktiv" : "inaktiv"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right">
                  {chef ? (
                    <button onClick={() => remove(d.id)} data-testid={`del-driver-${d.id}`}
                      className="p-2 hover:bg-white/5 rounded-sm">
                      <Trash2 size={14} />
                    </button>
                  ) : (
                    <span className="text-xs text-zinc-600">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
