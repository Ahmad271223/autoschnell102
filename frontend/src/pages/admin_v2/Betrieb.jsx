import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Activity, AlertTriangle, Check, RefreshCw, ShieldAlert } from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";

/**
 * Betrieb (Audit 09/2026): alles, was frueher still scheiterte, ist hier
 * sichtbar — offene Betriebsalarme (bezahlt ohne Zugang, Datei nicht
 * loeschbar, Vertrag ohne dauerhaften Datensatz, Backup unvollstaendig),
 * die Loesch-Warteschlange, haengende Freischaltungs-Vorgaenge und das
 * letzte Backup. Nur Super-Admin.
 */
export default function AdminBetrieb() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/betrieb");
      setData(r.data);
    } catch (e) {
      toast.error(errMsg(e, "Betriebsdaten konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const quittieren = async (a) => {
    try {
      await api.post(`/admin/betrieb/alarme/${a.id}/quittieren`);
      toast.success("Alarm quittiert");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };
  const nachholen = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/betrieb/nachholen");
      toast.success(`Reparaturlauf: Abo-Vorgänge ${r.data?.abo_vorgaenge ?? 0}, Zahlungen ${JSON.stringify(r.data?.zahlungen || {})}`);
      load();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  if (loading && !data) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  if (!data) return <EmptyState title="Keine Betriebsdaten" />;
  const alarme = data.alarme || [];
  const backup = data.backup || {};

  return (
    <div>
      <PageHeader
        title="Betrieb"
        subtitle="Alarme, Löschwarteschlange, Reparaturläufe, Backup — nur Super-Admin"
        action={
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={load}><RefreshCw size={14} /> Aktualisieren</Button>
            <Button size="sm" onClick={nachholen} disabled={busy} data-testid="betrieb-nachholen">
              <Activity size={14} /> Reparaturlauf jetzt
            </Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-4">
        <Kachel label="Offene Alarme" wert={alarme.length} tone={alarme.length ? "red" : "green"} />
        <Kachel label="Dateilöschungen offen" wert={data.datei_loeschungen_offen} tone={data.datei_loeschungen_offen ? "yellow" : "green"} />
        <Kachel label="Abo-Vorgänge hängend" wert={data.abo_vorgaenge_haengend} tone={data.abo_vorgaenge_haengend ? "yellow" : "green"} />
        <Kachel label="Zahlungen ohne Zugang" wert={data.zahlungen_ohne_zugang} tone={data.zahlungen_ohne_zugang ? "red" : "green"} />
        <Kachel label="Wartungsmodus" wert={data.wartungsmodus ? "AKTIV" : "aus"} tone={data.wartungsmodus ? "red" : "green"} />
      </div>

      <Card className="mb-4">
        <div className="flex items-center gap-2 mb-2">
          <ShieldAlert size={16} className="text-zinc-500" />
          <span className="text-[15px] font-semibold text-white">Letztes Backup</span>
          <Badge tone={backup.vollstaendig ? "green" : "red"}>
            {backup.vollstaendig ? "vollständig" : "unvollständig / fehlt"}
          </Badge>
          {backup.offsite ? <Badge tone="blue">Offsite-Kopie</Badge> : <Badge tone="yellow">nur lokal</Badge>}
        </div>
        <div className="text-[13px] text-zinc-400">
          {backup.erstellt ? `erstellt ${fmtDate(backup.erstellt)}` : "kein Zeitpunkt"}
          {backup.alter_stunden != null ? ` · vor ${Math.round(backup.alter_stunden)} Std.` : ""}
          {backup.pfad ? ` · ${backup.pfad}` : ""}
          {backup.hinweis ? ` · ${backup.hinweis}` : ""}
        </div>
      </Card>

      <Card padded={false}>
        <div className="flex items-center gap-2 px-5 py-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
          <AlertTriangle size={16} className="text-zinc-500" />
          <span className="text-[15px] font-semibold text-white">Offene Betriebsalarme</span>
          <Badge>{alarme.length}</Badge>
        </div>
        {alarme.length === 0 ? (
          <EmptyState title="Keine offenen Alarme" hint="Bezahlt-ohne-Zugang, nicht löschbare Dateien, Verträge ohne Datensatz und Backup-Probleme erscheinen hier." />
        ) : (
          <ul className="divide-y" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
            {alarme.map((a) => (
              <li key={a.id} className="px-5 py-3 flex items-start gap-3" data-testid={`alarm-${a.id}`}>
                <Badge tone="red">{a.typ}</Badge>
                <div className="flex-1 min-w-0">
                  <div className="text-[13.5px] text-white truncate">{a.ref || "—"}{a.anzahl > 1 ? ` · ${a.anzahl}×` : ""}</div>
                  <div className="text-[12px] text-zinc-500 truncate">
                    {Object.entries(a.details || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "keine Details"}
                    {" · "}{fmtDate(a.created_at)}
                  </div>
                </div>
                <Button size="sm" variant="ghost" onClick={() => quittieren(a)} title="Als erledigt markieren">
                  <Check size={14} /> Quittieren
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {(data.datei_loeschungen_aufgegeben || []).length > 0 && (
        <Card className="mt-4">
          <div className="text-[15px] font-semibold text-white mb-2">Aufgegebene Dateilöschungen (manuell prüfen)</div>
          <ul className="text-[12.5px] text-zinc-400 space-y-1">
            {data.datei_loeschungen_aufgegeben.map((e) => (
              <li key={e.id}><span className="text-zinc-200">{e.key || e.prefix}</span> · {e.grund} · Versuche {e.versuche} · {e.letzter_fehler}</li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

function Kachel({ label, wert, tone }) {
  return (
    <Card>
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        <span className="text-2xl font-bold text-white tabular-nums">{wert ?? "—"}</span>
        <Badge tone={tone}>{tone === "green" ? "ok" : tone === "yellow" ? "prüfen" : "!"}</Badge>
      </div>
    </Card>
  );
}
