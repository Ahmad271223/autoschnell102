import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";
import {
  Building2, Sliders, FileText, Mail, MessageSquare, ShieldCheck, Save, Check, Globe,
  CreditCard, Calendar, X, ArrowRight, Bolt, Store,
} from "lucide-react";
import CountryPicker from "@/components/CountryPicker";

const SECTIONS = [
  { id: "profile",    label: "Profil",         icon: Building2 },
  { id: "rules",      label: "Vergleich",      icon: Sliders },
  { id: "templates",  label: "Versand",        icon: Mail },
  { id: "markt",      label: "Marktplatz",     icon: Store },
  { id: "agb",        label: "AGB & Vereinb.", icon: ShieldCheck },
  { id: "abo",        label: "Abo",            icon: CreditCard },
];

export default function Einstellungen() {
  const { dealer, refresh, user } = useAuth();
  // Sucher sehen keinen Marktplatz-Reiter (nur der Chef verwaltet den
  // Marktplatz; die Endpunkte antworten Suchern mit 403 -> ewig "Lädt…").
  const sections = user?.role === "sucher"
    ? SECTIONS.filter((s) => s.id !== "markt")
    : SECTIONS;
  const [form, setForm] = useState(null);
  const [active, setActive] = useState("profile");
  const [savedFlash, setSavedFlash] = useState(false);

  useEffect(() => {
    if (dealer) setForm({
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
      default_terms: dealer.default_terms || "",
      default_special_agreements: dealer.default_special_agreements || "",
    });
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
  const setFeature = (key, mode) => {
    const features = { ...(form[rulesKey]?.features || {}) };
    features[key] = { mode };
    setForm({ ...form, [rulesKey]: { ...(form[rulesKey] || {}), features } });
  };

  const save = async () => {
    try {
      // _edit_profile ist nur UI-State, nicht ans Backend schicken
      const { _edit_profile, ...payload } = form;
      await api.put("/dealer/settings", payload);
      await refresh();
      toast.success("Einstellungen gespeichert");
      setSavedFlash(true);
      setTimeout(() => setSavedFlash(false), 1500);
    } catch (err) {
      toast.error(errMsg(err, "Fehler beim Speichern"));
    }
  };

  const setActiveProfile = async (p) => {
    setForm({ ...form, active_profile: p });
    try {
      await api.put("/dealer/active-profile", { active_profile: p });
      toast.success(p === "inland" ? "Inland-Profil aktiv" : "Export-Profil aktiv");
    } catch (err) {
      toast.error(errMsg(err, "Wechsel fehlgeschlagen"));
    }
  };

  const r = form[rulesKey] || {};
  const features = r.features || {};

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
        <button onClick={save} data-testid="save-settings-btn"
                className={`apple-btn ${savedFlash ? "apple-btn-secondary" : "apple-btn-primary"}`}>
          {savedFlash ? <><Check size={14} /> Gespeichert</> : <><Save size={14} /> Speichern</>}
        </button>
      </div>

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
                          ? "bg-white/[0.08] text-white shadow-[inset_0_0_0_1px_rgba(255,255,255,0.06)]"
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
                     style={{ background: "rgba(255,255,255,0.05)", border: "1px solid var(--divider)" }}>
                  {form.profile.logo_url
                    ? <img src={form.profile.logo_url.startsWith("http") ? form.profile.logo_url : `${process.env.REACT_APP_BACKEND_URL}${form.profile.logo_url}`}
                           alt="Logo" className="w-full h-full object-contain" />
                    : <Building2 size={26} className="text-zinc-600" />}
                </div>
                <div>
                  <label className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white cursor-pointer"
                         style={{ background: "var(--accent-red)" }} data-testid="set-logo">
                    Logo hochladen
                    <input type="file" accept="image/*" className="hidden"
                           onChange={(e) => uploadLogo(e.target.files?.[0])} />
                  </label>
                  <div className="text-[11px] text-zinc-500 mt-1.5">PNG/JPG, max. 2 MB. Erscheint auf Vertrag & Marktplatz.</div>
                  {form.profile.logo_url && (
                    <button type="button" onClick={() => setProfile("logo_url", "")}
                            className="text-[11px] text-zinc-500 hover:text-red-400 mt-1">Logo entfernen</button>
                  )}
                </div>
              </div>

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
                     style={{ background: "rgba(255,255,255,0.04)", border: "1px solid var(--divider)" }}>
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
                      color: form.active_profile === "inland" ? "var(--accent-green)" : "#0a84ff",
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
                                 value={r.first_registration?.years || 1}
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
                  <AppleNumber testid="rule-km-value" value={r.mileage?.value || 30000}
                               onChange={(v) => setRule("mileage", { ...r.mileage, value: v })}
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
                             ]} />
                {(r.power?.mode === "tolerance_ps" || r.power?.mode === "tolerance_kw") && (
                  <AppleNumber testid="rule-pwr-value" value={r.power?.value || 5}
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

              <RuleRow label="Kategorie">
                <AppleSelect testid="rule-cat-mode" value={r.category?.mode || "exact"}
                             onChange={(v) => setRule("category", { mode: v })}
                             options={[{ v: "ignore", l: "Nicht übernehmen" }, { v: "exact", l: "1:1 übernehmen" }]} />
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

              <RuleRow label="Land">
                <CountryPicker
                  value={r.country || { mode: "exact", codes: ["DE"] }}
                  onChange={(v) => setRule("country", v)}
                />
              </RuleRow>

              {/* Ausstattungsfilter — Navigation + Klimatisierung. */}
              <RuleRow label="Navigation">
                <AppleSelect testid="rule-navi-mode"
                             value={features.navigation?.mode || "ignore"}
                             onChange={(v) => setFeature("navigation", v)}
                             options={[
                               { v: "ignore", l: "Egal" },
                               { v: "exact",  l: "Nur wenn Fahrzeug es hat" },
                               { v: "always", l: "Immer pflichtig" },
                             ]} />
              </RuleRow>

              <RuleRow label="Klimatisierung" last>
                <AppleSelect testid="rule-climate-mode"
                             value={r.climatisation?.mode || "ignore"}
                             onChange={(v) => setRule("climatisation", { ...(r.climatisation || {}), mode: v })}
                             options={[
                               { v: "ignore", l: "Egal" },
                               { v: "exact",  l: "Auto erkennen aus Inserat" },
                               { v: "always", l: "Immer mit folgendem Typ:" },
                             ]} />
                {r.climatisation?.mode === "always" && (
                  <AppleSelect testid="rule-climate-value"
                               value={r.climatisation?.value || "AUTOMATIC_CLIMATISATION"}
                               onChange={(v) => setRule("climatisation", { ...(r.climatisation || {}), value: v })}
                               options={[
                                 { v: "AUTOMATIC_CLIMATISATION",         l: "Klimaautomatik" },
                                 { v: "MANUAL_CLIMATISATION",            l: "Klimaanlage o. -automatik" },
                                 { v: "AUTOMATIC_CLIMATISATION_2_ZONES", l: "2-Zonen-Klimaautomatik" },
                                 { v: "AUTOMATIC_CLIMATISATION_3_ZONES", l: "3-Zonen-Klimaautomatik" },
                                 { v: "AUTOMATIC_CLIMATISATION_4_ZONES", l: "4-Zonen-Klimaautomatik" },
                                 { v: "NO_CLIMATISATION",                l: "Keine Klimaanlage" },
                               ]} />
                )}
              </RuleRow>
            </Section>
          )}

          {active === "templates" && (
            <Section title="Versand-Vorlagen"
                     subtitle="Standardtexte für E-Mail und WhatsApp beim Verschicken eines Kaufvertrags.">
              <AppleField label="E-Mail-Betreff" value={form.email_subject}
                          onChange={(v) => setForm({ ...form, email_subject: v })}
                          testid="set-email-subject" />
              <AppleTextarea label="E-Mail-Text" rows={6} value={form.email_template}
                             onChange={(v) => setForm({ ...form, email_template: v })}
                             icon={Mail} testid="set-email-template" />
              <AppleTextarea label="WhatsApp-Text" rows={5} value={form.whatsapp_template}
                             onChange={(v) => setForm({ ...form, whatsapp_template: v })}
                             icon={MessageSquare} testid="set-wa-template" />
              <PlaceholderHint placeholders={[
                "{kunde_name}", "{fahrzeug}", "{marke}", "{modell}",
                "{abholdatum}", "{händler_name}", "{telefon}", "{email}",
              ]} />
            </Section>
          )}

          {active === "agb" && (
            <Section title="AGB & Besondere Vereinbarungen"
                     subtitle="Diese Texte werden bei jedem neuen Kaufvertrag automatisch übernommen.">
              <AppleTextarea
                label="Allgemeine Geschäftsbedingungen (AGB)"
                rows={12}
                value={form.default_terms}
                onChange={(v) => setForm({ ...form, default_terms: v })}
                icon={ShieldCheck}
                testid="set-default-terms"
                hint="Werden bei jedem PDF als eigener Block am Ende des Vertrags eingefügt."
              />
              <AppleTextarea
                label="Standard-Besondere-Vereinbarungen"
                rows={6}
                value={form.default_special_agreements}
                onChange={(v) => setForm({ ...form, default_special_agreements: v })}
                icon={FileText}
                testid="set-default-agreements"
                hint='Wird in das Feld „Besondere Vereinbarungen" jedes neuen Vertrags vorbelegt — kann beim Erstellen überschrieben werden.'
              />
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

function AppleField({ label, value, onChange, testid }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">{label}</label>
      <input data-testid={testid} value={value || ""}
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
  return (
    <input data-testid={testid} type="number" min={min} max={max} value={value}
           onChange={(e) => onChange(Number(e.target.value))}
           className={`apple-input ${className}`} />
  );
}

function AppleTextarea({ label, rows = 4, value, onChange, hint, testid, icon: Icon }) {
  return (
    <div className="space-y-1.5">
      <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
        {Icon && <Icon size={12} />} {label}
      </label>
      <textarea data-testid={testid} rows={rows} value={value || ""}
                onChange={(e) => onChange(e.target.value)}
                className="apple-input resize-y leading-relaxed" />
      {hint && <div className="text-[11px] text-zinc-500 leading-snug">{hint}</div>}
    </div>
  );
}

function PlaceholderHint({ placeholders }) {
  return (
    <div className="text-[11px] text-zinc-500 mt-1 flex items-center gap-2 flex-wrap">
      <span className="font-semibold text-zinc-400">Platzhalter:</span>
      {placeholders.map((p) => (
        <code key={p} className="px-1.5 py-0.5 rounded-md bg-white/[0.05] text-zinc-300 border border-white/[0.06]">
          {p}
        </code>
      ))}
    </div>
  );
}

/* ── Abo-Panel ── */

const PLAN_LABEL = {
  monthly:  "Monatsabo",
  yearly:   "Jahresabo",
  lifetime: "Lifetime",
  trial:    "Test-Phase",
};

const STATUS_BADGE = {
  active:    { label: "Aktiv",     bg: "rgba(52,199,89,0.15)",  fg: "var(--accent-green)", border: "rgba(52,199,89,0.35)" },
  cancelled: { label: "Gekündigt", bg: "rgba(255,159,10,0.18)", fg: "#ff9f0a",             border: "rgba(255,159,10,0.4)" },
  expired:   { label: "Abgelaufen",bg: "rgba(255,69,58,0.18)",  fg: "#ff453a",             border: "rgba(255,69,58,0.4)" },
  none:      { label: "Kein Abo",  bg: "rgba(255,255,255,0.08)",fg: "#a1a1aa",             border: "rgba(255,255,255,0.10)" },
};

function fmtGermanDate(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("de-DE", {
      day: "2-digit", month: "long", year: "numeric",
    });
  } catch { return iso; }
}

function MarketplacePanel() {
  const [mp, setMp] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try { const { data } = await api.get("/dealer/marketplace-profile"); setMp(data); }
    catch (e) { toast.error(errMsg(e, "Marktplatz-Profil konnte nicht geladen werden")); }
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

  if (!mp) return <Section title="Marktplatz"><div className="text-sm text-zinc-500">Lädt…</div></Section>;

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
        veröffentlichen" oder „Nur Netzwerk (privat)"). Dafür ist ein aktives Verkaufspaket nötig.
      </div>
    </Section>
  );
}

function InvitePanel() {
  const [invites, setInvites] = useState(null);
  const [validity, setValidity] = useState(168);
  const [uses, setUses] = useState(1);

  const load = async () => {
    try { const { data } = await api.get("/dealer/invites"); setInvites(data); }
    catch (e) { setInvites([]); }
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
    const link = `${window.location.origin}/markt/registrieren?invite=${inv.token}`;
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
      {invites === null ? (
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

  const load = async () => {
    try { const { data } = await api.get("/dealer/network/members"); setMembers(data); }
    catch (e) { setMembers([]); }
  };
  useEffect(() => { load(); }, []);

  const revoke = async (m) => {
    if (!window.confirm(`${m.company_name || m.email} aus dem Netzwerk entfernen? Er sieht deine privaten Inserate danach nicht mehr.`)) return;
    try { await api.delete(`/dealer/network/members/${m.buyer_user_id}`); toast.success("Zugang widerrufen"); load(); }
    catch (e) { toast.error(errMsg(e)); }
  };

  return (
    <div className="mt-4 pt-4 border-t" style={{ borderColor: "var(--divider)" }} data-testid="network-members">
      <div className="text-sm font-semibold mb-1">Netzwerk-Mitglieder ({members?.length ?? "…"})</div>
      <div className="text-xs text-zinc-500 mb-2">
        Zwischenhändler, die über eine Einladung beigetreten sind. Zugang jederzeit widerrufbar.
      </div>
      {members === null ? (
        <div className="text-xs text-zinc-500">Lädt…</div>
      ) : members.length === 0 ? (
        <div className="text-xs text-zinc-500">Noch keine Mitglieder.</div>
      ) : (
        <div className="space-y-1.5">
          {members.map((m) => (
            <div key={m.buyer_user_id} className="flex flex-wrap items-center gap-2 text-xs rounded-lg border px-3 py-2"
                 style={{ borderColor: "var(--divider)" }}>
              <span className="font-semibold">{m.company_name || "—"}</span>
              <span className="text-zinc-400">{m.contact_name}</span>
              <span className="text-zinc-500">{m.email}</span>
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
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const { data } = await api.get("/dealer/subscription");
      setData(data);
    } catch (err) {
      toast.error(errMsg(err, "Abo-Info konnte nicht geladen werden"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const cancel = async () => {
    setBusy("cancel");
    try {
      const { data } = await api.post("/dealer/subscription/cancel");
      toast.success(data?.message || "Abo gekündigt");
      setConfirming(false);
      await load();
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
  const planLabel = PLAN_LABEL[data.plan] || (data.plan ? data.plan : "—");
  const isCancelled = data.status === "cancelled";
  const isExpired = data.status === "expired" || data.status === "none";

  return (
    <Section title="Abo & Zahlung"
             subtitle="Übersicht zum aktuellen Abo, Verlängerung und Kündigung.">
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
               style={{ background: "rgba(255,159,10,0.10)", border: "1px solid rgba(255,159,10,0.25)", color: "#ffd9a3" }}
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
               style={{ background: "rgba(255,69,58,0.10)", border: "1px solid rgba(255,69,58,0.25)", color: "#ffb3a8" }}>
            <X size={14} className="mt-0.5 shrink-0" />
            <span>Kein aktives Abo. Bitte unten eine Verlängerung beim Betreiber anfragen — nach Freigabe geht es sofort weiter.</span>
          </div>
        )}
        {data.anfrage_offen && (
          <div className="mt-4 px-3 py-2.5 rounded-lg text-xs flex items-start gap-2"
               style={{ background: "rgba(10,132,255,0.10)", border: "1px solid rgba(10,132,255,0.25)", color: "#bcd9ff" }}
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
              suffix="/ Monat"
              tagline="Abrechnung per Rechnung — Freischaltung durch den Betreiber."
              testid="abo-renew-monthly"
              busy={busy === "monthly"}
              disabled={data.anfrage_offen}
              onClick={() => renew("monthly")}
              ctaLabel={data.anfrage_offen ? "Anfrage liegt beim Betreiber" : "Verlängerung anfragen (Monat)"}
            />
            <PlanCard
              title="Jahresabo"
              price="1.500 €"
              suffix="/ Jahr"
              tagline="Spart 300 € gegenüber monatlich (1.800 €)."
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
                      style={{ background: "#ff453a", color: "white" }}
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
          2 Monate gratis
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
