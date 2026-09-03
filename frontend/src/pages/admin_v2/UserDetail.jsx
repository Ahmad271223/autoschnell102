import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  ArrowLeft, FileText, Crown, Mail, Building2, Calendar, Download, Eye,
  UserPlus, X, Euro, Ban, Trash2, Check,
} from "lucide-react";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate, fmtNum } from "./_ui";

// Nur der Kalendertag (aus dem ISO-String, ohne Zeitzonen-Verschiebung):
// "2026-12-31T23:59:59+01:00" -> "31.12.2026"
const fmtTag = (iso) => (iso ? String(iso).slice(0, 10).split("-").reverse().join(".") : "—");

export default function AdminUserDetail() {
  const { id } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [sucher, setSucher] = useState(null);
  const [zahlungen, setZahlungen] = useState(null);
  const [showAdd, setShowAdd] = useState(false);
  const [gueltigBis, setGueltigBis] = useState({});   // je Konto-Id das Datumsfeld
  const [busy, setBusy] = useState(null);           // Doppelklick-Schutz je Konto
  const busyRef = useRef(null);                     // synchroner Guard (State hinkt im selben Tick nach)
  const sperren = (id) => { if (busyRef.current) return false; busyRef.current = id; setBusy(id); return true; };
  const freigeben = () => { busyRef.current = null; setBusy(null); };

  const load = async () => {
    setLoading(true);
    try {
      const r = await api.get(`/admin/users/${id}/contracts`);
      setData(r.data);
    } catch (e) {
      toast.error(errMsg(e, "Fehler beim Laden"));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [id]);

  const dealerId = data?.user?.role === "dealer" ? data.user.dealer_id : null;

  const loadFirma = useCallback(async () => {
    if (!dealerId) return;
    try {
      const [s, z] = await Promise.all([
        api.get(`/admin/dealers/${dealerId}/sucher`),
        api.get(`/admin/dealers/${dealerId}/zahlungen`),
      ]);
      setSucher(s.data);
      setZahlungen(z.data);
    } catch (e) { console.warn("firma laden:", e?.response?.status || e); }
  }, [dealerId]);
  useEffect(() => { loadFirma(); }, [loadFirma]);

  if (loading) return <div className="flex items-center gap-2 text-zinc-500 text-sm py-10"><Spinner /> lade…</div>;
  if (!data) return <EmptyState title="Nutzer nicht gefunden" />;
  const u = data.user || {};
  const contracts = data.contracts || [];

  const openPdf = async (c) => {
    try {
      const r = await api.get(`/admin/contracts/${c.id}/pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(r.data);
      window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (e) { toast.error(errMsg(e, "PDF nicht verfügbar")); }
  };

  const grantAbo = async (s, plan) => {
    if (!sperren(s.id)) return;             // zweiter Klick waehrend der Anfrage: ignorieren
    try {
      const datum = (gueltigBis[s.id] || "").trim();
      await api.post(`/admin/sucher/${s.id}/abo`,
        { plan, ...(datum ? { gueltig_bis: datum } : {}) });
      toast.success(`Abo freigeschaltet (${plan === "yearly" ? "1.500 € / Jahr" : "150 € / Monat"})`
        + (datum ? ` · gültig bis ${datum}` : "") + " — Zahlung erfasst");
      setGueltigBis((g) => ({ ...g, [s.id]: "" }));
      loadFirma();
    } catch (e) { toast.error(errMsg(e)); }
    finally { freigeben(); }
  };
  const saveGueltigBis = async (s) => {
    const datum = (gueltigBis[s.id] || "").trim();
    if (!datum) { toast.error("Bitte ein Datum wählen"); return; }
    if (!sperren(s.id)) return;
    try {
      await api.patch(`/admin/sucher/${s.id}/abo-gueltig-bis`, { gueltig_bis: datum });
      toast.success(`Gültig bis ${datum} gespeichert — danach wird automatisch gesperrt`);
      setGueltigBis((g) => ({ ...g, [s.id]: "" }));
      loadFirma();
    } catch (e) { toast.error(errMsg(e)); }
    finally { freigeben(); }
  };
  const revokeAbo = async (s) => {
    if (!window.confirm(`Abo von ${s.email} aufheben?`)) return;
    if (!sperren(s.id)) return;
    try { await api.post(`/admin/sucher/${s.id}/abo`, { plan: null }); toast.success("Abo aufgehoben"); loadFirma(); }
    catch (e) { toast.error(errMsg(e)); }
    finally { freigeben(); }
  };
  const toggleSucherActive = async (s) => {
    try {
      await api.post(`/admin/users/${s.id}/active`, { active: !s.active });
      toast.success(s.active ? "Sucher gesperrt" : "Sucher entsperrt");
      loadFirma();
    } catch (e) { toast.error(errMsg(e)); }
  };
  const removeSucher = async (s) => {
    if (!window.confirm(`Sucher ${s.email} endgültig löschen?`)) return;
    try { await api.delete(`/admin/users/${s.id}`); toast.success("Sucher gelöscht"); loadFirma(); }
    catch (e) { toast.error(errMsg(e)); }
  };
  const addZahlung = async () => {
    const betrag = window.prompt("Betrag in € (nur Zahl):");
    if (!betrag) return;
    const note = window.prompt("Notiz (optional, z.B. Rechnungsnummer):") || "";
    try {
      await api.post(`/admin/dealers/${dealerId}/zahlungen`,
        { amount: parseFloat(betrag.replace(",", ".")), note });
      toast.success("Zahlung erfasst");
      loadFirma();
    } catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div>
      <Link to="/admin/users" className="inline-flex items-center gap-1.5 text-[13px] text-zinc-400 hover:text-white mb-3">
        <ArrowLeft size={14} /> Zurück zu Nutzern
      </Link>
      <PageHeader
        title={u.company_name || u.username || u.email}
        subtitle={dealerId ? "Firma: Profil, Sucher, Zahlungen & Verträge" : "Nutzerprofil & Verträge (read-only)"}
      />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card className="lg:col-span-1">
          <div className="flex items-center gap-2 mb-4">
            {u.is_super_admin && <Crown size={16} className="text-amber-400" />}
            <span className="text-[15px] font-semibold text-white">Profil</span>
          </div>
          <Row icon={<Mail size={14} />}     label="E-Mail"        value={u.email} />
          <Row icon={<Building2 size={14} />} label="Firma"        value={u.company_name || "—"} />
          {u.kunden_nr != null && <Row label="Kundennummer" value={<Badge tone="blue">#{u.kunden_nr}</Badge>} />}
          <Row icon={<Calendar size={14} />}  label="Erstellt"      value={fmtDate(u.created_at)} />
          <Row label="Rolle"        value={<Badge tone={u.role === "admin" ? "purple" : "gray"}>{u.role || "dealer"}</Badge>} />
          <Row label="Status"       value={u.active === false
            ? <Badge tone="red">Gesperrt</Badge>
            : <Badge tone="green">Aktiv</Badge>} />
          {u.username && <Row label="Benutzername" value={u.username} />}
        </Card>

        <Card className="lg:col-span-2" padded={false}>
          <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
            <div className="flex items-center gap-2">
              <FileText size={16} className="text-zinc-500" />
              <span className="text-[15px] font-semibold text-white">Verträge</span>
              <Badge>{fmtNum(contracts.length)}</Badge>
            </div>
            <span className="text-[12px] text-zinc-500">read-only · keine Bearbeitung</span>
          </div>
          {contracts.length === 0 ? (
            <EmptyState title="Noch keine Verträge" hint="Dieser Nutzer hat bisher keine Verträge erzeugt." />
          ) : (
            <ul className="divide-y" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
              {contracts.map((c) => (
                <li key={c.id} className="px-5 py-3 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="text-[14px] font-medium text-white truncate">
                      {c.contract_data?.buyer_name || c.contract_data?.seller_name || c.filename || "Vertrag"}
                    </div>
                    <div className="text-[12px] text-zinc-400 truncate">
                      {c.contract_data?.vehicle_make || ""} {c.contract_data?.vehicle_model || ""} · {fmtDate(c.created_at)}
                    </div>
                  </div>
                  <Button variant="outline" size="sm" onClick={() => openPdf(c)}>
                    <Eye size={14} /> Ansicht
                  </Button>
                  <a
                    href="#"
                    onClick={(e) => { e.preventDefault(); openPdf(c); }}
                    className="text-zinc-500 hover:text-white" title="PDF"
                  >
                    <Download size={16} />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      {/* ---- Firmen-Verwaltung (nur Händler-Hauptaccounts, 09/2026) ---- */}
      {dealerId && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
          {/* Sucher der Firma */}
          <Card className="lg:col-span-2" padded={false}>
            <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
              <div className="flex items-center gap-2">
                <span className="text-[15px] font-semibold text-white">Chef & Sucher — Freischaltung</span>
                <Badge>{fmtNum((sucher || []).length)}</Badge>
              </div>
              <Button size="sm" onClick={() => setShowAdd(true)} data-testid="admin-add-sucher">
                <UserPlus size={14} /> Sucher anlegen
              </Button>
            </div>
            {!sucher?.length ? (
              <EmptyState title="Noch keine Sucher" hint="Lege die Zugänge an — Anmeldename (E-Mail) + Passwort vergibst du hier." />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px] min-w-[620px]">
                  <thead>
                    <tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                      <th className="px-4 py-2.5 font-medium">Sucher</th>
                      <th className="px-4 py-2.5 font-medium">Abo</th>
                      <th className="px-4 py-2.5 font-medium">Gültig bis / nächste Zahlung</th>
                      <th className="px-4 py-2.5 font-medium">Status</th>
                      <th className="px-4 py-2.5 font-medium text-right">Aktion</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sucher.map((s) => (
                      <tr key={s.id} className="border-t border-white/5">
                        <td className="px-4 py-2.5">
                          <div className="text-white font-medium flex items-center gap-1.5">
                            {s.ist_chef ? (u.contact_person || u.company_name || "Firmenchef") : `${s.first_name || ""} ${s.last_name || ""}`.trim() || "—"}
                            {s.ist_chef && <Badge tone="yellow">Chef</Badge>}
                          </div>
                          <div className="text-[11px] text-zinc-500">{s.email}</div>
                        </td>
                        <td className="px-4 py-2.5">
                          {s.subscription?.active ? (
                            <Badge tone="green">
                              Sucher-Funktion: ja · {s.subscription.plan === "yearly" ? "jährlich · 1.500 €" : "monatlich · 150 €"}
                            </Badge>
                          ) : (
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <Badge tone="red">Sucher-Funktion: nein</Badge>
                              <Button size="sm" onClick={() => grantAbo(s, "monthly")} disabled={busy === s.id}
                                      data-testid={`abo-monat-${s.id}`}
                                      title="Freischalten — erfasst 150 € Zahlung; ohne Datum 30 Tage gültig">
                                <Check size={13} /> 150 €/M
                              </Button>
                              <Button size="sm" variant="outline" onClick={() => grantAbo(s, "yearly")} disabled={busy === s.id}
                                      data-testid={`abo-jahr-${s.id}`}
                                      title="Freischalten — erfasst 1.500 € Zahlung; ohne Datum 365 Tage gültig">
                                1.500 €/J
                              </Button>
                            </div>
                          )}
                        </td>
                        <td className="px-4 py-2.5 text-zinc-400 tabular-nums">
                          <div className="flex items-center gap-1.5">
                            <input type="date" value={gueltigBis[s.id] || ""}
                                   onChange={(e) => setGueltigBis((g) => ({ ...g, [s.id]: e.target.value }))}
                                   data-testid={`gueltig-bis-${s.id}`}
                                   title={s.subscription?.active
                                     ? "Neues Ablaufdatum — Speichern ändert NUR das Datum (keine neue Zahlung)"
                                     : "Optional: gilt beim Freischalten als Ablaufdatum"}
                                   className="h-8 px-2 rounded-lg text-[12px] outline-none"
                                   style={{ background: "#18181b", color: "#fff",
                                            border: "1px solid rgba(255,255,255,0.12)", colorScheme: "dark" }} />
                            {s.subscription?.active && (
                              <Button size="sm" variant="ghost" onClick={() => saveGueltigBis(s)} disabled={busy === s.id}
                                      data-testid={`gueltig-bis-speichern-${s.id}`}
                                      title="Ablaufdatum speichern — danach automatisch gesperrt">
                                Speichern
                              </Button>
                            )}
                          </div>
                          <div className="text-[11px] mt-1" style={{ color: "var(--text-muted, #71717a)" }}>
                            {s.subscription?.active
                              ? <>gültig bis {fmtTag(s.naechste_zahlung_am)} · danach automatisch gesperrt</>
                              : (s.subscription?.status === "expired" && s.subscription?.expires_at
                                ? <span className="text-red-300">abgelaufen am {fmtTag(s.subscription.expires_at)} · automatisch gesperrt</span>
                                : "—")}
                          </div>
                        </td>
                        <td className="px-4 py-2.5">
                          <Badge tone={s.active ? "green" : "red"}>{s.active ? "aktiv" : "gesperrt"}</Badge>
                        </td>
                        <td className="px-4 py-2.5 text-right whitespace-nowrap">
                          {s.subscription?.active && (
                            <Button size="sm" variant="ghost" onClick={() => revokeAbo(s)} disabled={busy === s.id} title="Abo aufheben">
                              Abo aufheben
                            </Button>
                          )}
                          {!s.ist_chef && (
                            <>
                              <Button size="sm" variant="ghost" onClick={() => toggleSucherActive(s)} title={s.active ? "Sperren" : "Entsperren"}>
                                <Ban size={13} /> {s.active ? "Sperren" : "Entsperren"}
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => removeSucher(s)} title="Löschen">
                                <Trash2 size={13} />
                              </Button>
                            </>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* Zahlungen */}
          <Card className="lg:col-span-1" padded={false}>
            <div className="flex items-center justify-between px-5 py-4" style={{ borderBottom: "1px solid rgba(255,255,255,0.08)" }}>
              <div className="flex items-center gap-2">
                <Euro size={15} className="text-zinc-500" />
                <span className="text-[15px] font-semibold text-white">Zahlungen</span>
              </div>
              <Button size="sm" variant="outline" onClick={addZahlung}>Nachtragen</Button>
            </div>
            {!zahlungen?.length ? (
              <EmptyState title="Noch keine Zahlungen" hint="Beim Freischalten eines Abos wird die Zahlung automatisch erfasst." />
            ) : (
              <ul className="divide-y" style={{ borderColor: "rgba(255,255,255,0.06)" }}>
                {zahlungen.slice(0, 20).map((z) => (
                  <li key={z.id} className="px-5 py-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-white font-semibold tabular-nums">
                        {Number(z.amount).toLocaleString("de-DE", { minimumFractionDigits: 2 })} €
                      </span>
                      <span className="text-[12px] text-zinc-500 tabular-nums">{z.paid_at}</span>
                    </div>
                    <div className="text-[12px] text-zinc-400">
                      {z.plan ? (z.plan === "yearly" ? "Jahres-Abo" : "Monats-Abo") : "manuell"}
                      {z.period_until ? ` · bezahlt bis ${fmtTag(z.period_until)}` : ""}
                      {z.note ? ` · ${z.note}` : ""}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      )}

      {showAdd && dealerId && (
        <AddSucherDialog
          dealerId={dealerId}
          onClose={() => setShowAdd(false)}
          onDone={() => { setShowAdd(false); loadFirma(); }}
        />
      )}
    </div>
  );
}

/** Betreiber legt einen Sucher an — Anmeldename (E-Mail) + Passwort. */
function AddSucherDialog({ dealerId, onClose, onDone }) {
  const [f, setF] = useState({ email: "", password: "", first_name: "", last_name: "", phone: "" });
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (!f.email || f.password.length < 8) {
      toast.error("E-Mail und Passwort (min. 8 Zeichen) angeben"); return;
    }
    setBusy(true);
    try {
      const r = await api.post(`/admin/dealers/${dealerId}/sucher`, f);
      toast.success(r.data.hinweis || "Sucher angelegt");
      onDone?.();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  const inputCls = "w-full rounded-lg border bg-transparent px-3 py-2 text-sm outline-none";
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)" }}>
      <div className="w-full max-w-md rounded-2xl p-5 bg-white dark:bg-zinc-900 border border-black/10 dark:border-white/10">
        <div className="flex items-center justify-between mb-3">
          <div className="text-lg font-bold">Sucher anlegen</div>
          <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200"><X size={20} /></button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <input value={f.first_name} onChange={set("first_name")} placeholder="Vorname" className={inputCls} />
          <input value={f.last_name} onChange={set("last_name")} placeholder="Nachname" className={inputCls} />
          <div className="col-span-2"><input value={f.email} onChange={set("email")} placeholder="Anmelde-E-Mail *" className={inputCls} /></div>
          <div className="col-span-2"><input value={f.password} onChange={set("password")} placeholder="Passwort (min. 8 Zeichen, Ziffer/Sonderzeichen) *" className={inputCls} /></div>
          <input value={f.phone} onChange={set("phone")} placeholder="Telefon" className={inputCls} />
        </div>
        <div className="mt-3 text-[11px] text-zinc-500">
          Zugangsdaten danach an die Firma weitergeben. Suchen &amp; Vergleichen
          funktioniert erst nach Abo-Freischaltung (150 €/M · 1.500 €/J).
        </div>
        <Button className="mt-4 w-full" onClick={submit} disabled={busy}>
          {busy ? "Wird angelegt…" : "Sucher anlegen"}
        </Button>
      </div>
    </div>
  );
}

function Row({ icon, label, value }) {
  return (
    <div className="flex items-start justify-between py-2 gap-3 text-[13.5px]">
      <div className="flex items-center gap-1.5 text-zinc-400">{icon}{label}</div>
      <div className="text-white text-right break-words max-w-[60%]">{value || "—"}</div>
    </div>
  );
}
