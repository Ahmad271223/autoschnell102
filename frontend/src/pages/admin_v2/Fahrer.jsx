import { useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { ChevronDown, KeyRound, Lock, LockOpen, Search, Sparkles, Trash2, Truck, UserPlus } from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { useAuth } from "@/context/AuthContext";
import FahrerAnlegenDialog from "@/components/admin/FahrerAnlegenDialog";
import KontoPruefen from "@/components/admin/KontoPruefen";
import PasswortFeld from "@/components/admin/PasswortFeld";
import { passwortProblem, sperreHinweis } from "@/lib/passwort";

/**
 * Fahrer-Verwaltung (Review 09/2026: fehlte komplett).
 *
 * Fahrer-Konten sind firmenneutral — die Liste ist plattformweit; je Fahrer
 * werden die verknüpften Händler und die Terminzahl angezeigt. Aktionen:
 * anlegen (Kontonummer 13.09.2026: nur der Betreiber legt Fahrer an),
 * sperren/entsperren (beendet die Sitzung), Passwort setzen, löschen
 * (DSGVO: Verknüpfungen weg, offene Termine getrennt), KI-Abholbewertung
 * je Fahrer freischalten (Wunsch Ahmad 26.09.2026 abends: ohne Fahrer-
 * Freischaltung "allgemein nein"; nur Super-Admin).
 */
const fahrerLabel = (r) => [r.display_name || "Fahrer", r.kontonummer ? `Kontonummer ${r.kontonummer}` : ""]
  .filter(Boolean).join(" · ");

// Pruefbericht 20.09.2026 (AD-33): ab so vielen Zeichen sucht der SERVER
// (q) ueber alle Fahrer — vorher nur die Oberflaeche in der ersten Seite.
export const SUCHE_AB = 3;
export function serverSuche(q) {
  const s = String(q ?? "").trim();
  return s.length >= SUCHE_AB ? s : "";
}

export default function AdminFahrer() {
  const { user: ich } = useAuth();
  const superAdmin = !!ich?.is_super_admin;   // KI-Freischaltung ist Betreibersache
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [resetDriver, setResetDriver] = useState(null);
  const [newPw, setNewPw] = useState("");
  const [deleteDriver, setDeleteDriver] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [anlegen, setAnlegen] = useState(false);
  const [gekuerzt, setGekuerzt] = useState(false);   // Phase 4 (4.3): Liste vom Server gekuerzt
  // AD-33: Seite fuer "Weitere laden"; nur die juengste Antwort zaehlt
  const [seite, setSeite] = useState(1);
  const [laedtMehr, setLaedtMehr] = useState(false);
  const ladeLauf = useRef(0);
  // Pruefbericht 20.09.2026 (R1-42/AD-34): Sperren und Passwort-Setzen liefen
  // ohne busy-Zustand — ein zweiter Klick schickte den Aufruf doppelt (beim
  // Sperren die Umkehr). Gleiches Muster wie UserDetail.jsx: je Fahrer-Id
  // gesperrt, bis die Liste neu geladen ist; synchroner Guard per Ref.
  const [busy, setBusy] = useState(null);
  const busyRef = useRef(null);
  const sperren = (id) => { if (busyRef.current) return false; busyRef.current = id; setBusy(id); return true; };
  const freigeben = () => { busyRef.current = null; setBusy(null); };
  const suche = serverSuche(q);

  const load = async ({ suchtext = suche, naechste = 1 } = {}) => {
    const lauf = ++ladeLauf.current;
    if (naechste > 1) setLaedtMehr(true); else setLoading(true);
    try {
      const r = await api.get("/admin/drivers",
        { params: { seite: naechste, ...(suchtext ? { q: suchtext } : {}) } });
      if (lauf !== ladeLauf.current) return;          // ueberholt
      const neu = Array.isArray(r.data) ? r.data : [];
      setRows((alt) => (naechste > 1 ? [...alt, ...neu] : neu));
      setSeite(naechste);
      setGekuerzt(r.headers?.["x-truncated"] === "1");
    } catch (e) {
      if (lauf !== ladeLauf.current) return;
      toast.error(errMsg(e, "Fahrer konnten nicht geladen werden"));
    } finally {
      if (lauf === ladeLauf.current) { setLoading(false); setLaedtMehr(false); }
    }
  };
  // Erstes Laden sofort; eine Server-Suche kurz entprellt.
  useEffect(() => {
    const t = setTimeout(() => load({ suchtext: suche, naechste: 1 }), q ? 300 : 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [suche]);

  const filtered = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return rows;
    return rows.filter((r) =>
      (r.kontonummer || "").toLowerCase().includes(t)
      || (r.display_name || "").toLowerCase().includes(t)
      || (r.email || "").toLowerCase().includes(t)
      || (r.driver_code || "").toLowerCase().includes(t)
      || (r.firmen || []).some((f) => (f || "").toLowerCase().includes(t)));
  }, [rows, q]);

  const toggleActive = async (r) => {
    if (!sperren(r.id)) return;             // R1-42: zweiter Klick waehrend der Anfrage: ignorieren
    try {
      await api.post(`/admin/drivers/${r.id}/active`, { active: !r.active });
      toast.success(r.active ? "Fahrer gesperrt" : "Fahrer entsperrt");
      await load();
    } catch (e) { toast.error(errMsg(e)); }
    finally { freigeben(); }
  };

  // Wunsch Ahmad 26.09.2026 abends: KI-Abholbewertung je Fahrer freischalten
  // (wie bei den Sucher-Konten). Sperren fragt nach; danach Liste neu laden.
  const toggleKi = async (r) => {
    const aktiv = !r.ki_aktiv;
    if (!aktiv && !window.confirm(`KI-Abholbewertung für ${fahrerLabel(r)} sperren?`)) return;
    if (!sperren(r.id)) return;
    try {
      await api.post(`/admin/drivers/${r.id}/ki`, { aktiv });
      toast.success(aktiv ? "KI-Abholbewertung freigeschaltet" : "KI-Abholbewertung gesperrt");
      await load();
    } catch (e) { toast.error(errMsg(e, "KI-Freischaltung fehlgeschlagen")); }
    finally { freigeben(); }
  };

  const submitReset = async () => {
    const problem = passwortProblem(newPw);
    if (problem) { toast.error(problem); return; }
    if (!sperren(resetDriver.id)) return;   // AD-34: "Setzen" nicht doppelt
    try {
      const { data } = await api.post(`/admin/drivers/${resetDriver.id}/password`, { new_password: newPw });
      // 14.09.2026: nur von einer Sperre sprechen, wenn wirklich eine bestand.
      toast.success(`Passwort gesetzt — alle Sitzungen des Fahrers wurden beendet.${sperreHinweis(data)}`);
      setResetDriver(null); setNewPw("");
    } catch (e) { toast.error(errMsg(e)); }
    finally { freigeben(); }
  };

  const submitDelete = async () => {
    setDeleting(true);
    try {
      const { data } = await api.delete(`/admin/drivers/${deleteDriver.id}`);
      toast.success(`Fahrer gelöscht (${data.verknuepfungen_entfernt} Verknüpfung(en) entfernt, `
        + `${data.offene_termine_getrennt} offene(r) Termin(e) getrennt)`);
      setDeleteDriver(null);
      load();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setDeleting(false); }
  };

  return (
    <div>
      <PageHeader
        title="Fahrer"
        subtitle="Alle Fahrer-Konten der Plattform — Verknüpfungen laufen über die Händler"
        action={
          <div className="flex items-center gap-3">
            <span className="text-[12px] text-zinc-400">
              {rows.length} {suche ? `Treffer für „${suche}“` : "Konten"}{gekuerzt ? " (Liste gekürzt — es gibt weitere)" : ""}
            </span>
            <Button size="sm" onClick={() => setAnlegen(true)} data-testid="fahrer-anlegen-btn">
              <UserPlus size={14} /> Fahrer anlegen
            </Button>
          </div>
        }
      />

      <KontoPruefen />

      <Card padded={false}>
        <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--wa-08)" }}>
          <div className="relative max-w-sm">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
            <input
              value={q} onChange={(e) => setQ(e.target.value)}
              placeholder="Kontonummer, Name, E-Mail, Fahrer-Code oder Firma — ab 3 Zeichen über alle Fahrer"
              data-testid="fahrer-suche"
              className="h-10 pl-9 pr-3 rounded-xl text-[14px] w-full outline-none focus:ring-2 focus:ring-red-500/40"
              style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" }}
            />
          </div>
        </div>

        {loading ? (
          <div className="flex items-center gap-2 text-zinc-500 text-sm p-6"><Spinner /> lade…</div>
        ) : filtered.length === 0 ? (
          <EmptyState title={q ? "Keine Treffer" : "Noch keine Fahrer"}
                      hint={q ? "Suche anpassen." : "Fahrer legst du über „Fahrer anlegen“ an."} />
        ) : (
          <ul className="divide-y divide-white/5">
            {filtered.map((r) => (
              <li key={r.id} className="px-4 py-3 flex items-center gap-3" data-testid={`fahrer-row-${r.id}`}>
                <div className="w-9 h-9 rounded-full flex items-center justify-center shrink-0 text-zinc-300"
                     style={{ background: "var(--wa-06)" }}>
                  <Truck size={16} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[14px] font-semibold text-white truncate">{r.display_name || "—"}</span>
                    {r.kontonummer && (
                      <span className="font-mono text-[12.5px] text-zinc-200" data-testid={`fahrer-kontonummer-${r.id}`}>
                        {r.kontonummer}
                      </span>
                    )}
                    {r.driver_code && r.driver_code !== r.kontonummer && <Badge tone="blue">{r.driver_code}</Badge>}
                    <Badge tone={r.active ? "green" : "red"}>{r.active ? "Aktiv" : "Gesperrt"}</Badge>
                    <span data-testid={`fahrer-ki-${r.id}`}>
                      <Badge tone={r.ki_aktiv ? "green" : "gray"}>{r.ki_aktiv ? "KI: ja" : "KI: nein"}</Badge>
                    </span>
                  </div>
                  <div className="text-[12px] text-zinc-500 truncate">
                    {r.email ? `${r.email} · ` : ""}seit {fmtDate(r.created_at)}
                    {" · "}{r.verknuepfungen || 0} Händler{(r.firmen || []).length ? ` (${r.firmen.join(", ")})` : ""}
                    {" · "}{r.termine || 0} Termine{r.termine_offen ? ` (${r.termine_offen} offen)` : ""}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Button size="sm" variant="ghost" data-testid={`fahrer-ki-schalten-${r.id}`}
                          disabled={busy === r.id || !superAdmin}
                          title={superAdmin
                            ? "KI-Abholbewertung für diesen Fahrer (zusätzlich zur Firma; 10 € je Fahrer und Monat)"
                            : "Nur der Super-Admin"}
                          onClick={() => toggleKi(r)}>
                    <Sparkles size={13} /> {r.ki_aktiv ? "KI sperren" : "KI freischalten"}
                  </Button>
                  <Button size="sm" variant="secondary" data-testid={`fahrer-pw-btn-${r.id}`}
                          disabled={busy === r.id}
                          onClick={() => { setResetDriver(r); setNewPw(""); }}>
                    <KeyRound size={13} /> Passwort
                  </Button>
                  <Button size="sm" variant={r.active ? "outline" : "primary"}
                          data-testid={`fahrer-toggle-active-btn-${r.id}`}
                          disabled={busy === r.id}
                          onClick={() => toggleActive(r)}>
                    {r.active ? <><Lock size={13} /> Sperren</> : <><LockOpen size={13} /> Entsperren</>}
                  </Button>
                  <Button size="sm" variant="danger" data-testid={`fahrer-delete-btn-${r.id}`}
                          disabled={busy === r.id}
                          onClick={() => setDeleteDriver(r)}>
                    <Trash2 size={13} />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
        {gekuerzt && !loading && (
          // AD-33: die naechste Seite unten anhaengen statt nur "gekuerzt" zu sagen
          <div className="px-4 py-4 flex justify-center" style={{ borderTop: "1px solid var(--wa-06)" }}>
            <Button variant="outline" size="sm" onClick={() => load({ naechste: seite + 1 })}
                    disabled={laedtMehr} data-testid="fahrer-mehr">
              {laedtMehr ? <Spinner /> : <ChevronDown size={14} />}
              {laedtMehr ? "lädt…" : "Weitere laden"}
            </Button>
          </div>
        )}
      </Card>

      {anlegen && (
        <FahrerAnlegenDialog onClose={() => setAnlegen(false)} onAngelegt={load} />
      )}

      {resetDriver && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
             onClick={() => setResetDriver(null)}>
          <div className="w-full max-w-sm rounded-2xl p-5" style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)" }}
               onClick={(e) => e.stopPropagation()}>
            <div className="text-[15px] font-semibold text-white">Passwort setzen</div>
            <div className="text-[12.5px] text-zinc-500 mt-1">
              {fahrerLabel(resetDriver)}. Alle Sitzungen des Fahrers werden beendet.
            </div>
            <div className="mt-3">
              <PasswortFeld value={newPw} onChange={setNewPw} testid="fahrer-pw-input" autoFocus
                            placeholder="Neues Passwort (mind. 10 Zeichen, Ziffer oder Sonderzeichen)"
                            className="h-10 px-3 rounded-xl text-[14px] w-full outline-none"
                            style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" }} />
            </div>
            <div className="mt-4 flex gap-2 justify-end">
              <Button variant="ghost" onClick={() => setResetDriver(null)} disabled={busy === resetDriver.id}>Abbrechen</Button>
              <Button data-testid="fahrer-pw-submit" onClick={submitReset} disabled={busy === resetDriver.id}>
                {busy === resetDriver.id ? "Setze…" : "Setzen"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {deleteDriver && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60"
             onClick={() => !deleting && setDeleteDriver(null)}>
          <div className="w-full max-w-sm rounded-2xl p-5" style={{ background: "var(--bg-elevated)", border: "1px solid rgba(239,68,68,0.4)" }}
               onClick={(e) => e.stopPropagation()}>
            <div className="text-[15px] font-semibold text-white">Fahrer löschen?</div>
            <div className="text-[12.5px] text-zinc-400 mt-2 leading-relaxed">
              {fahrerLabel(deleteDriver)} wird endgültig gelöscht.
              Händler-Verknüpfungen werden entfernt, offene Termine vom Fahrer getrennt.
              Abgeschlossene Abholungen behalten ihre Historie.
            </div>
            <div className="mt-4 flex gap-2 justify-end">
              <Button variant="ghost" disabled={deleting} onClick={() => setDeleteDriver(null)}>Abbrechen</Button>
              <Button variant="danger" disabled={deleting} data-testid="fahrer-delete-confirm"
                      onClick={submitDelete}>
                {deleting ? "Löscht…" : "Endgültig löschen"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
