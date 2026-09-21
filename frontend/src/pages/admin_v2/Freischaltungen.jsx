import { useCallback, useEffect, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { PageHeader, Card, Badge, Button, Spinner, EmptyState, fmtDate } from "./_ui";
import { RefreshCw, Check, X, Store, Building2, Truck, KeyRound, UserPlus } from "lucide-react";
import ZugangsdatenKarte from "@/components/admin/ZugangsdatenKarte";
import PasswortFeld from "@/components/admin/PasswortFeld";
import { passwortProblem } from "@/lib/passwort";
import FahrerAnlegenDialog from "@/components/admin/FahrerAnlegenDialog";
import { mitAbweichung } from "@/lib/anfrageAbweichung";

/**
 * Freischaltungen (Betreiber-Modell 09/2026):
 * - Zugangs-Anfragen (Startseite) -> Konto direkt anlegen. Kontonummer
 *   (13.09.2026): je nach Art Firma, Zwischenhändler oder Fahrer; das Backend
 *   schliesst die Anfrage beim Anlegen (anfrage_id) und vergibt die Nummer.
 * - Sucher-Abo-Anfragen (150/1500, Rechnung) -> freischalten (erfasst die Zahlung).
 * - Marktplatz-Zugang: hier manuell aktivieren/sperren; Passwort setzen.
 */
export default function AdminFreischaltungen() {
  const [requests, setRequests] = useState(null);
  const [buyers, setBuyers] = useState(null);
  const [loading, setLoading] = useState(true);
  const [firmaReq, setFirmaReq] = useState(null);     // Zugangs-Anfrage art=firma -> Dialog
  const [kaeuferDialog, setKaeuferDialog] = useState(null);   // { request } (request null = frei)
  const [fahrerReq, setFahrerReq] = useState(null);   // Zugangs-Anfrage art=fahrer -> Dialog
  const [pwBuyer, setPwBuyer] = useState(null);
  const [gekuerzt, setGekuerzt] = useState(false);   // Phase 4 (4.3): Liste vom Server gekuerzt
  // Pruefbericht 20.09.2026 (AD-03/O6): Ladefehler landeten nur in der
  // Konsole — die Seite sagte "Keine offenen Anfragen", und wartende Sucher-
  // Abos blieben liegen. Anfragen und Zwischenhaendler jetzt getrennt, jeder
  // Fehler sichtbar.
  const [anfragenFehler, setAnfragenFehler] = useState("");
  const [kaeuferFehler, setKaeuferFehler] = useState("");
  // AD-11: je Anfrage/Zeile gesperrt, bis die Liste neu geladen ist — ein
  // zweiter Klick buchte sonst einen zweiten Vorgang samt Zahlung.
  const [arbeitet, setArbeitet] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    let gek = false;
    try {
      const r = await api.get("/admin/plan-requests?status=offen");
      setRequests(r.data);
      gek = gek || r.headers?.["x-truncated"] === "1";
      setAnfragenFehler("");
    } catch (e) {
      setAnfragenFehler(errMsg(e, "Offene Anfragen konnten nicht geladen werden"));
    }
    try {
      const b = await api.get("/admin/buyers");
      setBuyers(b.data);
      gek = gek || b.headers?.["x-truncated"] === "1";
      setKaeuferFehler("");
    } catch (e) {
      setKaeuferFehler(errMsg(e, "Zwischenhändler konnten nicht geladen werden"));
    }
    setGekuerzt(gek);
    setLoading(false);
  }, []);

  // Eine Aktion je Zeile, Sperre erst nach dem Neuladen aufheben.
  const aktion = async (schluessel, fn) => {
    if (arbeitet) return;
    setArbeitet(schluessel);
    try { await fn(); } finally { await load(); setArbeitet(""); }
  };

  useEffect(() => { load(); }, [load]);

  // USt-IdNr. beim EU-Dienst VIES pruefen (Audit 09/2026, Punkt 40)
  const pruefeUstId = async (b) => {
    try {
      const r = await api.post(`/admin/buyers/${b.id}/ustid-pruefen`);
      const e = r.data || {};
      const txt = e.status === "gueltig"
        ? `USt-IdNr. ${e.ust_id} gültig${e.name ? ` · ${e.name}` : ""}${e.adresse ? ` · ${e.adresse}` : ""}`
        : (e.hinweis || e.status);
      (e.status === "gueltig" ? toast.success : e.status === "ungueltig" ? toast.error : toast.warning)(txt, { duration: 9000 });
      load();
    } catch (e) { toast.error(errMsg(e, "Prüfung fehlgeschlagen")); }
  };

  // AD-10: Fehler beim Schliessen wurden verschluckt ("egal") — beim
  // Verkaufspaket blieb die Anfrage dann offen und liess sich ein zweites Mal
  // freischalten. Jetzt sagt die Oberflaeche es.
  const closeReq = async (id, status = "erledigt") => {
    await api.put(`/admin/plan-requests/${id}`, { status });
  };
  const anfrageSchliessen = async (req) => {
    try { await closeReq(req.id); }
    catch (e) {
      toast.warning(`Freigeschaltet — aber die Anfrage konnte nicht geschlossen werden (${errMsg(e, "Fehler")}). `
        + "Bitte nicht noch einmal freischalten, sondern die Anfrage ablehnen/erledigen.", { duration: 12000 });
    }
  };

  const grantSucher = (req) => aktion(req.id, async () => {
    try {
      await api.post(`/admin/sucher/${req.subject_user_id}/abo`,
        { plan: req.wanted_plan || "monthly" });
      await anfrageSchliessen(req);
      toast.success(`Sucher-Abo aktiviert (${req.sucher_name || req.kontonummer || ""})`);
    } catch (e) { toast.error(errMsg(e)); }
  });

  const grantPlan = (req) => aktion(req.id, async () => {
    try {
      await api.put(`/admin/dealers/${req.dealer_id}/sale-plan`, { tier: req.wanted_tier });
      await anfrageSchliessen(req);
      toast.success(`Verkaufspaket ${req.wanted_tier} aktiviert (${req.company_name || ""})`);
    } catch (e) { toast.error(errMsg(e)); }
  });

  const grantBuyer = (req) => aktion(req.id, async () => {
    try {
      await api.post(`/admin/buyers/${req.buyer_user_id}/access`, { plan: "monthly" });
      await anfrageSchliessen(req);
      toast.success("Marktplatz-Zugang aktiviert");
    } catch (e) { toast.error(errMsg(e)); }
  });

  const ablehnen = (req) => aktion(req.id, async () => {
    try {
      await closeReq(req.id, "abgelehnt");
      toast.success("Anfrage abgelehnt");
    } catch (e) { toast.error(errMsg(e, "Ablehnen fehlgeschlagen")); }
  });

  const setBuyerAccess = (buyer, activate) => aktion(`k-${buyer.id}`, async () => {
    try {
      await api.post(`/admin/buyers/${buyer.id}/access`, { plan: activate ? "monthly" : null });
      toast.success(activate ? "Zugang aktiviert" : "Zugang gesperrt");
    } catch (e) { toast.error(errMsg(e)); }
  });

  const ZUGANG_ART = {
    firma: { label: "Neue Firma", tone: "green" },
    kaeufer: { label: "Neuer Zwischenhändler", tone: "blue" },
    fahrer: { label: "Neuer Fahrer", tone: "orange" },
  };

  return (
    <div>
      <PageHeader
        title="Freischaltungen"
        subtitle="Zugänge anlegen, Sucher-Abos, Marktplatz-Zugänge & offene Anfragen — manuell freigeben"
        action={<Button variant="secondary" size="sm" onClick={load}><RefreshCw size={14} /> Aktualisieren</Button>}
      />

      {loading && !requests ? (
        <div className="flex items-center gap-2 text-zinc-500 text-sm"><Spinner /> lade…</div>
      ) : (
        <div className="space-y-8">
          {/* Offene Anfragen */}
          <div>
            <div className="text-[13px] font-semibold text-zinc-300 mb-3 uppercase tracking-wide">
              Offene Anfragen {requests?.length ? `(${requests.length})` : ""}
            </div>
            {gekuerzt && (
              <div className="mb-3 rounded-xl border px-4 py-3 text-sm" data-testid="freischaltungen-gekuerzt"
                   style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "#fbbf24" }}>
                Die Liste ist gekürzt — es gibt mehr Einträge, als hier angezeigt werden. Bitte zuerst die
                sichtbaren bearbeiten, danach neu laden.
              </div>
            )}
            {anfragenFehler ? (
              <div className="rounded-xl border px-4 py-3 text-sm text-red-300" role="alert"
                   data-testid="freischaltungen-ladefehler" style={{ borderColor: "#ef444455", background: "#ef444414" }}>
                {anfragenFehler} — ob Anfragen warten, ist gerade UNBEKANNT.{" "}
                <button type="button" onClick={load} className="underline underline-offset-2 font-semibold text-white">
                  Erneut laden
                </button>
              </div>
            ) : !requests?.length ? (
              <EmptyState title="Keine offenen Anfragen" hint="Neue Sucher-Abo- und Zugangsanfragen erscheinen hier." />
            ) : (
              <div className="space-y-3">
                {requests.map((r) => {
                  const isSucher = r.type === "sucher_abo";
                  const isBuyer = r.type === "buyer_access";
                  const isZugang = r.type === "zugang";
                  const art = ZUGANG_ART[r.art] ? r.art : "firma";
                  return (
                    <Card key={r.id} padded={false}>
                      <div className="p-4 flex flex-wrap items-center gap-3" data-testid={`anfrage-${r.id}`}>
                        <Badge tone={isZugang ? ZUGANG_ART[art].tone : isSucher ? "purple" : isBuyer ? "blue" : "gray"}>
                          {isZugang ? ZUGANG_ART[art].label : isSucher ? "Sucher-Abo" : isBuyer ? "Marktplatz-Zugang" : (r.type || "Paket")}
                        </Badge>
                        <div className="min-w-0">
                          <div className="text-[14px] text-white font-medium">
                            {isSucher ? (
                              <>
                                {r.subject_role === "dealer" ? "Chef " : "Sucher "}
                                {r.sucher_name || r.kontonummer || r.sucher_email}
                                {r.company_name ? ` von Firma ${r.company_name}` : ""}
                                {r.kunden_nr != null ? ` (#${r.kunden_nr})` : ""}
                                {" möchte das Sucher-Abo verlängern"}
                                <span className="text-zinc-500 font-normal">
                                  {" · "}{r.wanted_plan === "yearly" ? "1 Jahr · 1.500 €" : "1 Monat · 150 €"}
                                </span>
                              </>
                            ) : (
                              <>
                                {r.company_name}
                                <span className="text-zinc-500 font-normal"> · {r.wanted || (r.wanted_tier ? `Verkaufspaket ${r.wanted_tier}` : "")}</span>
                              </>
                            )}
                          </div>
                          <div className="text-[12px] text-zinc-500">
                            {isZugang ? (r.contact_person || "") : r.company_name}
                            {r.contact_email ? ` · ${r.contact_email}` : ""}
                            {isZugang && r.contact_phone ? ` · ${r.contact_phone}` : ""}
                            {isZugang && art === "kaeufer" && r.ust_id ? ` · USt ${r.ust_id}` : ""}
                            {isZugang && art === "kaeufer" && r.gewerblich_bestaetigt_am ? " · B2B bestätigt" : ""}
                            {" · "}{fmtDate(r.created_at)}
                          </div>
                          {isZugang && r.message ? (
                            <div className="text-[12px] text-zinc-400 mt-1 max-w-xl whitespace-pre-wrap">{r.message}</div>
                          ) : null}
                        </div>
                        <div className="ml-auto flex gap-2">
                          {isZugang && art === "firma" && (
                            <Button size="sm" onClick={() => setFirmaReq(r)} data-testid={`firma-anlegen-${r.id}`}>
                              <Building2 size={14} /> Firma anlegen
                            </Button>
                          )}
                          {isZugang && art === "kaeufer" && (
                            <Button size="sm" onClick={() => setKaeuferDialog({ request: r })} data-testid={`kaeufer-anlegen-${r.id}`}>
                              <Store size={14} /> Käufer anlegen
                            </Button>
                          )}
                          {isZugang && art === "fahrer" && (
                            <Button size="sm" onClick={() => setFahrerReq(r)} data-testid={`fahrer-anlegen-${r.id}`}>
                              <Truck size={14} /> Fahrer anlegen
                            </Button>
                          )}
                          {isSucher && <Button size="sm" onClick={() => grantSucher(r)} disabled={!!arbeitet} data-testid={`abo-ja-${r.id}`}><Check size={14} /> Ja, freischalten</Button>}
                          {isBuyer && <Button size="sm" onClick={() => grantBuyer(r)} disabled={!!arbeitet}><Check size={14} /> Zugang aktivieren</Button>}
                          {!isZugang && !isSucher && !isBuyer && r.wanted_tier && r.dealer_id && (
                            <Button size="sm" onClick={() => grantPlan(r)} disabled={!!arbeitet}><Check size={14} /> Paket aktivieren</Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => ablehnen(r)} disabled={!!arbeitet} data-testid={`abo-nein-${r.id}`}>
                            <X size={14} /> {isSucher ? "Nein, ablehnen" : "Ablehnen"}
                          </Button>
                        </div>
                      </div>
                    </Card>
                  );
                })}
              </div>
            )}
          </div>

          {/* Zwischenhändler */}
          <div>
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="text-[13px] font-semibold text-zinc-300 uppercase tracking-wide inline-flex items-center gap-1.5">
                <Store size={14} /> Zwischenhändler {buyers?.length ? `(${buyers.length})` : ""}
              </div>
              <Button size="sm" variant="outline" onClick={() => setKaeuferDialog({ request: null })} data-testid="kaeufer-anlegen-btn">
                <UserPlus size={14} /> Käufer anlegen
              </Button>
            </div>
            {!buyers?.length ? (
              <EmptyState title="Noch keine Zwischenhändler" hint="Angelegte B2B-Käufer erscheinen hier." />
            ) : (
              <Card padded={false}>
                <div className="overflow-x-auto">
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr className="text-left text-zinc-500 text-[11px] uppercase tracking-wide">
                        <th className="px-4 py-3 font-medium">Kontonummer</th>
                        <th className="px-4 py-3 font-medium">Firma</th>
                        <th className="px-4 py-3 font-medium">E-Mail</th>
                        <th className="px-4 py-3 font-medium">USt-IdNr.</th>
                        <th className="px-4 py-3 font-medium">Zugang</th>
                        <th className="px-4 py-3 font-medium">Läuft ab</th>
                        <th className="px-4 py-3 font-medium text-right">Aktion</th>
                      </tr>
                    </thead>
                    <tbody>
                      {buyers.map((b) => (
                        <tr key={b.id} className="border-t border-white/5" data-testid={`buyer-row-${b.id}`}>
                          <td className="px-4 py-2.5 font-mono text-white" data-testid={`buyer-kontonummer-${b.id}`}>
                            {b.kontonummer || "—"}
                          </td>
                          <td className="px-4 py-2.5 text-white font-medium">
                            {b.company_name}
                            <div className="text-[11px] text-zinc-500 font-normal">{b.contact_name}</div>
                          </td>
                          <td className="px-4 py-2.5 text-zinc-400">{b.email || <span className="text-zinc-600">—</span>}</td>
                          <td className="px-4 py-2.5 text-zinc-400" data-testid={`ustid-${b.id}`}>
                            {b.ust_id ? (
                              <div className="flex items-center gap-2">
                                <span className="font-mono text-[12px]">{b.ust_id}</span>
                                {b.ust_id_pruefung && (
                                  <span title={b.ust_id_pruefung.hinweis || b.ust_id_pruefung.name || ""}>
                                    <Badge tone={b.ust_id_pruefung.status === "gueltig" ? "green" : b.ust_id_pruefung.status === "ungueltig" ? "red" : "yellow"}>
                                      {b.ust_id_pruefung.status === "gueltig" ? "VIES ok" : b.ust_id_pruefung.status === "ungueltig" ? "VIES ungültig" : "nicht prüfbar"}
                                    </Badge>
                                  </span>
                                )}
                                <Button size="sm" variant="ghost" onClick={() => pruefeUstId(b)} title="Beim EU-Dienst VIES prüfen">Prüfen</Button>
                              </div>
                            ) : <span className="text-zinc-600">—</span>}
                          </td>
                          <td className="px-4 py-2.5">
                            <Badge tone={b.access?.active ? "green" : "red"}>
                              {b.access?.active ? "aktiv" : "gesperrt"}
                            </Badge>
                          </td>
                          <td className="px-4 py-2.5 text-zinc-500 tabular-nums">{fmtDate(b.access?.expires_at)}</td>
                          <td className="px-4 py-2.5 text-right whitespace-nowrap">
                            <Button size="sm" variant="ghost" onClick={() => setPwBuyer(b)} data-testid={`buyer-pw-btn-${b.id}`}
                                    title="Neues Passwort setzen (beendet die Sitzung, hebt eine Anmeldesperre auf)">
                              <KeyRound size={13} /> Passwort setzen
                            </Button>
                            {b.access?.active ? (
                              <Button size="sm" variant="ghost" onClick={() => setBuyerAccess(b, false)} disabled={!!arbeitet}>Sperren</Button>
                            ) : (
                              <Button size="sm" onClick={() => setBuyerAccess(b, true)} disabled={!!arbeitet}>Freischalten</Button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </div>
        </div>
      )}

      {firmaReq && (
        <FirmaAnlegenDialog
          request={firmaReq}
          onClose={() => { setFirmaReq(null); load(); }}
        />
      )}
      {kaeuferDialog && (
        <KaeuferAnlegenDialog
          request={kaeuferDialog.request}
          onClose={() => { setKaeuferDialog(null); load(); }}
        />
      )}
      {fahrerReq && (
        <FahrerAnlegenDialog
          request={fahrerReq}
          onClose={() => { setFahrerReq(null); load(); }}
        />
      )}
      {pwBuyer && (
        <PasswortSetzenDialog konto={pwBuyer} onClose={() => setPwBuyer(null)} />
      )}
    </div>
  );
}


// Admin-Dialoge sind fest dunkel (wie Fahrer.jsx). Das fruehere
// "bg-white dark:bg-zinc-900" griff nie: Tailwind erwartet dafuer eine
// "dark"-Klasse am Dokument, die die App nicht setzt — der Dialog war
// weiss, Titel und vorbefuellte Felder (weisse Schrift) unsichtbar.
const inputCls = "w-full rounded-lg px-3 py-2 text-sm outline-none";
const inputStyle = { background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" };

function DialogRahmen({ titel, testid, onClose, children, schliessbar = true }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.6)" }}>
      <div className="w-full max-w-md rounded-2xl p-5"
           style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)" }}
           data-testid={testid}>
        <div className="flex items-center justify-between mb-1">
          <div className="text-lg font-bold text-white">{titel}</div>
          {/* AD-08: mit angezeigtem Passwort nur ueber "Fertig" schliessen */}
          {schliessbar && <button onClick={onClose} className="text-zinc-400 hover:text-zinc-200" aria-label="Schließen"><X size={20} /></button>}
        </div>
        {children}
      </div>
    </div>
  );
}

/** Firmen-Konto direkt aus einer Zugangs-Anfrage anlegen (plan_type "none":
 *  der Hauptaccount ist kostenlos, Sucher-Abos werden separat freigeschaltet).
 *  Kontonummer (13.09.2026): Kontakt-E-Mail optional, anfrage_id schliesst
 *  die Anfrage im Backend; danach die Zugangsdaten. */
function FirmaAnlegenDialog({ request, onClose }) {
  const [f, setF] = useState({
    company_name: request.company_name || "",
    contact_person: request.contact_person || "",
    phone: request.contact_phone || "",
    email: request.contact_email || "",
    password: "",
  });
  const [busy, setBusy] = useState(false);
  const [ergebnis, setErgebnis] = useState(null);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (!f.company_name.trim()) { toast.error("Firma angeben"); return; }
    const problem = passwortProblem(f.password);
    if (problem) { toast.error(problem); return; }
    setBusy(true);
    try {
      const { data } = await mitAbweichung((extra) => api.post("/admin/users", {
        email: f.email.trim(), password: f.password,
        company_name: f.company_name.trim(), contact_person: f.contact_person.trim(),
        phone: f.phone.trim(), plan_type: "none", anfrage_id: request.id, ...extra,
      }));
      setErgebnis({ ...data, name: f.company_name.trim(), passwort: f.password });
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  return (
    <DialogRahmen titel="Firma anlegen" testid="firma-anlegen-dialog" onClose={onClose} schliessbar={!ergebnis}>
      {ergebnis ? (
        <ZugangsdatenKarte titel="Firmen-Konto angelegt" name={ergebnis.name} kontonummer={ergebnis.kontonummer}
                           passwort={ergebnis.passwort}
                           bereich="app" hinweis="Sucher danach über die Nutzer-Detailseite anlegen."
                           onClose={onClose} />
      ) : (<>
        <div className="text-[12px] text-zinc-500 mb-4">
          Aus Anfrage: {request.contact_person || "—"}
          {request.sucher_anzahl ? ` · gewünschte Sucher: ${request.sucher_anzahl}` : ""}
          {" — Sucher danach über die Nutzer-Detailseite anlegen."}
        </div>
        <div className="space-y-3">
          <input value={f.company_name} onChange={set("company_name")} placeholder="Firmenname *"
                 className={inputCls} style={inputStyle} autoFocus />
          <div className="grid grid-cols-2 gap-3">
            <input value={f.contact_person} onChange={set("contact_person")} placeholder="Ansprechpartner"
                   className={inputCls} style={inputStyle} />
            <input value={f.phone} onChange={set("phone")} placeholder="Telefon"
                   className={inputCls} style={inputStyle} />
          </div>
          <input value={f.email} onChange={set("email")} placeholder="Kontakt-E-Mail (optional)"
                 type="email" className={inputCls} style={inputStyle} />
          <PasswortFeld value={f.password} onChange={(v) => setF((s) => ({ ...s, password: v }))}
                        placeholder="Start-Passwort (mind. 10 Zeichen, Ziffer oder Sonderzeichen) *"
                        testid="firma-anlegen-passwort" className={inputCls} style={inputStyle} />
        </div>
        <Button className="mt-4 w-full" onClick={submit} disabled={busy}>
          {busy ? "Wird angelegt…" : "Firmen-Konto anlegen"}
        </Button>
      </>)}
    </DialogRahmen>
  );
}

/** Kontonummer (13.09.2026): Zwischenhändler (b2b_buyer) anlegen — aus einer
 *  Anfrage (art=kaeufer) oder frei. Der Betreiber bestätigt den B2B-Nachweis
 *  ausdrücklich (AGB §1); das Backend lehnt die Anlage ohne ihn ab. */
function KaeuferAnlegenDialog({ request, onClose }) {
  const [f, setF] = useState({
    company_name: request?.company_name || "",
    contact_name: request?.contact_person || "",
    phone: request?.contact_phone || "",
    email: request?.contact_email || "",
    ust_id: request?.ust_id || "",
    password: "",
  });
  const [nachweis, setNachweis] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ergebnis, setErgebnis] = useState(null);
  const set = (k) => (e) => setF((s) => ({ ...s, [k]: e.target.value }));

  const submit = async () => {
    if (f.company_name.trim().length < 2 || f.contact_name.trim().length < 2) {
      toast.error("Firma und Ansprechpartner angeben"); return;
    }
    const problem = passwortProblem(f.password);
    if (problem) { toast.error(problem); return; }
    if (!nachweis) {
      toast.error("Bitte bestätigen, dass der B2B-Nachweis vorliegt"); return;
    }
    setBusy(true);
    try {
      const { data } = await mitAbweichung((extra) => api.post("/admin/buyers", {
        company_name: f.company_name.trim(), contact_name: f.contact_name.trim(),
        phone: f.phone.trim(), email: f.email.trim(), ust_id: f.ust_id.trim(),
        password: f.password, b2b_nachweis: nachweis,
        ...(request?.id ? { anfrage_id: request.id } : {}), ...extra,
      }));
      setErgebnis({ ...data, name: f.company_name.trim(), passwort: f.password });
    } catch (e) { toast.error(errMsg(e, "Käufer anlegen fehlgeschlagen")); }
    finally { setBusy(false); }
  };

  return (
    <DialogRahmen titel="Zwischenhändler anlegen" testid="kaeufer-anlegen-dialog" onClose={onClose} schliessbar={!ergebnis}>
      {ergebnis ? (
        <ZugangsdatenKarte titel="Zwischenhändler angelegt" name={ergebnis.name} kontonummer={ergebnis.kontonummer}
                           passwort={ergebnis.passwort}
                           hinweis="Der Käufer-Code ist die Anmeldekennung im B2B-Marktplatz (Groß-/Kleinschreibung egal)."
                           bereich="kaeufer" onClose={onClose} />
      ) : (<>
        <div className="text-[12px] text-zinc-500 mb-4">
          {request
            ? `Aus Anfrage vom ${fmtDate(request.created_at)}${request.gewerblich_bestaetigt_am ? " · B2B im Formular bestätigt" : ""}`
            : "Die Kontonummer vergibt das System."}
          {" — E-Mail ist nur Kontaktadresse (optional)."}
        </div>
        <div className="space-y-3">
          <input value={f.company_name} onChange={set("company_name")} placeholder="Firma *"
                 data-testid="kaeufer-anlegen-firma" className={inputCls} style={inputStyle} autoFocus />
          <div className="grid grid-cols-2 gap-3">
            <input value={f.contact_name} onChange={set("contact_name")} placeholder="Ansprechpartner *"
                   data-testid="kaeufer-anlegen-name" className={inputCls} style={inputStyle} />
            <input value={f.phone} onChange={set("phone")} placeholder="Telefon"
                   className={inputCls} style={inputStyle} />
          </div>
          <input value={f.email} onChange={set("email")} placeholder="Kontakt-E-Mail (optional)" type="email"
                 data-testid="kaeufer-anlegen-email" className={inputCls} style={inputStyle} />
          <input value={f.ust_id} onChange={set("ust_id")} maxLength={40}
                 placeholder="USt-IdNr. oder Handelsregister-Nr. (optional)"
                 className={inputCls} style={inputStyle} />
          <PasswortFeld value={f.password} onChange={(v) => setF((s) => ({ ...s, password: v }))}
                        testid="kaeufer-anlegen-passwort" className={inputCls} style={inputStyle} />
          <label className="flex items-start gap-2.5 text-[13px] text-zinc-300 cursor-pointer select-none">
            <input type="checkbox" checked={nachweis} onChange={(e) => setNachweis(e.target.checked)}
                   data-testid="kaeufer-anlegen-b2b" className="mt-0.5 h-4 w-4 shrink-0"
                   style={{ accentColor: "var(--accent-red)" }} />
            <span>B2B-Nachweis liegt vor (gewerblicher Händler, AGB §1)</span>
          </label>
        </div>
        <Button className="mt-4 w-full" onClick={submit} disabled={busy} data-testid="kaeufer-anlegen-submit">
          {busy ? "Wird angelegt…" : "Käufer-Konto anlegen"}
        </Button>
      </>)}
    </DialogRahmen>
  );
}

/** Passwort eines Zwischenhändlers setzen — einziger Weg bei "Passwort
 *  vergessen" (Kontonummer 13.09.2026). Beendet die Sitzung und hebt eine
 *  Anmeldesperre des Kontos auf. */
function PasswortSetzenDialog({ konto, onClose }) {
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (pw.length < 8) { toast.error("Mindestens 8 Zeichen"); return; }
    setBusy(true);
    try {
      await api.post(`/admin/users/${konto.id}/password`, { new_password: pw });
      toast.success("Passwort gesetzt — Sitzung beendet, Anmeldesperre aufgehoben");
      onClose();
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };
  return (
    <DialogRahmen titel="Passwort setzen" testid="buyer-pw-dialog" onClose={onClose}>
      <div className="text-[12.5px] text-zinc-500">
        {konto.company_name || konto.contact_name || "Zwischenhändler"}
        {konto.kontonummer ? ` · Kontonummer ${konto.kontonummer}` : ""}
      </div>
      <input type="text" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus
             placeholder="Neues Passwort (min. 8, Ziffer oder Sonderzeichen)"
             data-testid="buyer-pw-input" className={`${inputCls} mt-3`} style={inputStyle} />
      <div className="mt-4 flex gap-2 justify-end">
        <Button variant="ghost" onClick={onClose}>Abbrechen</Button>
        <Button onClick={submit} disabled={busy} data-testid="buyer-pw-submit">Setzen</Button>
      </div>
    </DialogRahmen>
  );
}
