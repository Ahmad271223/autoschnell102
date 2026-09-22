import { useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Activity, AlertTriangle, Check, Mail, RefreshCw, Send, ShieldAlert } from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";

/**
 * Betrieb (Audit 09/2026): alles, was frueher still scheiterte, ist hier
 * sichtbar — offene Betriebsalarme (Datei nicht loeschbar, Vertrag ohne
 * dauerhaften Datensatz, Backup unvollstaendig), die Loesch-Warteschlange,
 * haengende Freischaltungs-Vorgaenge und das letzte Backup. Nur Super-Admin.
 * Pruefbericht 20.09.2026 (DO-22): die Kachel "Zahlungen ohne Zugang" ist
 * weg — Stripe ist seit 14.09.2026 entfernt, der Alarm zahlung_ohne_zugang
 * wird nirgends mehr ausgeloest.
 */
export default function AdminBetrieb() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  // Pruefbericht 20.09.2026 (AD-14): Ladefehler endeten bei "Keine
  // Betriebsdaten" — der Alarmstand war dann unbekannt, sah aber ruhig aus.
  const [ladeFehler, setLadeFehler] = useState("");
  const [quittiert, setQuittiert] = useState("");
  const [testmailLaeuft, setTestmailLaeuft] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get("/admin/betrieb");
      setData(r.data);
      setLadeFehler("");
    } catch (e) {
      setLadeFehler(errMsg(e, "Betriebsdaten konnten nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const quittieren = async (a) => {
    if (quittiert) return;                 // AD-31: kein zweiter Klick -> kein roter 404
    setQuittiert(a.id);
    try {
      await api.post(`/admin/betrieb/alarme/${a.id}/quittieren`);
      toast.success("Alarm quittiert");
    } catch (e) {
      if (e?.response?.status !== 404) toast.error(errMsg(e));
    } finally {
      await load();
      setQuittiert("");
    }
  };
  const nachholen = async () => {
    setBusy(true);
    try {
      const r = await api.post("/admin/betrieb/nachholen");
      const z = r.data?.zahlungen;
      const zText = z && typeof z === "object"
        ? Object.entries(z).map(([k, v]) => `${k} ${v}`).join(", ") || "keine" : String(z ?? 0);
      toast.success(`Reparaturlauf: Abo-Vorgänge ${r.data?.abo_vorgaenge ?? 0}, Zahlungen ${zText}`);
      load();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };
  // Wunsch Ahmad 21.09.2026: Probe-Mail an BETRIEB_MELDUNG_AN per Knopf —
  // bisher war der einzige Test der Tagesbericht um 8 Uhr.
  const testmail = async () => {
    if (testmailLaeuft) return;
    setTestmailLaeuft(true);
    try {
      const r = await api.post("/admin/betrieb/testmail");
      if (r.data?.ok === false) {
        // Pruefung 21.09.2026 (Betrieb): Ablehnung oder keine Antwort des
        // Anbieters kommt als 200 mit ok:false und Grund — ein 502 ersetzte
        // Cloudflare durch eine eigene Fehlerseite, der Grund ging verloren.
        toast.error(r.data.grund || "Testmail nicht zugestellt — Server-Protokoll prüfen.",
          { duration: 15000 });
      } else if (r.data?.zustellung === "mock") {
        toast.success("Testmodus: keine echte Mail verschickt (MOCK_PROVIDER_FETCH ist an).");
      } else {
        toast.success(`Testmail an ${r.data?.an || data?.alarm_empfaenger} verschickt — bitte Posteingang und Spam-Ordner prüfen.`,
          { duration: 10000 });
      }
    } catch (e) {
      toast.error(errMsg(e, "Testmail konnte nicht gesendet werden"), { duration: 15000 });
    } finally {
      setTestmailLaeuft(false);
    }
  };

  if (loading && !data) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  if (!data) {
    return (
      <Card data-testid="betrieb-ladefehler">
        <div className="text-[14px] text-red-300">
          {ladeFehler || "Betriebsdaten nicht ladbar"} — der Alarmstand ist UNBEKANNT.
        </div>
        <Button size="sm" className="mt-3" onClick={load}><RefreshCw size={14} /> Erneut laden</Button>
      </Card>
    );
  }
  const alarme = data.alarme || [];
  const uebersicht = data.alarm_uebersicht || null;
  const alarmeGesamt = uebersicht?.gesamt ?? alarme.length;
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

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
        <Kachel label="Offene Alarme" wert={alarmeGesamt} tone={alarmeGesamt ? "red" : "green"} />
        <Kachel label="Dateilöschungen offen" wert={data.datei_loeschungen_offen} tone={data.datei_loeschungen_offen ? "yellow" : "green"} />
        <Kachel label="Abo-Vorgänge hängend" wert={data.abo_vorgaenge_haengend} tone={data.abo_vorgaenge_haengend ? "yellow" : "green"} />
        <Kachel label="Wartungsmodus" wert={data.wartungsmodus ? "AKTIV" : "aus"} tone={data.wartungsmodus ? "red" : "green"} />
      </div>
      {"alarm_empfaenger" in data && !data.alarm_empfaenger && (
        // Pruefbericht 20.09.2026 (AL-03/U3): ohne Empfaenger gehen Alarme an niemanden
        <Card className="mb-4" data-testid="alarm-empfaenger-fehlt">
          <div className="text-[13.5px] text-amber-200">
            Alarme werden an niemanden per E-Mail gemeldet (BETRIEB_MELDUNG_AN ist leer) — auf
            beiden Servern setzen: <span className="font-mono">sh deploy/env_setzen.sh BETRIEB_MELDUNG_AN=deine@adresse.de</span>
          </div>
        </Card>
      )}
      {data.alarm_empfaenger && (
        // Wunsch Ahmad 21.09.2026: sichtbar, WOHIN Alarme, Anfragen und
        // Tagesbericht gehen — und per Knopf pruefbar.
        <Card className="mb-4" data-testid="alarm-empfaenger">
          <div className="flex flex-wrap items-center gap-3">
            <Mail size={16} className="text-zinc-500 shrink-0" />
            <div className="text-[13.5px] text-zinc-300 flex-1 min-w-0">
              Meldungen gehen an: <span className="font-mono text-white break-all">{data.alarm_empfaenger}</span>
            </div>
            <Button size="sm" variant="outline" onClick={testmail} disabled={testmailLaeuft}
                    data-testid="betrieb-testmail">
              <Send size={14} /> {testmailLaeuft ? "Sende…" : "Testmail senden"}
            </Button>
          </div>
        </Card>
      )}
      {(data.super_admins_ohne_mfa || []).length > 0 && (
        <Card className="mb-4" data-testid="mfa-hinweis">
          <div className="text-[13.5px] text-amber-200">
            Zwei-Faktor-Anmeldung fehlt bei: {data.super_admins_ohne_mfa.join(", ")} — unter Einstellungen einrichten.
          </div>
        </Card>
      )}

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

      {"aufraeumlauf" in data && <AufraeumlaufKarte lauf={data.aufraeumlauf} />}

      <Card padded={false}>
        <div className="flex items-center gap-2 px-5 py-4" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <AlertTriangle size={16} className="text-zinc-500" />
          <span className="text-[15px] font-semibold text-white">Offene Betriebsalarme</span>
          <Badge>{alarmeGesamt}</Badge>
        </div>
        {ladeFehler && (
          <div className="px-5 py-2 text-[12.5px] text-red-300" role="alert">
            Aktualisieren fehlgeschlagen ({ladeFehler}) — angezeigt ist der letzte Stand.
          </div>
        )}
        {uebersicht?.je_typ?.length > 0 && (
          <div className="px-5 py-3 flex flex-wrap gap-2" data-testid="alarm-uebersicht"
               style={{ borderBottom: "1px solid var(--wa-06)" }}>
            {uebersicht.je_typ.map((t) => (
              <span key={t.typ} className="text-[12px] text-zinc-300 rounded-lg px-2 py-1"
                    style={{ background: "var(--wa-05)" }} title={`zuletzt ${fmtDate(t.zuletzt)}`}>
                {t.typ}: <b className="text-white">{t.eintraege}</b>
                {t.vorkommen > t.eintraege ? ` (${t.vorkommen}×)` : ""}
              </span>
            ))}
          </div>
        )}
        {alarmeGesamt > alarme.length && (
          <div className="px-5 py-2 text-[12.5px] text-amber-300" data-testid="alarme-gekuerzt">
            Angezeigt werden die {alarme.length} zuletzt aufgetretenen von {alarmeGesamt} offenen Alarmen.
          </div>
        )}
        {alarme.length === 0 ? (
          <EmptyState title="Keine offenen Alarme" hint="Nicht löschbare Dateien, Verträge ohne Datensatz und Backup-Probleme erscheinen hier." />
        ) : (
          <ul className="divide-y" style={{ borderColor: "var(--wa-06)" }}>
            {alarme.map((a) => (
              <li key={a.id} className="px-5 py-3 flex items-start gap-3" data-testid={`alarm-${a.id}`}>
                <Badge tone="red">{a.typ}</Badge>
                <div className="flex-1 min-w-0">
                  <div className="text-[13.5px] text-white truncate">{a.ref || "—"}{a.anzahl > 1 ? ` · ${a.anzahl}×` : ""}</div>
                  <div className="text-[12px] text-zinc-500 truncate">
                    {Object.entries(a.details || {}).map(([k, v]) => `${k}: ${v}`).join(" · ") || "keine Details"}
                    {" · "}zuletzt {fmtDate(a.zuletzt || a.created_at)}
                  </div>
                </div>
                <Button size="sm" variant="ghost" onClick={() => quittieren(a)} disabled={!!quittiert} title="Als erledigt markieren">
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

/**
 * Rollenprüfung 22.09.2026 (RP-394, Welle 2): Stand des stündlichen
 * Aufräumlaufs. Scheiterte ein Teilschritt, stand das vorher nur im Log;
 * jetzt sieht der Betreiber den letzten VOLLSTÄNDIGEN Lauf und die
 * gescheiterten Schritte (der Alarm aufraeumschritt_fehlgeschlagen steht
 * zusätzlich unten bei den Alarmen).
 */
export function aufraeumlaufZustand(lauf) {
  if (!lauf || !lauf.letzter_lauf) return { tone: "yellow", text: "noch kein Lauf erfasst" };
  const fehl = lauf.fehlgeschlagen || [];
  if (lauf.ueberfaellig) return { tone: "red", text: "kein vollständiger Lauf seit über " + (lauf.grenze_h || 3) + " Std." };
  if (fehl.length) return { tone: "red", text: `${fehl.length} Schritt(e) gescheitert` };
  return { tone: "green", text: "vollständig" };
}

function AufraeumlaufKarte({ lauf }) {
  const z = aufraeumlaufZustand(lauf);
  const fehl = lauf?.fehlgeschlagen || [];
  return (
    <Card className="mb-4" data-testid="aufraeumlauf">
      <div className="flex items-center gap-2 mb-2">
        <Activity size={16} className="text-zinc-500" />
        <span className="text-[15px] font-semibold text-white">Aufräumlauf</span>
        <Badge tone={z.tone}>{z.text}</Badge>
      </div>
      <div className="text-[13px] text-zinc-400">
        Letzter vollständiger Aufräumlauf:{" "}
        <span className="text-zinc-200">{lauf?.letzter_vollstaendiger_lauf ? fmtDate(lauf.letzter_vollstaendiger_lauf) : "—"}</span>
        {lauf?.letzter_lauf ? ` · letzter Lauf ${fmtDate(lauf.letzter_lauf)}` : ""}
      </div>
      {fehl.length > 0 && (
        <div className="mt-1 text-[12.5px] text-red-300" data-testid="aufraeumlauf-fehlgeschlagen">
          Gescheiterte Schritte im letzten Lauf: {fehl.join(", ")}
        </div>
      )}
    </Card>
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
