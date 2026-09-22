import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { useFeatures } from "@/lib/features";
import { ungespeichertMelden } from "@/lib/ungespeichert";
import { kmAusText } from "@/lib/preis";
import { aboKontextVeraltet, planText } from "@/lib/abo";

// Vorlage Ahmad 20.09.2026: dieselben Namen wie im Backend
// (backend/vertrag_platzhalter.py). Der Test test_vertragstexte_20260920.py
// haelt beide Listen zusammen — wird hier einer ergaenzt, muss er dort auch
// stehen, sonst bliebe er im PDF woertlich stehen.
// Der Standardsatz, wie er im Vertrag steht — zur Anschauung neben dem
// Schalter. Muss mit backend/vertrag_vorlagen.BESONDERE_VEREINBARUNGEN
// uebereinstimmen; test_vertragstexte_20260920.py haelt beide zusammen.
const STANDARD_SONDERSATZ =
  "• Die Fahrzeugübergabe findet bis/am {abholdatum} in {ort} gegen {zahlungsart} statt.\n"
  + "• Das Fahrzeug wird nur unter Vorlage der Kundennummer ({kundennummer}) nach einem "
  + "kurzen Gebrauchtwagencheck und Datenabgleich mit Zulassungsbescheinigung Teil I & II "
  + "ausgehändigt.";

const PLATZHALTER = [
  "{kunde_name}", "{fahrzeug}", "{marke}", "{modell}",
  "{abholdatum}", "{ort}", "{zahlungsart}", "{kaufpreis}",
  "{vertragsnummer}", "{kundennummer}",
  "{händler_name}", "{telefon}", "{email}",
];
import { toast } from "sonner";
import { vertragstextFuerFormular } from "@/lib/vertragstext";
import KopierKnopf from "@/components/KopierKnopf";
import {
  Building2, Sliders, FileText, Mail, MessageSquare, ShieldCheck, Save, Check, Globe,
  CreditCard, Calendar, X, ArrowRight, Bolt, Store,
} from "lucide-react";
import CountryPicker from "@/components/CountryPicker";

// Formular aus dem (wirksamen) Haendlerdokument — ohne reine UI-Felder.
export function formAus(dealer) {
  // Rollenprüfung 22.09.2026 (RP-423): bei leeren Bedingungen und noch
  // vorhandenen AGB ist der Standardtext die Grundlage — sonst ersetzte der
  // AGB-Text beim Speichern die vier Standardklauseln.
  const vt = vertragstextFuerFormular(dealer.default_terms, dealer.digital_vertragstext,
                                      dealer.digital_vertragstext_standard);
  return {
    profile: {
      company_name: dealer.company_name || "", contact_person: dealer.contact_person || "",
      phone: dealer.phone || "", whatsapp_number: dealer.whatsapp_number || dealer.phone || "",
      email: dealer.email || "", address: dealer.address || "",
      zip_code: dealer.zip_code || "", city: dealer.city || "",
      opening_hours: dealer.opening_hours || "", logo_url: dealer.logo_url || "",
    },
    comparison_rules: dealer.comparison_rules || {},
    export_rules: dealer.export_rules || {},
    active_profile: dealer.active_profile || "inland",
    email_subject: dealer.email_subject || "",
    email_template: dealer.email_template || "",
    whatsapp_template: dealer.whatsapp_template || "",
    // Vorlage Ahmad 20.09.2026: Korrektur (Versand) und die Vorlagen zum
    // Kopieren (21.09.2026: die App verschickt sie nicht)
    email_subject_korrektur: dealer.email_subject_korrektur || "",
    email_template_korrektur: dealer.email_template_korrektur || "",
    email_subject_nach_kauf: dealer.email_subject_nach_kauf || "",
    email_template_nach_kauf: dealer.email_template_nach_kauf || "",
    whatsapp_template_nach_kauf: dealer.whatsapp_template_nach_kauf || "",
    email_subject_bahn: dealer.email_subject_bahn || "",
    email_template_bahn: dealer.email_template_bahn || "",
    sondervereinbarung_standard_aktiv:
      dealer.sondervereinbarung_standard_aktiv !== false,
    // Runde 26: EIN Feld. Ein noch vorhandener AGB-Text wird hier
    // angehaengt; beim Speichern wird das alte Feld geleert.
    default_terms: "",
    default_special_agreements: dealer.default_special_agreements || "",
    digital_vertragstext: vt.text,
    _agb_zusammengefuehrt: vt.zusammengefuehrt,
    _agb_standard_genutzt: vt.standardGenutzt,
  };
}

const gleich = (x, y) => JSON.stringify(x ?? null) === JSON.stringify(y ?? null);

/**
 * Pruefbericht 20.09.2026 (H37): Nach dem Logo-Hochladen (oder jedem anderen
 * Neuladen des Kontexts) wurde das Formular komplett aus den Serverwerten neu
 * gebaut — Firmenname, Oeffnungszeiten, AGB-Text: alles Ungespeicherte weg.
 * Jetzt bleiben Felder, die seit dem letzten Serverstand geaendert wurden,
 * stehen; alle anderen kommen frisch vom Server.
 */
export function mitOffenenAenderungen(alt, alterStand, neu) {
  const out = { ...neu, _edit_profile: alt?._edit_profile || "inland" };
  if (!alt || !alterStand) return out;
  for (const k of Object.keys(neu)) {
    if (k.startsWith("_")) continue;
    if (k === "profile") {
      const p = { ...neu.profile };
      for (const pk of Object.keys(neu.profile)) {
        if (!gleich(alt.profile?.[pk], alterStand.profile?.[pk])) p[pk] = alt.profile[pk];
      }
      out.profile = p;
    } else if (!gleich(alt[k], alterStand[k])) {
      out[k] = alt[k];
    }
  }
  return out;
}

/**
 * Pruefbericht 20.09.2026 (B19/F18): Das Formular schickte beim Speichern
 * ALLE Werte zurueck, darunter abgeleitete (WhatsApp faellt auf die
 * Telefonnummer zurueck, AGB-Feld immer leer, Vertragstext zusammengefuehrt).
 * Fuer einen Sucher wich das vom Chef-Wert ab und wurde als persoenlicher
 * Wert eingefroren — spaetere Aenderungen des Chefs kamen nie mehr an.
 * Sucher senden deshalb nur, was sie wirklich geaendert haben.
 * default_terms und digital_vertragstext gehoeren zusammen (Runde 26).
 */
export function nurGeaenderte(payload, stand) {
  if (!stand) return payload;
  const out = {};
  for (const [k, v] of Object.entries(payload)) {
    if (k === "default_terms") continue;
    if (k === "profile") {
      const p = {};
      for (const [pk, pv] of Object.entries(v || {})) {
        if (!gleich(pv, stand.profile?.[pk])) p[pk] = pv;
      }
      if (Object.keys(p).length) out.profile = p;
    } else if (!gleich(v, stand[k])) {
      out[k] = v;
    }
  }
  if ("digital_vertragstext" in out) out.default_terms = "";
  return out;
}

/**
 * Was beim Speichern an PUT /dealer/settings geht.
 *
 * Rollenprüfung 22.09.2026:
 *   RP-005/RP-104/RP-255/RP-425: active_profile ging mit — aus einem veralteten
 *     Kontext (Wechsel über das Profil-Abzeichen im Vergleich). Jedes
 *     Speichern drehte Export still auf Inland zurück, für die ganze Firma.
 *     Der Live-Schalter hat seine eigene Route (/dealer/active-profile) und
 *     gehört nie in dieses Formular.
 *   RP-426/RP-138: Der Chef schickte das GANZE Formular. Ein zweiter Tab oder
 *     Handy und PC setzten damit alle Felder des anderen zurück — auch die
 *     Logo-Adresse, deren Datei beim neuen Hochladen schon gelöscht war.
 *     Jetzt schickt auch der Chef nur, was er wirklich geändert hat.
 *     Ausnahme: sind alte AGB in das Vertragstext-Feld gewandert (Hinweis
 *     "bitte einmal speichern"), geht der zusammengeführte Text mit, damit das
 *     alte Feld geleert wird. Bei Suchern nicht — das würde die Chef-Vorgabe
 *     als persönlichen Wert einfrieren (B19).
 */
export function speicherPayload(form, stand, { istChef = false } = {}) {
  const { _edit_profile, _agb_zusammengefuehrt, _agb_standard_genutzt, active_profile, ...alles } = form || {};
  let basis = stand;
  if (stand && "active_profile" in stand) {
    basis = { ...stand };
    delete basis.active_profile;
  }
  const payload = { ...nurGeaenderte(alles, basis) };
  if (istChef && _agb_zusammengefuehrt) {
    payload.digital_vertragstext = alles.digital_vertragstext;
    payload.default_terms = "";
  }
  return payload;
}

// Anzeige der persoenlich ueberschriebenen Felder (Sucher).
const FELD_TITEL = {
  company_name: "Firmenname", contact_person: "Ansprechpartner", phone: "Telefon",
  whatsapp_number: "WhatsApp-Nummer", email: "E-Mail", address: "Adresse",
  zip_code: "PLZ", city: "Ort", opening_hours: "Öffnungszeiten",
  comparison_rules: "Vergleichsregeln Inland", export_rules: "Vergleichsregeln Export",
  active_profile: "aktives Profil",
  email_subject: "E-Mail-Betreff", email_template: "E-Mail-Vorlage",
  whatsapp_template: "WhatsApp-Vorlage",
  email_subject_korrektur: "Betreff erneuter Versand (Korrektur)",
  email_template_korrektur: "Text erneuter Versand (Korrektur)",
  email_subject_nach_kauf: "Hinweis nach Kaufabschluss (Betreff)",
  email_template_nach_kauf: "Hinweis nach Kaufabschluss (E-Mail)",
  whatsapp_template_nach_kauf: "Hinweis nach Kaufabschluss (WhatsApp)",
  email_subject_bahn: "Bahnverbindung (Betreff)", email_template_bahn: "Bahnverbindung (E-Mail)",
  default_terms: "AGB", default_special_agreements: "Besondere Vereinbarungen",
  digital_vertragstext: "Vertragstext", sondervereinbarung_standard_aktiv: "Standardsatz an/aus",
};

const SECTIONS = [
  { id: "profile",    label: "Profil",         icon: Building2 },
  { id: "rules",      label: "Vergleich",      icon: Sliders },
  { id: "templates",  label: "Versand",        icon: Mail },
  { id: "markt",      label: "Marktplatz",     icon: Store },
  { id: "agb",        label: "Vertragstexte", icon: ShieldCheck },
  { id: "abo",        label: "Abo",            icon: CreditCard },
];

export default function Einstellungen() {
  const { dealer, refresh, user } = useAuth();
  // Sucher sehen keinen Marktplatz-Reiter (nur der Chef verwaltet den
  // Marktplatz; die Endpunkte antworten Suchern mit 403 -> ewig "Lädt…").
  const features = useFeatures();          // Go-Live-Schalter: Marktplatz-Reiter nur wenn frei
  const sections = (user?.role === "sucher" || !features.marktplatz)
    ? SECTIONS.filter((s) => s.id !== "markt")
    : SECTIONS;
  const [form, setForm] = useState(null);
  const [active, setActive] = useState("profile");
  const [savedFlash, setSavedFlash] = useState(false);
  const [speichert, setSpeichert] = useState(false);
  // Letzter Serverstand des Formulars — Grundlage fuer "was hat sich
  // geaendert" (B19) und fuer das Erhalten ungespeicherter Eingaben (H37).
  const ausgangRef = useRef(null);
  const istChef = user?.role === "dealer";
  // Rollenprüfung 22.09.2026 (RP-414): Eingabefelder mit unlesbarem Wert
  // (z. B. Kilometer "50.0") — solange einer da ist, wird nicht gespeichert.
  const [feldFehler, setFeldFehler] = useState({});
  const feldFehlerSetzen = useCallback((feld, text) => {
    setFeldFehler((s) => ((s[feld] || "") === (text || "") ? s : { ...s, [feld]: text || "" }));
  }, []);

  // Pruefbericht 20.09.2026 (U-130): Ungespeicherte Aenderungen gingen beim
  // Verlassen/Neuladen ohne Rueckfrage verloren. Jetzt meldet die Seite sie
  // an, solange das Formular vom letzten Serverstand abweicht.
  // Rollenprüfung 22.09.2026: der Live-Schalter (active_profile) zählt nicht
  // — er wird sofort über seine eigene Route gespeichert.
  const geaendert = !!form && !!ausgangRef.current
    && Object.keys(speicherPayload(form, ausgangRef.current)).filter((k) => k !== "default_terms").length > 0;
  useEffect(() => (geaendert ? ungespeichertMelden() : undefined), [geaendert]);

  useEffect(() => {
    // 16.09.2026: nach "Speichern" (refresh) bleibt der Regel-Editor auf dem
    // Profil, das gerade bearbeitet wurde — vorher sprang er auf Inland
    // zurueck und die eben gespeicherten Export-Regeln schienen verschwunden.
    if (!dealer) return;
    const neu = formAus(dealer);
    const alterStand = ausgangRef.current;   // VOR dem Ueberschreiben merken
    ausgangRef.current = neu;
    setForm((alt) => mitOffenenAenderungen(alt, alterStand, neu));
  }, [dealer]);

  if (!form) return <div className="p-10 text-zinc-500">Lade…</div>;

  // Welches Regel-Paket der Editor gerade bearbeitet (Inland vs. Export).
  // Eigenständig vom `active_profile` (das ist der Live-Schalter), damit
  // der Händler beide Profile anpassen kann, egal welches gerade aktiv ist.
  const editProfile = form._edit_profile || "inland";
  const setEditProfile = (p) => setForm({ ...form, _edit_profile: p });
  const rulesKey = editProfile === "export" ? "export_rules" : "comparison_rules";

  const setProfile = (k, v) => setForm({ ...form, profile: { ...form.profile, [k]: v } });

  const uploadLogo = async (file) => {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024) { toast.error("Logo zu groß (max. 2 MB)"); return; }
    try {
      const b64 = await new Promise((res, rej) => {
        const r = new FileReader();
        r.onload = () => res(r.result); r.onerror = rej; r.readAsDataURL(file);
      });
      const { data } = await api.post("/dealer/logo", { logo_b64: b64 });
      setProfile("logo_url", data.logo_url);
      await refresh();
      toast.success("Logo hochgeladen");
    } catch (e) { toast.error(errMsg(e, "Logo konnte nicht hochgeladen werden")); }
  };
  const setRule = (key, val) => setForm({
    ...form,
    [rulesKey]: { ...(form[rulesKey] || {}), [key]: val },
  });

  const save = async () => {
    if (speichert) return;
    const offenerFehler = Object.values(feldFehler).find(Boolean);
    if (offenerFehler) {
      toast.error(`Bitte zuerst korrigieren: ${offenerFehler}`);
      return;
    }
    // B19: Sucher schicken nur echte Aenderungen (sonst wurden geerbte
    // Chef-Werte als persoenliche Werte eingefroren). Rollenprüfung
    // 22.09.2026 (RP-426/RP-138/RP-425): der Chef jetzt ebenso, und
    // active_profile nie (siehe speicherPayload).
    const payload = speicherPayload(form, ausgangRef.current, { istChef });
    if (Object.keys(payload).length === 0) {
      toast.info("Keine Änderungen zum Speichern.");
      return;
    }
    setSpeichert(true);
    try {
      await api.put("/dealer/settings", payload);
      // Nach dem Speichern gilt der Serverstand — nichts mehr "offen".
      ausgangRef.current = null;
      await refresh();
      toast.success("Einstellungen gespeichert");
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (err) {
      toast.error(errMsg(err, "Fehler beim Speichern"));
    } finally {
      setSpeichert(false);
    }
  };

  // B19: Weg zurueck zu den Vorgaben des Chefs.
  const eigene = (!istChef && Array.isArray(dealer?.eigene_einstellungen))
    ? dealer.eigene_einstellungen.filter((k) => k !== "active_profile") : [];
  const aufChefZuruecksetzen = async () => {
    if (!window.confirm("Alle eigenen Werte löschen? Danach gelten wieder die Vorgaben deines Chefs "
      + "(auch für künftige Änderungen).")) return;
    if (!eigene.length) return;
    try {
      // Rollenprüfung 22.09.2026 (RP-005): ohne Feldliste löschte der Server
      // ALLE persönlichen Werte — auch das eigene aktive Profil (Inland/
      // Export), das hier gar nicht aufgeführt ist. Jetzt nur die angezeigten.
      await api.post("/dealer/settings/zuruecksetzen", { felder: eigene });
      ausgangRef.current = null;
      setForm(null);
      await refresh();
      toast.success("Zurückgesetzt — es gelten wieder die Vorgaben deines Chefs.");
    } catch (err) {
      toast.error(errMsg(err, "Zurücksetzen fehlgeschlagen"));
    }
  };

  const setActiveProfile = async (p) => {
    const vorher = form.active_profile;
    setForm({ ...form, active_profile: p });
    try {
      await api.put("/dealer/active-profile", { active_profile: p });
      toast.success(p === "inland" ? "Inland-Profil aktiv" : "Export-Profil aktiv");
      // Rollenprüfung 22.09.2026 (RP-255): Kontext nachziehen, damit Vergleich
      // und manuelle Suche das neue Profil zeigen.
      refresh();
    } catch (err) {
      // Runde 16 (15.09.2026): bei Fehler den alten Stand wieder anzeigen —
      // sonst stand "Export aktiv" da, waehrend der Server Inland behielt.
      setForm((f) => ({ ...f, active_profile: vorher }));
      toast.error(errMsg(err, "Wechsel fehlgeschlagen"));
    }
  };

  const r = form[rulesKey] || {};

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-[1280px] mx-auto" data-testid="settings-page">
      {/* Header */}
      <div className="flex items-end justify-between gap-4 flex-wrap mb-6">
        <div>
          <div className="overline">Einstellungen</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">
            Profil & Vorgaben
          </h1>
        </div>
        <button onClick={save} data-testid="save-settings-btn" disabled={speichert}
                className={`apple-btn ${savedFlash ? "apple-btn-secondary" : "apple-btn-primary"} disabled:opacity-60`}>
          {savedFlash ? <><Check size={14} /> Gespeichert</> : <><Save size={14} /> {speichert ? "Speichert…" : "Speichern"}</>}
        </button>
      </div>

      {eigene.length > 0 && (
        <div className="mb-5 rounded-xl border px-4 py-3 text-sm flex flex-wrap items-center gap-3"
             data-testid="eigene-einstellungen"
             style={{ borderColor: "rgba(10,132,255,0.35)", background: "rgba(10,132,255,0.08)",
                      color: "var(--text-primary)" }}>
          <span className="flex-1 min-w-0">
            Eigene Werte (gelten nur für dich): {eigene.map((k) => FELD_TITEL[k] || k).join(", ")}.
            {" "}Ändert dein Chef diese Felder, kommt das bei dir nicht an.
          </span>
          <button type="button" onClick={aufChefZuruecksetzen} data-testid="eigene-zuruecksetzen"
                  className="apple-btn apple-btn-secondary">
            Auf Chef-Vorgaben zurücksetzen
          </button>
        </div>
      )}

      <div className="grid lg:grid-cols-[220px_1fr] gap-5">
        {/* Sidebar nav */}
        <nav className="apple-surface p-2 self-start lg:sticky lg:top-6">
          {sections.map((s) => {
            const Icon = s.icon;
            const isActive = active === s.id;
            return (
              <button key={s.id} onClick={() => setActive(s.id)}
                      data-testid={`settings-tab-${s.id}`}
                      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all mb-1 ${
                        isActive
                          ? "bg-white/[0.08] text-white shadow-[inset_0_0_0_1px_var(--wa-06)]"
                          : "text-zinc-400 hover:text-white hover:bg-white/[0.04]"
                      }`}>
                <Icon size={15} className={isActive ? "text-[var(--accent-red)]" : ""} />
                <span className="font-medium">{s.label}</span>
              </button>
            );
          })}
        </nav>

        {/* Content */}
        <div className="space-y-5">
          {active === "profile" && (
            <Section title="Händlerprofil" subtitle="Diese Angaben erscheinen auf jedem Kaufvertrag, in den Versand-Vorlagen und – für Zwischenhändler – auf dem Marktplatz.">
              {/* Firmenlogo */}
              <div className="flex items-center gap-4 mb-4">
                <div className="w-20 h-20 rounded-2xl overflow-hidden flex items-center justify-center shrink-0"
                     style={{ background: "var(--wa-05)", border: "1px solid var(--divider)" }}>
                  {form.profile.logo_url
                    ? <img src={form.profile.logo_url.startsWith("http") ? form.profile.logo_url : `${process.env.REACT_APP_BACKEND_URL}${form.profile.logo_url}`}
                           alt="Logo" className="w-full h-full object-contain" />
                    : <Building2 size={26} className="text-zinc-600" />}
                </div>
                {user?.role === "sucher" ? (
                  // Entscheidung Ahmad 16.09.2026: das Firmenlogo pflegt nur der
                  // Chef — Sucher sehen es, aendern es aber nicht (Server: 403).
                  <div className="text-[12px] text-zinc-500" data-testid="logo-nur-chef">
                    {/* Rollenprüfung 22.09.2026 (RP-452): genau sagen, wo das Logo
                        erscheint — bereits erstellte Verträge bekommen es nie nachträglich. */}
                    Das Firmenlogo pflegt der Chef. Es erscheint auf neuen Kaufverträgen und in Vertrags-Mails.
                  </div>
                ) : (
                <div>
                  <label className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white cursor-pointer"
                         style={{ background: "var(--accent-red)" }} data-testid="set-logo">
                    Logo hochladen
                    {/* U-133/M50: nach einem Fehlschlag laesst sich dieselbe Datei
                        wieder waehlen (sonst feuert onChange nicht erneut). */}
                    <input type="file" accept="image/*" className="hidden"
                           onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; uploadLogo(f); }} />
                  </label>
                  {/* RP-452: das Logo wird beim Anlegen im Vertrag festgehalten
                      (contract_data.logo_key) — ältere Verträge bleiben ohne. */}
                  <div className="text-[11px] text-zinc-500 mt-1.5" data-testid="logo-hinweis">
                    PNG/JPG, max. 2 MB. Erscheint auf neuen Kaufverträgen, in Vertrags-Mails und auf dem
                    Marktplatz. Bereits erstellte Verträge bleiben unverändert.
                  </div>
                  {form.profile.logo_url && (
                    <button type="button" onClick={() => setProfile("logo_url", "")}
                            className="text-[11px] text-zinc-500 hover:text-red-400 mt-1">Logo entfernen</button>
                  )}
                </div>
                )}
              </div>

              {/* Entscheidung Ahmad 22.09.2026: eigene Kundennummer fuer Vertraege —
                  im Kaufvertrag, in den Vorlagen ({kundennummer}) und im Abholauftrag.
                  Nicht die Anmeldenummer. Vergibt der Server, nicht aenderbar. */}
              {dealer?.vertrags_kundennummer && (
                <div className="rounded-xl border p-3 text-[12.5px] leading-relaxed mb-1"
                     data-testid="vertrags-kundennummer"
                     style={{ borderColor: "var(--border-default)", background: "var(--wa-03)" }}>
                  <span className="font-semibold">Kundennummer für Verträge: </span>
                  <span className="font-mono text-[14px]">{dealer.vertrags_kundennummer}</span>
                  <div className="text-[11px] text-zinc-500 mt-1">
                    Steht im Kaufvertrag („nur unter Vorlage der Kundennummer“), in den Versand-Vorlagen
                    als {"{kundennummer}"} und im Abholauftrag des Fahrers. Das ist nicht eure Anmeldenummer.
                  </div>
                </div>
              )}
              <div className="grid md:grid-cols-2 gap-3">
                <AppleField label="Firmenname" value={form.profile.company_name} onChange={(v) => setProfile("company_name", v)} testid="set-company" />
                <AppleField label="Ansprechpartner" value={form.profile.contact_person} onChange={(v) => setProfile("contact_person", v)} testid="set-contact" />
                <AppleField label="Telefon" value={form.profile.phone} onChange={(v) => setProfile("phone", v)} testid="set-phone" />
                <AppleField label="WhatsApp-Nummer" value={form.profile.whatsapp_number} onChange={(v) => setProfile("whatsapp_number", v)} testid="set-wa" />
                <AppleField label="E-Mail" value={form.profile.email} onChange={(v) => setProfile("email", v)} testid="set-email" />
                <AppleField label="Adresse" value={form.profile.address} onChange={(v) => setProfile("address", v)} testid="set-address" />
                <AppleField label="PLZ" value={form.profile.zip_code} onChange={(v) => setProfile("zip_code", v)} testid="set-zip" />
                <AppleField label="Ort" value={form.profile.city} onChange={(v) => setProfile("city", v)} testid="set-city" />
              </div>

              {/* Öffnungszeiten */}
              <div className="mt-3">
                <label className="block text-[13px] font-medium text-zinc-300 mb-1">Öffnungszeiten</label>
                <textarea value={form.profile.opening_hours} onChange={(e) => setProfile("opening_hours", e.target.value)}
                          rows={3} data-testid="set-hours"
                          placeholder={"Mo–Fr: 08:00–18:00\nSa: 09:00–13:00\nSo: geschlossen"}
                          className="w-full rounded-xl border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40"
                          style={{ borderColor: "var(--divider)" }} />
                <div className="text-[11px] text-zinc-500 mt-1">Wird Zwischenhändlern im Marktplatz angezeigt.</div>
              </div>
            </Section>
          )}

          {active === "rules" && (
            <Section title="Vergleichsregeln" subtitle="Wie der mobile.de-Filter aus den Fahrzeugdaten gebaut wird. Du kannst zwei Profile speichern (Inland & Export) und am Vergleich-Header per Klick wechseln.">
              {/* Profile tabs (welches Profil bearbeite ich gerade?)
                  + Live-Schalter (welches ist gerade aktiv?). */}
              <div className="apple-card p-3 mb-4 flex flex-col sm:flex-row sm:items-center gap-3 justify-between"
                   style={{ background: "var(--hover-bg)" }}>
                <div className="flex items-center gap-1 p-1 rounded-xl"
                     style={{ background: "var(--wa-04)", border: "1px solid var(--divider)" }}>
                  {[
                    { v: "inland", l: "🇩🇪 Inland" },
                    { v: "export", l: "🌍 Export" },
                  ].map((p) => (
                    <button key={p.v}
                      type="button"
                      data-testid={`profile-tab-${p.v}`}
                      onClick={() => setEditProfile(p.v)}
                      className={`px-4 py-1.5 rounded-lg text-xs font-bold transition-all ${
                        editProfile === p.v ? "shadow-sm" : "opacity-60 hover:opacity-100"
                      }`}
                      style={editProfile === p.v
                        ? { background: "var(--accent-red)", color: "white" }
                        : { color: "var(--text-secondary)" }}
                    >
                      {p.l}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-2 text-xs">
                  <span style={{ color: "var(--text-muted)" }}>Aktives Profil:</span>
                  <button
                    type="button"
                    data-testid="active-profile-toggle"
                    onClick={() => setActiveProfile(form.active_profile === "inland" ? "export" : "inland")}
                    className="px-3 py-1.5 rounded-md font-bold uppercase tracking-wider text-[10px]"
                    style={{
                      background: form.active_profile === "inland"
                        ? "rgba(52,199,89,0.14)" : "rgba(10,132,255,0.14)",
                      color: form.active_profile === "inland" ? "var(--accent-green)" : "var(--st-blau)",
                      border: `1px solid ${form.active_profile === "inland" ? "rgba(52,199,89,0.35)" : "rgba(10,132,255,0.4)"}`,
                    }}
                    title="Klick wechselt das aktive Profil"
                  >
                    {form.active_profile === "inland" ? "Inland" : "Export"} ↻
                  </button>
                </div>
              </div>

              <div className="text-[11px] mb-3 px-1" style={{ color: "var(--text-muted)" }}>
                Du bearbeitest gerade: <b style={{ color: "var(--text-primary)" }}>
                  {editProfile === "inland" ? "Inland-Profil" : "Export-Profil"}
                </b>. Änderungen oben rechts mit „Speichern" sichern.
              </div>

              <RuleRow label="Erstzulassung">
                <AppleSelect testid="rule-fr-mode" value={r.first_registration?.mode || "older_exact"}
                             onChange={(v) => setRule("first_registration", { ...r.first_registration, mode: v })}
                             options={[
                               { v: "ignore", l: "Nicht übernehmen" },
                               { v: "any", l: "Beliebig (Export-Standard)" },
                               { v: "exact", l: "1:1 (gleiches Jahr)" },
                               { v: "older_exact", l: "X Jahre älter" },
                             ]} />
                {r.first_registration?.mode === "older_exact" && (
                  <>
                    <AppleNumber testid="rule-fr-years" min={1} max={10}
                                 value={r.first_registration?.years ?? 1}
                                 onChange={(v) => setRule("first_registration", { ...r.first_registration, years: v })}
                                 className="w-24" />
                    <span className="text-xs text-zinc-500">Jahr(e) älter</span>
                  </>
                )}
              </RuleRow>

              <RuleRow label="Kilometer">
                <AppleSelect testid="rule-km-mode" value={r.mileage?.mode || "plus"}
                             onChange={(v) => setRule("mileage", { ...r.mileage, mode: v })}
                             options={[
                               { v: "ignore", l: "Nicht übernehmen" },
                               { v: "exact", l: "1:1 (max. KM = aktueller KM)" },
                               { v: "plus", l: "+ X km" },
                               { v: "range", l: "± X km" },
                             ]} />
                {r.mileage?.mode !== "ignore" && r.mileage?.mode !== "exact" && (
                  // Rollenprüfung 22.09.2026 (RP-414): deutsch lesen — "50.000"
                  // wurde im number-Feld zu 50 und die Suche lief mit ±50 km.
                  <KmFeld key={`${rulesKey}-km`} feld={`${rulesKey}.mileage`}
                          testid="rule-km-value" value={r.mileage?.value ?? 30000}
                          onChange={(v) => setRule("mileage", { ...r.mileage, value: v })}
                          onFehler={feldFehlerSetzen}
                          className="w-32" />
                )}
              </RuleRow>

              <RuleRow label="Leistung">
                <AppleSelect testid="rule-pwr-mode" value={r.power?.mode || "tolerance_ps"}
                             onChange={(v) => setRule("power", { ...r.power, mode: v })}
                             options={[
                               { v: "ignore", l: "Nicht übernehmen" },
                               { v: "exact", l: "1:1" },
                               { v: "tolerance_ps", l: "± X PS" },
                               { v: "tolerance_kw", l: "± X kW" },
                               { v: "min_ps", l: "− X PS und aufwärts (nach oben offen)" },
                             ]} />
                {(r.power?.mode === "tolerance_ps" || r.power?.mode === "tolerance_kw"
                  || r.power?.mode === "min_ps") && (
                  <AppleNumber testid="rule-pwr-value" value={r.power?.value ?? 5}
                               onChange={(v) => setRule("power", { ...r.power, value: v })}
                               className="w-24" />
                )}
              </RuleRow>

              <RuleRow label="Kraftstoff">
                <AppleSelect testid="rule-fuel-mode" value={r.fuel?.mode || "exact"}
                             onChange={(v) => setRule("fuel", { mode: v })}
                             options={[{ v: "ignore", l: "Nicht übernehmen" }, { v: "exact", l: "1:1 übernehmen" }]} />
              </RuleRow>

              <RuleRow label="Getriebe">
                <AppleSelect testid="rule-gear-mode" value={r.gearbox?.mode || "exact"}
                             onChange={(v) => setRule("gearbox", { mode: v })}
                             options={[{ v: "ignore", l: "Nicht übernehmen" }, { v: "exact", l: "1:1 übernehmen" }]} />
              </RuleRow>

              {/* Wunsch Ahmad 18.09.2026: Navi im Vergleich mitfiltern —
                  aber nur, wenn das Inserat eines nennt. Wer das nicht will,
                  stellt hier "Nicht filtern" ein. */}
              <RuleRow label="Navigationssystem">
                <AppleSelect testid="rule-navi-mode" value={r.navi?.mode || "wenn_vorhanden"}
                             onChange={(v) => setRule("navi", { mode: v })}
                             options={[
                               { v: "wenn_vorhanden", l: "Mitfiltern, wenn im Inserat vorhanden" },
                               { v: "ignore", l: "Nicht filtern" },
                             ]} />
              </RuleRow>

              <RuleRow label="Schadensfilter">
                <AppleSelect testid="rule-damage-mode" value={r.damage?.mode || "no_accident"}
                             onChange={(v) => setRule("damage", { mode: v })}
                             options={[{ v: "ignore", l: "Alle" }, { v: "no_accident", l: "Nur unfallfrei & fahrbereit" }]} />
              </RuleRow>

              <RuleRow label="Anbieter">
                <AppleSelect testid="rule-seller-mode" value={r.seller?.mode || "all"}
                             onChange={(v) => setRule("seller", { mode: v })}
                             options={[
                               { v: "all", l: "Alle" },
                               { v: "dealer", l: "Nur Händler" },
                               { v: "private", l: "Nur Privat" },
                             ]} />
              </RuleRow>

              {/* Runde 24 (11.09.2026): Kategorie, Navigation und Klimatisierung
                  sind als Filter für beide Portale entfallen (AutoScout24 kann
                  die Kategorie nicht sauber abbilden). Alt-Werte verwirft das
                  Backend beim Speichern still. */}
              <RuleRow label="Land" last>
                <CountryPicker
                  value={r.country || { mode: "exact", codes: ["DE"] }}
                  onChange={(v) => setRule("country", v)}
                />
              </RuleRow>
            </Section>
          )}

          {active === "templates" && (
            <Section title="Kaufvertrag verschicken"
                     subtitle="Diese Texte gehen mit dem Kaufvertrag raus, wenn du ihn per E-Mail oder WhatsApp verschickst.">
              <AppleField label="E-Mail-Betreff" value={form.email_subject}
                          onChange={(v) => setForm({ ...form, email_subject: v })}
                          testid="set-email-subject" />
              <AppleTextarea label="E-Mail-Text" rows={8} value={form.email_template}
                             onChange={(v) => setForm({ ...form, email_template: v })}
                             icon={Mail} testid="set-email-template" />
              {/* Wunsch Ahmad 21.09.2026: Betreff und Text für den erneuten
                  Versand nach einer Korrektur. Der Versand-Dialog nimmt sie
                  von selbst, sobald eine frühere Fassung verschickt wurde —
                  mit dem korrigierten Vertrag als Anhang. */}
              <AppleField label="Betreff – erneuter Versand (nach Korrektur)"
                          value={form.email_subject_korrektur}
                          onChange={(v) => setForm({ ...form, email_subject_korrektur: v })}
                          testid="set-email-subject-korrektur" />
              <AppleTextarea label="E-Mail-Text – erneuter Versand (nach Korrektur)" rows={8}
                             value={form.email_template_korrektur}
                             onChange={(v) => setForm({ ...form, email_template_korrektur: v })}
                             icon={Mail} testid="set-email-template-korrektur"
                             hint="Wird beim Senden automatisch genommen, wenn eine frühere Fassung dieses Vertrags schon verschickt wurde." />
              <AppleTextarea label="WhatsApp-Text" rows={8} value={form.whatsapp_template}
                             onChange={(v) => setForm({ ...form, whatsapp_template: v })}
                             icon={MessageSquare} testid="set-wa-template" />
              <PlaceholderHint placeholders={PLATZHALTER} />
            </Section>
          )}
          {active === "templates" && (
            <Section title="Hinweis nach Kaufabschluss & Bahnverbindung"
                     subtitle="Diese Texte gehen nie automatisch raus. Im Vertragsarchiv (Knopf mit dem Brief-Symbol beim Vertrag) sind Name und Daten schon eingesetzt — dort per E-Mail verschicken oder kopieren. Hier kannst du sie anpassen und kopieren.">
              <div className="text-xs font-semibold text-zinc-300 pt-1">Bahnverbindung</div>
              <AppleField label="Betreff" kopieren
                          value={form.email_subject_bahn}
                          onChange={(v) => setForm({ ...form, email_subject_bahn: v })}
                          testid="set-email-subject-bahn" />
              <AppleTextarea label="E-Mail-Text (Bahnverbindung)" rows={8} kopieren
                             value={form.email_template_bahn}
                             onChange={(v) => setForm({ ...form, email_template_bahn: v })}
                             icon={Mail} testid="set-email-template-bahn" />
              <div className="text-xs font-semibold text-zinc-300 pt-3">Hinweis nach Kaufabschluss</div>
              <AppleField label="Betreff" kopieren
                          value={form.email_subject_nach_kauf}
                          onChange={(v) => setForm({ ...form, email_subject_nach_kauf: v })}
                          testid="set-email-subject-nach-kauf" />
              <AppleTextarea label="E-Mail-Text (Hinweis nach Kaufabschluss)" rows={14} kopieren
                             value={form.email_template_nach_kauf}
                             onChange={(v) => setForm({ ...form, email_template_nach_kauf: v })}
                             icon={Mail} testid="set-email-template-nach-kauf" />
              <AppleTextarea label="WhatsApp-Text (Hinweis nach Kaufabschluss)" rows={14} kopieren
                             value={form.whatsapp_template_nach_kauf}
                             onChange={(v) => setForm({ ...form, whatsapp_template_nach_kauf: v })}
                             icon={MessageSquare} testid="set-wa-template-nach-kauf" />
              <div className="text-[11px] text-zinc-500 leading-snug">
                Hier kopiert, stehen die Platzhalter (z. B. {"{kunde_name}"}) noch im Text — bitte
                beim Einfügen durch den Namen ersetzen. Im Vertragsarchiv (Knopf mit dem
                Brief-Symbol beim Vertrag) sind sie schon ausgefüllt.
              </div>
              <PlaceholderHint placeholders={PLATZHALTER} />
            </Section>
          )}

          {active === "agb" && (
            <Section title="Vertragsbedingungen & Vereinbarungen"
                     subtitle="Diese Texte werden bei jedem neuen Kaufvertrag automatisch übernommen.">
              {form._agb_zusammengefuehrt && (
                <div className="rounded-xl border p-3 text-[12.5px] leading-relaxed"
                     data-testid="agb-zusammengefuehrt"
                     style={{ borderColor: "#f59e0b55", background: "#f59e0b14", color: "var(--tx-amber)" }}>
                  Aus zwei Textfeldern ist eins geworden: Deine bisherigen AGB stehen jetzt
                  unten im Feld „Vertragsbedingungen“
                  {form._agb_standard_genutzt ? " — angehängt an den Standardtext, der bisher galt" : ""}.
                  Bitte einmal durchlesen und speichern.
                </div>
              )}
              <AppleTextarea
                label="Vertragsbedingungen & AGB (stehen in jedem Kaufvertrag)"
                rows={16}
                value={form.digital_vertragstext}
                onChange={(v) => setForm({ ...form, digital_vertragstext: v })}
                icon={Mail}
                testid="set-digital-text"
                hint='Steht in jedem neuen Kaufvertrag als eigener Abschnitt „Allgemeine Vertragsbedingungen" (Druck und digital). Beim Versand per E-Mail/WhatsApp entfallen die Unterschriftsfelder, dort steht nur: „Dieser Vertrag ist ohne Unterschrift gültig." Leer = Standardtext (unten). Bestehende Verträge bleiben unverändert. Absätze mit einer Leerzeile trennen.'
              />
              {!(form.digital_vertragstext || "").trim() && dealer?.digital_vertragstext_standard && (
                <div className="rounded-xl border p-3 text-[12px] leading-relaxed whitespace-pre-line"
                     style={{ borderColor: "var(--border-default)", color: "var(--text-secondary)" }}
                     data-testid="digital-text-standard">
                  <div className="text-[11px] font-semibold uppercase tracking-wider mb-1 text-zinc-400">
                    Aktuell gilt der Standardtext:
                  </div>
                  {dealer.digital_vertragstext_standard}
                </div>
              )}
              {dealer?.digital_vertragstext_standard && (
                <button type="button" data-testid="digital-text-standard-btn"
                        onClick={() => setForm({ ...form, digital_vertragstext: dealer.digital_vertragstext_standard })}
                        className="text-xs font-semibold underline underline-offset-2"
                        style={{ color: "var(--text-secondary)" }}>
                  Standardtext ins Feld übernehmen und anpassen
                </button>
              )}
              {/* Wunsch Ahmad 20.09.2026: unser Standardsatz ist ein
                  Schalter. An = steht in jedem neuen Vertrag, mit
                  automatisch eingesetztem Abholdatum, Übergabeort und
                  Zahlungsart. Aus = nur der eigene Text darunter. */}
              <label className="flex items-start gap-3 p-4 rounded-2xl cursor-pointer"
                     style={{ background: "var(--wa-03)",
                              border: "1px solid var(--border-default)" }}
                     data-testid="set-sonder-standard">
                <input type="checkbox" className="mt-1 w-5 h-5 shrink-0"
                       checked={form.sondervereinbarung_standard_aktiv !== false}
                       onChange={(e) => setForm({
                         ...form,
                         sondervereinbarung_standard_aktiv: e.target.checked,
                       })} />
                <span>
                  <span className="block font-semibold"
                        style={{ color: "var(--text-primary)" }}>
                    Unseren Standardsatz mitdrucken
                  </span>
                  <span className="block text-xs mt-1"
                        style={{ color: "var(--text-secondary)" }}>
                    Übergabe bis/am … in … gegen … und die Kundennummer-Regel.
                    Abholdatum, Anschrift des Verkäufers und Zahlungsart werden
                    beim Erstellen des Vertrags von selbst eingesetzt.
                  </span>
                  <span className="block text-xs mt-2 whitespace-pre-line"
                        style={{ color: "var(--text-secondary)", opacity: .8 }}>
                    {STANDARD_SONDERSATZ}
                  </span>
                </span>
              </label>
              <AppleTextarea
                label="Eigene Besondere Vereinbarungen"
                rows={6}
                value={form.default_special_agreements}
                onChange={(v) => setForm({ ...form, default_special_agreements: v })}
                icon={FileText}
                testid="set-default-agreements"
                hint="Stehen in jedem neuen Vertrag — unter dem Standardsatz, falls der eingeschaltet ist. Platzhalter sind auch hier erlaubt."
              />
              <PlaceholderHint placeholders={PLATZHALTER} />
            </Section>
          )}

          {active === "markt" && <MarketplacePanel />}
          {active === "abo" && <SubscriptionPanel />}
        </div>
      </div>
    </div>
  );
}

/* ── Helpers ── */

function Section({ title, subtitle, children }) {
  return (
    <div className="apple-surface p-6">
      <div className="mb-5">
        <h2 className="font-display font-bold text-xl tracking-tight">{title}</h2>
        {subtitle && <p className="text-sm text-zinc-500 mt-1">{subtitle}</p>}
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function RuleRow({ label, children, last }) {
  return (
    <div className={`flex flex-wrap items-center gap-3 py-3 ${last ? "" : "border-b border-white/[0.05]"}`}>
      <div className="w-36 text-sm font-medium text-zinc-300">{label}</div>
      <div className="flex flex-wrap items-center gap-2 flex-1">{children}</div>
    </div>
  );
}

function AppleField({ label, value, onChange, testid, kopieren = false }) {
  // U-135/M50: dieselbe Grenze wie der Server (Profilfelder max. 500 Zeichen)
  return (
    <div className="space-y-1.5">
      {/* Knopf NEBEN dem Label, nicht darin — ein Label ohne htmlFor
          beschriftet sein erstes Bedienelement, ein Klick auf den Titel
          hätte sonst kopiert. */}
      <div className="flex items-center gap-1.5">
        <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">{label}</label>
        {kopieren && <KopierKnopf text={value} className="ml-auto"
                                   testid={testid ? `${testid}-kopieren` : undefined} />}
      </div>
      <input data-testid={testid} value={value || ""} maxLength={500}
             onChange={(e) => onChange(e.target.value)}
             className="apple-input" />
    </div>
  );
}

function AppleSelect({ value, onChange, options, testid }) {
  return (
    <select data-testid={testid} value={value}
            onChange={(e) => onChange(e.target.value)}
            className="apple-input !w-auto !min-w-[180px] cursor-pointer">
      {options.map((o) => <option key={o.v} value={o.v}>{o.l}</option>)}
    </select>
  );
}

function AppleNumber({ value, onChange, min, max, className = "", testid }) {
  // 16.09.2026: ein geleertes Feld ergab Number("") = 0 — gespeichert wurde
  // "0 PS Toleranz", angezeigt blieb der Standard (0 || 5). Leer oder
  // unlesbar heisst jetzt "kein Wert" (undefined): das Feld zeigt sofort den
  // Standard, und genau der wird gespeichert und angewendet.
  return (
    <input data-testid={testid} type="number" min={min} max={max} value={value ?? ""}
           onChange={(e) => {
             const s = e.target.value;
             const n = Number(s);
             onChange(s === "" || Number.isNaN(n) ? undefined : n);
           }}
           className={`apple-input ${className}`} />
  );
}

/** 30000 -> "30.000"; kein Wert -> "". */
export function kmAnzeige(n) {
  return typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("de-DE") : "";
}

/**
 * Rollenprüfung 22.09.2026 (RP-414): Kilometer-Toleranz als Textfeld mit
 * deutscher Zahlenlogik (kmAusText). Das frühere type="number"-Feld machte
 * aus "50.000" im deutschen Chrome 50 — gespeichert wurde ±50 km, der
 * Vergleich fand kaum noch etwas. Unlesbares wird angezeigt und blockiert
 * das Speichern (onFehler), statt still einen falschen Wert zu schreiben.
 * Leer = Standard (wie bisher), übernommen beim Verlassen des Feldes.
 */
export function KmFeld({ feld, value, onChange, onFehler, testid, className = "" }) {
  const [text, setText] = useState(() => kmAnzeige(value));
  const [fehler, setFehler] = useState("");
  const textRef = useRef(text);
  // Rollenprüfung 22.09.2026 (Review): der Wert, der VOR der laufenden
  // Eingabe galt (beim Einhängen, nach einer Änderung von außen oder nach dem
  // Verlassen mit lesbarem Text). Beim Tippen gehen Zwischenstände ins
  // Formular ("5", "50" ...); wird der Text unlesbar ("50.00"), bekommt das
  // Formular diesen Wert zurück. Vorher blieb der Zwischenstand 50 stehen —
  // und nach einem Profilwechsel (Aushängen räumt den Fehler) speicherte
  // "Speichern" still ±50 km.
  const vorherRef = useRef(value);

  const fehlerSetzen = useCallback((f) => {
    setFehler(f);
    onFehler?.(feld, f);
  }, [feld, onFehler]);

  // Wert von außen geändert (Neuladen, anderes Profil): Anzeige nachziehen —
  // aber nicht, solange der Text genau diesen Wert schon meint, und nicht,
  // wenn wir selbst bei unlesbarem Text den Vorwert zurückgegeben haben
  // (sonst verschwände die Eingabe samt Fehlermeldung unter dem Finger).
  useEffect(() => {
    const n = kmAusText(textRef.current);
    if (n === value || (n === null && value == null)) return;
    if (Number.isNaN(n) && value === vorherRef.current) return;
    const neu = kmAnzeige(value);
    textRef.current = neu;
    setText(neu);
    vorherRef.current = value;
    fehlerSetzen("");
  }, [value, fehlerSetzen]);
  // Beim Aushängen (Profilwechsel, Modus "Nicht übernehmen"/"1:1") keinen
  // alten Fehler stehen lassen — das Formular hält dann ja den Vorwert, nie
  // einen halb getippten Zwischenstand (siehe aendern).
  useEffect(() => () => onFehler?.(feld, ""), [feld, onFehler]);

  const aendern = (s) => {
    textRef.current = s;
    setText(s);
    const n = kmAusText(s);
    if (n === null) { fehlerSetzen(""); return; }            // leer: erst beim Verlassen
    if (Number.isNaN(n)) {
      fehlerSetzen("Kilometer bitte als ganze Zahl eingeben, z. B. 50.000");
      // Zwischenstand aus dem Formular nehmen (siehe vorherRef).
      if (value !== vorherRef.current) onChange(vorherRef.current);
      return;
    }
    fehlerSetzen("");
    onChange(n);
  };
  const verlassen = () => {
    const n = kmAusText(textRef.current);
    if (n === null) { onChange(undefined); return; }         // leer = Standard
    if (!Number.isNaN(n)) {
      const schoen = kmAnzeige(n);
      textRef.current = schoen;
      setText(schoen);
      vorherRef.current = n;
    }
  };

  return (
    <span className="inline-flex flex-col gap-1">
      <span className="inline-flex items-center gap-1.5">
        <input data-testid={testid} type="text" inputMode="numeric" autoComplete="off"
               value={text} onChange={(e) => aendern(e.target.value)} onBlur={verlassen}
               aria-invalid={fehler ? "true" : undefined}
               className={`apple-input ${className}`}
               style={fehler ? { borderColor: "var(--st-rot)" } : undefined} />
        <span className="text-xs text-zinc-500">km</span>
      </span>
      {fehler && (
        <span role="alert" className="text-[11px]" style={{ color: "var(--tx-rot)" }}
              data-testid={testid ? `${testid}-fehler` : undefined}>{fehler}</span>
      )}
      {/* Altwerte aus dem number-Feld ("50.000" -> 50) sichtbar machen. */}
      {!fehler && typeof value === "number" && value > 0 && value < 1000 && (
        <span className="text-[11px]" style={{ color: "var(--tx-amber)" }}
              data-testid={testid ? `${testid}-hinweis` : undefined}>
          Nur {kmAnzeige(value)} km — gemeint {kmAnzeige(value * 1000)} km?
        </span>
      )}
    </span>
  );
}

function AppleTextarea({ label, rows = 4, value, onChange, hint, testid, icon: Icon, kopieren = false }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
          {Icon && <Icon size={12} />} {label}
        </label>
        {kopieren && <KopierKnopf text={value} className="ml-auto"
                                   testid={testid ? `${testid}-kopieren` : undefined} />}
      </div>
      <textarea data-testid={testid} rows={rows} value={value || ""}
                onChange={(e) => onChange(e.target.value)}
                className="apple-input resize-y leading-relaxed" />
      {hint && <div className="text-[11px] text-zinc-500 leading-snug">{hint}</div>}
    </div>
  );
}

// Rollenprüfung 22.09.2026 (RP-484): {abholdatum} steht im Kaufvertrag nur als
// Datum (backend/vertrag_platzhalter.py, mit_uhrzeit=False), in Mails mit Uhrzeit.
const PLATZHALTER_HINWEIS = {
  "{abholdatum}": "im Kaufvertrag nur das Datum, in Mails mit Uhrzeit",
};

function PlaceholderHint({ placeholders }) {
  const hinweise = placeholders.filter((p) => PLATZHALTER_HINWEIS[p]);
  return (
    <div className="text-[11px] text-zinc-500 mt-1">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-semibold text-zinc-400">Platzhalter:</span>
        {placeholders.map((p) => (
          <code key={p} title={PLATZHALTER_HINWEIS[p] || undefined}
                className="px-1.5 py-0.5 rounded-md bg-white/[0.05] text-zinc-300 border border-white/[0.06]">
            {p}
          </code>
        ))}
      </div>
      {hinweise.map((p) => (
        <div key={p} className="mt-1" data-testid="platzhalter-hinweis">
          <code>{p}</code>: {PLATZHALTER_HINWEIS[p]}
        </div>
      ))}
    </div>
  );
}

/* ── Abo-Panel ── */

// Rollenprüfung 22.09.2026 (RP-010/RP-109/RP-260): Planname und
// Kontext-Abgleich stehen in lib/abo.js (auch für die Team-Seite).
export { PLAN_LABEL, planText, aboKontextVeraltet } from "@/lib/abo";

const STATUS_BADGE = {
  active:    { label: "Aktiv",     bg: "rgba(52,199,89,0.15)",  fg: "var(--accent-green)", border: "rgba(52,199,89,0.35)" },
  cancelled: { label: "Gekündigt", bg: "rgba(255,159,10,0.18)", fg: "var(--st-amber)",             border: "rgba(255,159,10,0.4)" },
  expired:   { label: "Abgelaufen",bg: "rgba(255,69,58,0.18)",  fg: "var(--st-rot)",             border: "rgba(255,69,58,0.4)" },
  none:      { label: "Kein Abo",  bg: "var(--wa-08)",fg: "var(--text-dim)",             border: "var(--wa-10)" },
};

function fmtGermanDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("de-DE", {
      day: "2-digit", month: "long", year: "numeric",
    });
  } catch { return iso; }
}

/**
 * Rollenprüfung 22.09.2026 (RP-040/RP-139): Ladefehler sichtbar mit
 * "Erneut versuchen" — vorher stand der Marktplatz-Reiter für immer auf
 * "Lädt…", und Einladungen/Mitglieder zeigten bei einem Fehler "Noch keine".
 */
function LadeFehler({ text, onErneut, testid }) {
  return (
    <div className="flex flex-wrap items-center gap-3 text-sm rounded-xl border px-3 py-2.5" role="alert"
         data-testid={testid}
         style={{ borderColor: "rgba(255,69,58,0.35)", background: "rgba(255,69,58,0.08)",
                  color: "var(--text-primary)" }}>
      <span className="flex-1 min-w-0">{text}</span>
      <button type="button" onClick={onErneut}
              className="rounded-lg px-3 py-1.5 text-xs border font-semibold"
              style={{ borderColor: "var(--border-default)" }}>
        Erneut versuchen
      </button>
    </div>
  );
}

function MarketplacePanel() {
  const [mp, setMp] = useState(null);
  const [busy, setBusy] = useState(false);
  const [ladeFehler, setLadeFehler] = useState("");

  const load = async () => {
    setLadeFehler("");
    try { const { data } = await api.get("/dealer/marketplace-profile"); setMp(data); }
    catch (e) { setLadeFehler(errMsg(e, "Marktplatz-Profil konnte nicht geladen werden")); }
  };
  useEffect(() => { load(); }, []);

  const setPublic = async (val) => {
    setBusy(true);
    try {
      const { data } = await api.put("/dealer/marketplace-profile", { public: val });
      setMp((s) => ({ ...s, public: data.public }));
      toast.success(val ? "Marktplatz-Profil ist jetzt öffentlich" : "Marktplatz-Profil ist privat");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setBusy(false); }
  };

  const saveDesc = async () => {
    try { await api.put("/dealer/marketplace-profile", { description: mp.description || "" }); toast.success("Beschreibung gespeichert"); }
    catch (e) { toast.error(errMsg(e)); }
  };

  if (!mp) {
    return (
      <Section title="Marktplatz">
        {ladeFehler
          ? <LadeFehler text={ladeFehler} onErneut={load} testid="markt-profil-ladefehler" />
          : <div className="text-sm text-zinc-500">Lädt…</div>}
      </Section>
    );
  }

  return (
    <Section title="Marktplatz" subtitle="Steuert, ob deine veröffentlichten Fahrzeuge für registrierte Zwischenhändler sichtbar sind.">
      {/* Öffentlich-Schalter */}
      <div className="flex items-center justify-between rounded-xl border p-4" style={{ borderColor: "var(--divider)" }}>
        <div className="flex items-start gap-3">
          <Globe size={18} className={mp.public ? "text-emerald-400 mt-0.5" : "text-zinc-500 mt-0.5"} />
          <div>
            <div className="text-sm font-semibold">Profil öffentlich sichtbar</div>
            <div className="text-xs text-zinc-500 mt-0.5">
              {mp.public
                ? "Aktiv — veröffentlichte Fahrzeuge erscheinen im B2B-Marktplatz."
                : "Aus — nur eingeladene Netzwerk-Partner sehen deine Fahrzeuge."}
            </div>
          </div>
        </div>
        <button onClick={() => setPublic(!mp.public)} disabled={busy}
                className={`relative w-12 h-7 rounded-full transition ${mp.public ? "bg-emerald-500" : "bg-white/15"}`}
                title="Umschalten" data-testid="markt-public-toggle">
          <span className={`absolute top-1 w-5 h-5 rounded-full bg-white transition-all ${mp.public ? "left-6" : "left-1"}`} />
        </button>
      </div>

      {/* Kennzahlen */}
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border p-4" style={{ borderColor: "var(--divider)" }}>
          <div className="text-2xl font-black tabular-nums">{mp.published_count ?? 0}</div>
          <div className="text-xs text-zinc-500">veröffentlichte Fahrzeuge</div>
        </div>
        <div className="rounded-xl border p-4" style={{ borderColor: "var(--divider)" }}>
          <div className="text-2xl font-black tabular-nums">{mp.network_members ?? 0}</div>
          <div className="text-xs text-zinc-500">Netzwerk-Partner</div>
        </div>
      </div>

      {/* Kurzbeschreibung */}
      <div>
        <label className="block text-[13px] font-medium text-zinc-300 mb-1">Kurzbeschreibung (für deine Marktplatz-Seite)</label>
        <textarea value={mp.description || ""} onChange={(e) => setMp((s) => ({ ...s, description: e.target.value }))}
                  rows={3} placeholder="z.B. Gepflegte Gebrauchtwagen aus Hannover, faire B2B-Preise."
                  className="w-full rounded-xl border bg-transparent px-3 py-2 text-sm outline-none focus:border-white/40"
                  style={{ borderColor: "var(--divider)" }} />
        <button onClick={saveDesc} className="mt-2 inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold text-white"
                style={{ background: "var(--accent-red)" }}>
          <Save size={14} /> Beschreibung speichern
        </button>
      </div>

      {/* Einladungslinks (privates Netzwerk) */}
      <InvitePanel />

      <div className="text-[11px] text-zinc-600">
        Fahrzeuge veröffentlichst du im jeweiligen Inserat (Bestand → Inserat → „Öffentlich
        veröffentlichen" oder „Nur Netzwerk (privat)"). Das Verkaufen ist kostenlos und unbegrenzt.
      </div>
    </Section>
  );
}

function InvitePanel() {
  const [invites, setInvites] = useState(null);
  const [validity, setValidity] = useState(168);
  const [uses, setUses] = useState(1);
  const [ladeFehler, setLadeFehler] = useState("");

  const load = async () => {
    setLadeFehler("");
    try { const { data } = await api.get("/dealer/invites"); setInvites(Array.isArray(data) ? data : []); }
    catch (e) { setInvites(null); setLadeFehler(errMsg(e, "Einladungen konnten nicht geladen werden")); }
  };
  useEffect(() => { load(); }, []);

  const create = async () => {
    try {
      const { data } = await api.post("/dealer/invites", {
        validity_hours: Number(validity), max_uses: Number(uses),
      });
      const link = `${window.location.origin}${data.link}`;
      try { await navigator.clipboard.writeText(link); toast.success("Einladungslink erstellt & kopiert"); }
      catch { toast.success("Einladungslink erstellt"); }
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const copy = async (inv) => {
    // Kontonummer (13.09.2026): gleicher Link wie in der Antwort beim Erstellen —
    // Zwischenhaendler melden sich an, die Einladung wird danach eingeloest.
    const link = `${window.location.origin}/markt/login?invite=${inv.token}`;
    try { await navigator.clipboard.writeText(link); toast.success("Link kopiert"); }
    catch { window.prompt("Link kopieren:", link); }
  };

  const remove = async (inv) => {
    try { await api.delete(`/dealer/invites/${inv.id}`); toast.success("Einladung gelöscht"); load(); }
    catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div className="rounded-xl border p-4" style={{ borderColor: "var(--divider)" }}>
      <div className="text-sm font-semibold mb-1">Einladungslinks (privates Netzwerk)</div>
      <div className="text-xs text-zinc-500 mb-3">
        Eingeladene Zwischenhändler treten deinem Netzwerk bei und sehen auch deine
        <b> privaten</b> Inserate und Netzwerkpreise.
      </div>
      <div className="flex flex-wrap items-end gap-2 mb-3">
        <div>
          <label className="block text-[10px] text-zinc-500 mb-1 uppercase">Gültig</label>
          <select value={validity} onChange={(e) => setValidity(e.target.value)}
                  className="h-9 px-2 rounded-lg border bg-[var(--bg-surface)] text-sm" style={{ borderColor: "var(--divider)" }}>
            <option value={24}>24 Stunden</option>
            <option value={168}>7 Tage</option>
            <option value={720}>30 Tage</option>
          </select>
        </div>
        <div>
          <label className="block text-[10px] text-zinc-500 mb-1 uppercase">Nutzungen</label>
          <select value={uses} onChange={(e) => setUses(e.target.value)}
                  className="h-9 px-2 rounded-lg border bg-[var(--bg-surface)] text-sm" style={{ borderColor: "var(--divider)" }}>
            <option value={1}>1×</option>
            <option value={5}>5×</option>
            <option value={10}>10×</option>
          </select>
        </div>
        <button onClick={create} data-testid="invite-create"
                className="h-9 px-4 rounded-lg text-sm font-semibold text-white"
                style={{ background: "var(--accent-red)" }}>
          + Einladungslink erstellen
        </button>
      </div>
      {ladeFehler ? (
        <LadeFehler text={ladeFehler} onErneut={load} testid="einladungen-ladefehler" />
      ) : invites === null ? (
        <div className="text-xs text-zinc-500">Lädt…</div>
      ) : invites.length === 0 ? (
        <div className="text-xs text-zinc-500">Noch keine Einladungen erstellt.</div>
      ) : (
        <div className="space-y-1.5">
          {invites.map((inv) => (
            <div key={inv.id} className="flex flex-wrap items-center gap-2 text-xs rounded-lg border px-3 py-2"
                 style={{ borderColor: "var(--divider)" }}>
              <span className={inv.valid ? "text-emerald-400" : "text-zinc-500"}>
                {inv.valid ? "● aktiv" : "○ abgelaufen/verbraucht"}
              </span>
              <span className="text-zinc-400">{inv.used_count}/{inv.max_uses} genutzt</span>
              <span className="text-zinc-600">bis {new Date(inv.expires_at).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>
              <span className="ml-auto flex gap-2">
                {inv.valid && (
                  <button onClick={() => copy(inv)} className="text-zinc-300 hover:text-white underline">Link kopieren</button>
                )}
                <button onClick={() => remove(inv)} className="text-zinc-500 hover:text-red-400">löschen</button>
              </span>
            </div>
          ))}
        </div>
      )}
      <NetzwerkMitglieder />
    </div>
  );
}

// Mitglieder des privaten Netzwerks: sichtbar und widerrufbar. Ein
// Widerruf nimmt dem Zwischenhaendler sofort den Zugang zu privaten
// Inseraten und Netzwerkpreisen (Backend: DELETE /dealer/network/members).
function NetzwerkMitglieder() {
  const [members, setMembers] = useState(null);
  const [ladeFehler, setLadeFehler] = useState("");

  const load = async () => {
    setLadeFehler("");
    try { const { data } = await api.get("/dealer/network/members"); setMembers(Array.isArray(data) ? data : []); }
    catch (e) { setMembers(null); setLadeFehler(errMsg(e, "Netzwerk-Mitglieder konnten nicht geladen werden")); }
  };
  useEffect(() => { load(); }, []);

  const revoke = async (m) => {
    // Kontonummer (13.09.2026): Kaeufer haben nicht immer eine E-Mail — Rueckfall
    // auf Firma/Ansprechpartner, NIE auf die Kontonummer (halbe Zugangsdaten).
    if (!window.confirm(`${m.company_name || m.contact_name || "Zwischenhändler"} aus dem Netzwerk entfernen? Er sieht deine privaten Inserate danach nicht mehr.`)) return;
    try { await api.delete(`/dealer/network/members/${m.buyer_user_id}`); toast.success("Zugang widerrufen"); load(); }
    catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--divider)" }} data-testid="network-members">
      <div className="text-sm font-semibold mb-1">Netzwerk-Mitglieder ({members?.length ?? "…"})</div>
      <div className="text-xs text-zinc-500 mb-2">
        Zwischenhändler, die über eine Einladung beigetreten sind. Zugang jederzeit widerrufbar.
      </div>
      {ladeFehler ? (
        <LadeFehler text={ladeFehler} onErneut={load} testid="netzwerk-ladefehler" />
      ) : members === null ? (
        <div className="text-xs text-zinc-500">Lädt…</div>
      ) : members.length === 0 ? (
        <div className="text-xs text-zinc-500">Noch keine Mitglieder.</div>
      ) : (
        <div className="space-y-1.5">
          {members.map((m) => (
            <div key={m.buyer_user_id} className="flex flex-wrap items-center gap-2 text-xs rounded-lg border px-3 py-2"
                 style={{ borderColor: "var(--divider)" }}>
              <span className="font-semibold">{m.company_name || m.contact_name || "Zwischenhändler"}</span>
              {m.company_name && m.contact_name && <span className="text-zinc-400">{m.contact_name}</span>}
              {m.email && <span className="text-zinc-500">{m.email}</span>}
              {m.joined_at && (
                <span className="text-zinc-600">seit {new Date(m.joined_at).toLocaleDateString("de-DE")}</span>
              )}
              <button onClick={() => revoke(m)} className="ml-auto text-zinc-500 hover:text-red-400"
                      data-testid={`revoke-${m.buyer_user_id}`}>Zugang widerrufen</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function SubscriptionPanel() {
  const { refresh, subscription } = useAuth();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState("");
  // Letzter Kontext-Stand, ohne den Abruf-Takt neu aufzusetzen.
  const kontextRef = useRef(subscription);
  kontextRef.current = subscription;

  const uebernehmen = useCallback((d) => {
    setData(d);
    if (aboKontextVeraltet(kontextRef.current, d)) refresh();
  }, [refresh]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/dealer/subscription");
      uebernehmen(data);
    } catch (err) {
      toast.error(errMsg(err, "Abo-Info konnte nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  }, [uebernehmen]);

  useEffect(() => { load(); }, [load]);
  // Audit 09/2026: nach Freischaltung durch den Betreiber aktualisiert sich
  // die Seite selbst (alle 30 s, solange kein aktives Abo) — plus Button.
  // Rollenprüfung 22.09.2026 (RP-105): dabei auch den Anmelde-Kontext, damit
  // Vergleich und Suche sofort freigegeben sind ("geht es sofort weiter").
  useEffect(() => {
    if (!data || data.active) return undefined;
    const t = setInterval(() => { api.get("/dealer/subscription").then((r) => uebernehmen(r.data)).catch(() => {}); }, 30000);
    return () => clearInterval(t);
  }, [data, uebernehmen]);

  const cancel = async () => {
    setBusy("cancel");
    try {
      const { data } = await api.post("/dealer/subscription/cancel");
      toast.success(data?.message || "Abo gekündigt");
      setConfirming(false);
      await load();
      refresh();              // RP-105: Abo-Punkt in der Leiste nachziehen
    } catch (err) {
      toast.error(errMsg(err, "Kündigung fehlgeschlagen"));
    } finally {
      setBusy("");
    }
  };

  // 09/2026: Verlängerung läuft per Rechnung über den Betreiber — der Klick
  // sendet nur noch eine Anfrage (kein Stripe für Firmen/Sucher).
  const renew = async (plan) => {
    setBusy(plan);
    try {
      const { data: r } = await api.post("/dealer/abo-anfrage-selbst", { plan });
      toast.success(r?.bereits_offen
        ? "Deine Anfrage liegt bereits beim Betreiber — wir melden uns."
        : "Anfrage an den Betreiber gesendet — nach Freigabe wird das Abo verlängert");
      await load();
    } catch (err) {
      toast.error(errMsg(err, "Anfrage fehlgeschlagen"));
    } finally {
      setBusy("");
    }
  };

  if (loading) {
    return (
      <Section title="Abo & Zahlung" subtitle="Lade Abo-Daten…">
        <div className="text-zinc-500 text-sm py-4">Bitte warten…</div>
      </Section>
    );
  }
  if (!data) return null;

  const badge = STATUS_BADGE[data.status] || STATUS_BADGE.none;
  const planLabel = planText(data.plan);
  const isCancelled = data.status === "cancelled";
  const isExpired = data.status === "expired" || data.status === "none";

  // Rollenprüfung 22.09.2026 (RP-010/RP-109): Verträge und Versand verlangen
  // das Abo (contracts.py require_active_sub) — der alte Untertitel nannte
  // sie "kostenlos". Gleicher Wortlaut wie auf der Abo-Seite.
  return (
    <Section title="Abo & Zahlung"
             subtitle="Das Sucher-Abo schaltet Suche, Vergleich und Kaufverträge (samt Versand) frei. Terminplaner, Freigaben, Bestand und Inserate bleiben kostenlos.">
      <div className="flex justify-end -mt-2 mb-2">
        <button onClick={load} data-testid="abo-status-aktualisieren"
                className="text-xs text-zinc-400 hover:text-white underline underline-offset-2">
          Status aktualisieren
        </button>
      </div>
      {/* Aktueller Status */}
      <div className="apple-card p-5 mb-4" data-testid="abo-status-card"
           style={{ background: "var(--hover-bg)", border: "1px solid var(--divider)" }}>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="overline mb-1">Aktueller Plan</div>
            <div className="font-display font-black text-2xl tracking-tight" data-testid="abo-plan-label">
              {planLabel}
            </div>
          </div>
          <span
            className="px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider"
            style={{ background: badge.bg, color: badge.fg, border: `1px solid ${badge.border}` }}
            data-testid="abo-status-badge"
          >
            {badge.label}
          </span>
        </div>

        <div className="grid sm:grid-cols-2 gap-4 mt-5">
          <InfoRow icon={<Calendar size={14} />} label={data.is_lifetime ? "Gültig" : "Läuft bis"}>
            <span className="font-mono text-sm font-semibold text-white" data-testid="abo-expires-at">
              {data.is_lifetime ? "unbegrenzt" : fmtGermanDate(data.expires_at)}
            </span>
          </InfoRow>
          {!data.is_lifetime && (
            <InfoRow icon={<Bolt size={14} />} label="Verbleibend">
              <span className="font-mono text-sm font-semibold text-white" data-testid="abo-days-remaining">
                {data.days_remaining != null ? `${data.days_remaining} Tage` : "—"}
              </span>
            </InfoRow>
          )}
        </div>

        {isCancelled && (
          <div className="mt-4 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
               style={{ background: "rgba(255,159,10,0.10)", border: "1px solid rgba(255,159,10,0.25)", color: "var(--tx-amber)" }}
               data-testid="abo-cancelled-info">
            <X size={14} className="mt-0.5 shrink-0" />
            <span>
              Dein Abo wurde gekündigt und läuft bis zum {fmtGermanDate(data.expires_at)} weiter.
              Danach wird der Zugriff automatisch deaktiviert. Du kannst jederzeit verlängern.
            </span>
          </div>
        )}
        {isExpired && (
          <div className="mt-4 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
               style={{ background: "rgba(255,69,58,0.10)", border: "1px solid rgba(255,69,58,0.25)", color: "var(--tx-rot)" }}>
            <X size={14} className="mt-0.5 shrink-0" />
            <span>Kein aktives Abo. Bitte unten eine Verlängerung beim Betreiber anfragen — nach Freigabe geht es sofort weiter.</span>
          </div>
        )}
        {data.anfrage_offen && (
          <div className="mt-4 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
               style={{ background: "rgba(10,132,255,0.10)", border: "1px solid rgba(10,132,255,0.25)", color: "var(--tx-blau)" }}
               data-testid="abo-anfrage-offen">
            <Check size={14} className="mt-0.5 shrink-0" />
            <span>
              Deine Verlängerungs-Anfrage ({data.anfrage?.wanted_plan === "yearly" ? "Jahr" : "Monat"}) liegt beim Betreiber
              und wartet auf Freigabe. Du musst nichts weiter tun.
            </span>
          </div>
        )}
      </div>

      {/* Aktionen */}
      {!data.is_lifetime && (
        <>
          <div className="grid md:grid-cols-2 gap-4 mb-4">
            <PlanCard
              title="Monatsabo"
              price="150 €"
              suffix="/ 30 Tage"
              tagline="Netto zzgl. USt, Abrechnung per Rechnung — Freischaltung durch den Betreiber."
              testid="abo-renew-monthly"
              busy={busy === "monthly"}
              disabled={data.anfrage_offen}
              onClick={() => renew("monthly")}
              ctaLabel={data.anfrage_offen ? "Anfrage liegt beim Betreiber" : "Verlängerung anfragen (Monat)"}
            />
            <PlanCard
              title="Jahresabo"
              price="1.500 €"
              suffix="/ 365 Tage"
              tagline="Spart 300 € gegenüber 12 Monatsabos (1.800 €), netto zzgl. USt."
              highlight
              testid="abo-renew-yearly"
              busy={busy === "yearly"}
              disabled={data.anfrage_offen}
              onClick={() => renew("yearly")}
              ctaLabel={data.anfrage_offen ? "Anfrage liegt beim Betreiber" : "Verlängerung anfragen (Jahr)"}
            />
          </div>

          {/* Kündigen */}
          {data.can_cancel && !confirming && (
            <button
              onClick={() => setConfirming(true)}
              className="text-xs text-zinc-500 hover:text-red-400 underline underline-offset-2"
              data-testid="abo-cancel-btn"
            >
              Abo zum Ende der Periode kündigen
            </button>
          )}

          {confirming && (
            <div className="apple-card p-4"
                 style={{ background: "rgba(255,69,58,0.08)", border: "1px solid rgba(255,69,58,0.25)" }}
                 data-testid="abo-cancel-confirm">
              <div className="flex items-start gap-3">
                <X size={16} className="text-red-400 mt-0.5 shrink-0" />
                <div className="flex-1">
                  <div className="font-bold text-sm text-white mb-1">Abo wirklich kündigen?</div>
                  <div className="text-xs text-zinc-400 mb-3">
                    Dein Abo läuft bis zum <b className="text-white">{fmtGermanDate(data.expires_at)}</b> weiter.
                    Danach wird die Plattform deaktiviert. Du kannst es jederzeit wieder verlängern.
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={cancel}
                      disabled={busy === "cancel"}
                      className="apple-btn"
                      style={{ background: "var(--st-rot)", color: "white" }}
                      data-testid="abo-cancel-confirm-btn"
                    >
                      {busy === "cancel" ? "Lade…" : "Ja, kündigen"}
                    </button>
                    <button
                      onClick={() => setConfirming(false)}
                      className="apple-btn apple-btn-secondary"
                      data-testid="abo-cancel-abort-btn"
                    >
                      Abbrechen
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}

      {data.is_lifetime && (
        <div className="apple-card p-4 text-sm text-zinc-300"
             style={{ background: "rgba(52,199,89,0.08)", border: "1px solid rgba(52,199,89,0.25)" }}>
          <span className="font-bold text-[var(--accent-green)]">Lifetime-Account</span> — du hast unbegrenzten Zugriff. Keine Verlängerung nötig.
        </div>
      )}
    </Section>
  );
}

function InfoRow({ icon, label, children }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-wider font-bold mb-1 flex items-center gap-1.5"
           style={{ color: "var(--text-muted)" }}>
        {icon}{label}
      </div>
      {children}
    </div>
  );
}

function PlanCard({ title, price, suffix, tagline, highlight, busy, disabled, onClick, testid, ctaLabel }) {
  return (
    <div
      className="apple-card p-5 relative"
      style={highlight
        ? { borderColor: "rgba(255,59,48,0.4)", background: "rgba(255,59,48,0.04)" }
        : {}}
    >
      {highlight && (
        <div
          className="absolute -top-2.5 left-4 px-2 py-0.5 text-[9px] uppercase tracking-[0.2em] font-bold rounded-sm"
          style={{ background: "var(--accent-red)", color: "white" }}
        >
          Spart 300 €
        </div>
      )}
      <div className="overline">{title}</div>
      <div className="mt-1.5 flex items-baseline gap-1.5">
        <span className="font-display font-black text-3xl tracking-tighter">{price}</span>
        <span className="text-zinc-400 text-xs">{suffix}</span>
      </div>
      <p className="text-zinc-400 text-xs mt-2">{tagline}</p>
      <button
        data-testid={testid}
        disabled={busy || disabled}
        onClick={onClick}
        className={`apple-btn mt-4 w-full ${highlight ? "apple-btn-primary" : "apple-btn-secondary"}`}
      >
        {busy ? "Lade…" : <>{ctaLabel} <ArrowRight size={13} /></>}
      </button>
    </div>
  );
}
