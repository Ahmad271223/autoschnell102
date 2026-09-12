import { useUngespeichert } from "@/lib/ungespeichert";
import { useEffect, useRef, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { X, Eye, FileText, Loader2, AlertTriangle, ExternalLink } from "lucide-react";
import DamageSelector from "./DamageSelector";
import { fehlendeKaeuferfelder, kaeuferAusProfil, kaeuferLueckenFuellen } from "@/lib/kaeuferdaten";

const YN_OPTIONS = [
  { value: "", label: "—" },
  { value: "Ja", label: "Ja" },
  { value: "Nein", label: "Nein" },
];

const TIRE_OPTIONS = [
  { value: "", label: "—" },
  { value: "4-fach", label: "4-fach (1 Satz)" },
  { value: "8-fach", label: "8-fach (Sommer + Winter)" },
  { value: "keine", label: "Keine / nicht enthalten" },
];

// Runde 22 (11.09.2026): Zulassungsstatus wie auf Ahmads Vertragsvorlage.
const ZULASSUNG_OPTIONS = [
  { value: "", label: "—" },
  { value: "angemeldet", label: "Angemeldet" },
  { value: "abgemeldet", label: "Abgemeldet" },
];

// Runde 22 (11.09.2026): Zahlungsart als Auswahl statt Freitext
// "Bar / Überweisung" — leer = noch nicht gewählt (Pflicht beim Erstellen).
const PAYMENT_OPTIONS = [
  { value: "", label: "— bitte wählen —" },
  { value: "Bar", label: "Bar" },
  { value: "Überweisung", label: "Überweisung" },
  { value: "Echtzeitüberweisung", label: "Echtzeitüberweisung" },
];

// HU-Datum Auto-Formatter: nur Ziffern, automatisch "/" nach 2 Ziffern.
// Akzeptiert MM/JJ (5 Zeichen) oder MM/JJJJ (7 Zeichen).
//   "0626"   -> "06/26"
//   "062026" -> "06/2026"
//   "06"     -> "06"   (Slash kommt erst beim 3. Zeichen)
const formatHuDate = (raw) => {
  if (raw === undefined || raw === null) return "";
  const digits = String(raw).replace(/\D/g, "").slice(0, 6);
  if (digits.length <= 2) return digits;
  return digits.slice(0, 2) + "/" + digits.slice(2);
};

// Numerische Helper für Vorhalter (nur ganze Zahlen, max 2 Stellen).
const cleanIntStr = (raw, max = 2) => {
  if (raw === undefined || raw === null) return "";
  return String(raw).replace(/\D/g, "").slice(0, max);
};

// Runde 22 (11.09.2026): heutiges LOKALES Datum als JJJJ-MM-TT für die
// Empfangsbestätigung. Bewusst nicht toISOString() — das rechnet in UTC
// und liefert nachts (vor 02:00 deutscher Zeit) noch den Vortag.
const todayLocalIso = () => {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

export default function ContractDialog({ open, onClose, vehicle, vehicleId, onCreated }) {
  const { dealer, refresh } = useAuth();
  const v = vehicle || {};
  // Runde 22 (11.09.2026, Nachprüfung): Vorgabe fürs Empfangsdatum einmal
  // beim Öffnen festhalten — set() vergleicht damit (siehe unten).
  const [heute] = useState(todayLocalIso);
  const [form, setForm] = useState({
    seller_name: v.seller_name || "",
    seller_address: v.seller_address || "",
    seller_zip: v.seller_zip || "",
    seller_city: v.seller_city || "",
    seller_phone: v.seller_phone || "",
    seller_email: v.seller_email || "",
    purchase_price: "",
    payment_method: "",
    pickup_date: "",
    pickup_time: "",
    additional_terms: dealer?.default_special_agreements || "",
    agb_text: dealer?.default_terms || "",
    notes: "",
    id_document: "",
    tires: "",
    hu_valid: "",
    hu_until: "",
    accident_free: "",
    accident_location: "",
    eu_import: "",
    drivable: "",
    commercial_since_ez: "",
    previous_owners: cleanIntStr(v.previous_owners ?? ""),
    vehicle_description: v.description || "",
    damages: [],
    damages_text: "",
    show_vat: false,

    // Runde 22 (11.09.2026): Zulassungsstatus + Empfangsbestätigung wie auf
    // der Papiervorlage ("Käufer bestätigt Empfang von … / Verkäufer
    // bestätigt Empfang von …", jeweils Datum und Ort). Nicht angehakte
    // Kästchen erscheinen im PDF leer zum Ankreuzen von Hand.
    zulassung: "",
    empfang_zulassungsbescheinigung: false,
    empfang_schluessel: false,
    schluessel_anzahl: "",
    empfang_kaufpreis: false,
    empfang_datum: heute,
    empfang_ort_kaeufer: dealer?.city || "",
    empfang_ort_verkaeufer: v.seller_city || "",

    // Fahrzeugdaten — vom Inserat vorbefüllt, vor Vertrags-Erstellung
    // editierbar (z.B. wenn Verkäufer abweichende Angaben macht).
    vehicle_make: v.make_label || v.make || "",
    vehicle_model: v.model_label || v.model_description || v.model || "",
    vehicle_category: v.category_label || v.category || "",
    vehicle_first_registration: v.first_registration || v.ezl || "",
    vehicle_mileage: v.mileage || v.km || "",
    vehicle_fuel: v.fuel_label || v.fuel_type || v.fuel || "",
    vehicle_gearbox: v.gearbox_label || v.transmission || v.gearbox || "",
    vehicle_power_kw: v.power_kw || "",
    vehicle_power_ps: v.power_ps || (v.power_kw ? Math.round(v.power_kw * 1.36) : ""),
    vehicle_displacement: v.displacement || v.cubic_capacity || "",
    vehicle_color: v.exterior_color || v.color || "",
    vehicle_doors: v.door_count || v.doors || "",
    vehicle_seats: v.seat_count || v.seats || "",
    vehicle_vin: v.vin || v.fin || "",
    vehicle_license_plate: v.license_plate || v.kennzeichen || "",
    vehicle_damage_note: v.damage_unrepaired ? "Motorschaden / Unfallschaden vorhanden"
                       : (v.accident_damaged ? "Unfallschaden" : ""),

    // Händler-Profil — pre-filled, kann pro Vertrag überschrieben werden
    // (z.B. abweichende Telefonnummer im Vertretungsfall).
    ...kaeuferAusProfil(dealer),
  });
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  // Runde 31: rund 60 Felder ohne Zwischenspeicher — solange der Dialog offen
  // ist, fragt der Browser vor dem Neuladen oder Schliessen nach.
  useUngespeichert(Boolean(open));
  // Runde 24 (11.09.2026): Käuferdaten sind Pflicht (Wunsch Ahmad). Der
  // Hinweis sagt, was die EINSTELLUNGEN offen lassen — daher aus dem Profil
  // abgeleitet, nicht aus dem Formular: er bleibt stehen, während der
  // Sucher tippt.
  const fehltInEinstellungen = fehlendeKaeuferfelder(kaeuferAusProfil(dealer));
  const kaeuferRef = useRef(null);

  // Runde 24 (11.09.2026, Gegenprüfung): useAuth().dealer wird nur beim
  // App-Start/Login geladen. Speichert der Sucher seine Käuferdaten über den
  // Link im Hinweis in einem ANDEREN Tab (oder ergänzt der Chef die
  // Firmenadresse), wäre das Profil hier veraltet: der Hinweis stünde
  // wieder da und die Pflicht blockierte "PDF erstellen", obwohl die Daten
  // gespeichert sind. Deshalb beim Öffnen frisch laden und nur LEERE
  // Käuferfelder nachfüllen (Getipptes bleibt). refresh() behält bei
  // Netzfehlern den geladenen Stand und hängt die Seite nicht aus.
  useEffect(() => {
    if (!open || !refresh) return undefined;
    let aktiv = true;
    Promise.resolve(refresh())
      .then((data) => {
        if (aktiv && data?.dealer) setForm((f) => kaeuferLueckenFuellen(f, data.dealer));
      })
      .catch(() => {});
    return () => { aktiv = false; };
  }, [open, refresh]);

  if (!open) return null;

  // Runde 22 (11.09.2026): funktional updaten — sonst überschreiben sich
  // schnell aufeinanderfolgende Änderungen (Checkbox + abhängiges Feld)
  // mit einem veralteten form-Stand.
  //
  // Runde 22 (11.09.2026, Nachprüfung): Die Vorgaben der Empfangsbestätigung
  // folgen ihrer Quelle, solange der Sucher sie nicht von Hand abweichend
  // geändert hat:
  //  - Datum folgt dem Abholdatum (= Übergabetag; der Vertrag wird meist
  //    Tage vorher angelegt). Ohne Abholdatum gilt wieder "heute".
  //  - Ort (Verkäufer) folgt "Verkäufer / Halter → Ort", Ort (Käufer)
  //    folgt "Käufer → Ort" (z.B. Ort im Inserat fehlte und wird nachgetragen).
  const set = (k, v) => setForm((f) => {
    const next = { ...f, [k]: v };
    if (k === "pickup_date" && f.empfang_datum === (f.pickup_date || heute)) {
      next.empfang_datum = v || heute;
    }
    if (k === "seller_city" && f.empfang_ort_verkaeufer === f.seller_city) {
      next.empfang_ort_verkaeufer = v;
    }
    if (k === "dealer_city" && f.empfang_ort_kaeufer === f.dealer_city) {
      next.empfang_ort_kaeufer = v;
    }
    return next;
  });

  // Eintippen einer Schlüsselanzahl hakt "KFZ mit __ Schlüssel(n)" gleich an.
  // Nachprüfung: führende Nullen fallen weg — "0" ist keine Anzahl und darf
  // nicht "KFZ mit 0 Schlüssel(n)" angekreuzt ins PDF bringen.
  const setSchluesselAnzahl = (raw) => {
    const n = cleanIntStr(raw).replace(/^0+/, "");
    setForm((f) => ({
      ...f,
      schluessel_anzahl: n,
      empfang_schluessel: n ? true : f.empfang_schluessel,
    }));
  };

  const buildPayload = () => ({
    vehicle_id: vehicleId,
    ...form,
    purchase_price: form.purchase_price ? Number(form.purchase_price) : 0,
  });

  const openPreview = async () => {
    if (!form.purchase_price || Number(form.purchase_price) <= 0) {
      toast.error("Bitte Kaufpreis eingeben (auch für Vorschau erforderlich)");
      return;
    }
    setPreviewing(true);
    try {
      const res = await api.post("/contracts/preview", buildPayload(), {
        responseType: "blob",
      });
      const blob = new Blob([res.data], { type: "application/pdf" });
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener,noreferrer");
      // Revoke after a delay so the new tab has time to load
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (err) {
      toast.error(errMsg(err, "Vorschau konnte nicht erzeugt werden"));
    } finally {
      setPreviewing(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    // Runde 24 (11.09.2026): Käuferdaten Pflicht beim Erstellen (die Vorschau
    // geht weiterhin ohne). Leere Felder hält schon required auf; hier
    // zusätzlich nur-Leerzeichen — mit Feldnamen und Sprung zum Abschnitt.
    const fehlend = fehlendeKaeuferfelder(form);
    if (fehlend.length > 0) {
      toast.error(`Bitte Käuferdaten ergänzen: ${fehlend.map((f) => f.label).join(", ")}`);
      const abschnitt = kaeuferRef.current;
      abschnitt?.scrollIntoView({ behavior: "smooth", block: "start" });
      abschnitt
        ?.querySelector(`[data-testid="contract-${fehlend[0].key.replace("_", "-")}"]`)
        ?.focus({ preventScroll: true });
      return;
    }
    if (!form.purchase_price || Number(form.purchase_price) <= 0) {
      toast.error("Bitte Kaufpreis manuell eingeben");
      return;
    }
    // Runde 22 (11.09.2026): Zahlungsart ist Pflicht beim Erstellen
    // (die Vorschau geht weiterhin ohne).
    if (!form.payment_method) {
      toast.error("Bitte Zahlungsart wählen");
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.post("/contracts", buildPayload());
      // Runde 15: der Vertrag ist gespeichert, auch wenn der automatische
      // Abholtermin nicht angelegt werden konnte — der Server sagt es.
      if (data?.termin_hinweis) toast.warning(data.termin_hinweis, { duration: 8000 });
      onCreated?.(data);
    } catch (err) {
      toast.error(errMsg(err, "PDF konnte nicht erstellt werden"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
      <div className="bg-[var(--bg-surface)] border w-full max-w-4xl max-h-[92vh] overflow-y-auto rounded-2xl"
           style={{ borderColor: "var(--border-default)" }} data-testid="contract-dialog">
        <div className="flex items-center justify-between px-6 py-4 border-b sticky top-0 bg-[var(--bg-surface)] z-10"
             style={{ borderColor: "var(--border-default)" }}>
          <div>
            <div className="overline">Kaufvertrag</div>
            <div className="font-display font-bold text-lg">
              {vehicle?.make_label} {vehicle?.model_label}
            </div>
          </div>
          <button onClick={onClose} className="text-zinc-400 hover:text-white" data-testid="close-contract">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={submit} className="p-6 space-y-6">
          {/* Verkäufer + Käufer side-by-side on lg, stacked on small */}
          <div className="grid lg:grid-cols-2 gap-5">
            <Section title="Verkäufer / Halter">
              <Field label="Name / Firma *" required value={form.seller_name} onChange={(v) => set("seller_name", v)} testid="contract-seller-name" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Telefon" value={form.seller_phone} onChange={(v) => set("seller_phone", v)} testid="contract-seller-phone" />
                <Field label="E-Mail" type="email" value={form.seller_email} onChange={(v) => set("seller_email", v)} testid="contract-seller-email" />
              </div>
              <Field label="Adresse" value={form.seller_address} onChange={(v) => set("seller_address", v)} testid="contract-seller-address" />
              <div className="grid grid-cols-3 gap-3">
                <Field label="PLZ" value={form.seller_zip} onChange={(v) => set("seller_zip", v)} testid="contract-seller-zip" />
                <Field label="Ort" value={form.seller_city} onChange={(v) => set("seller_city", v)} testid="contract-seller-city" />
                <Field label="Ausweis-Nr." value={form.id_document} onChange={(v) => set("id_document", v)} testid="contract-id-doc" />
              </div>
            </Section>

            {/* Runde 24 (11.09.2026): Firma/Adresse/PLZ/Ort sind Pflicht —
                Käufer im Kaufvertrag, Auftraggeber im Abholprotokoll. */}
            <div ref={kaeuferRef} style={{ scrollMarginTop: "5rem" }} data-testid="contract-kaeufer">
            <Section title="Käufer (Händler — du)">
              {fehltInEinstellungen.length > 0 && (
                <div role="alert" data-testid="contract-kaeufer-fehlt"
                     className="flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-sm leading-snug"
                     style={{
                       borderColor: "var(--accent-red)",
                       background: "color-mix(in srgb, var(--accent-red) 12%, transparent)",
                       color: "var(--text-primary)",
                     }}>
                  <AlertTriangle size={16} className="shrink-0 mt-0.5" style={{ color: "var(--accent-red)" }} />
                  <div>
                    <div className="font-semibold">Käuferdaten fehlen in deinen Einstellungen</div>
                    <div className="text-[12px] mt-0.5" style={{ color: "var(--text-secondary)" }}>
                      Fehlt: {fehltInEinstellungen.map((f) => f.label).join(", ")}. Bitte hier eintragen —
                      ohne diese Angaben wird kein Kaufvertrag erstellt.
                    </div>
                    <a href="/app/einstellungen" target="_blank" rel="noopener noreferrer"
                       data-testid="contract-kaeufer-einstellungen"
                       className="inline-flex items-center gap-1 mt-1.5 text-[12px] font-semibold underline"
                       style={{ color: "var(--accent-blue)" }}>
                      Dauerhaft in den Einstellungen speichern <ExternalLink size={12} />
                    </a>
                    <span className="text-[11px] ml-1" style={{ color: "var(--text-secondary)" }}>
                      (neuer Tab — deine Eingaben hier bleiben erhalten)
                    </span>
                  </div>
                </div>
              )}
              <Field label="Firma *" required value={form.dealer_company} onChange={(v) => set("dealer_company", v)} testid="contract-dealer-company" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Ansprechpartner" value={form.dealer_contact} onChange={(v) => set("dealer_contact", v)} testid="contract-dealer-contact" />
                <Field label="Telefon" value={form.dealer_phone} onChange={(v) => set("dealer_phone", v)} testid="contract-dealer-phone" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="WhatsApp" value={form.dealer_whatsapp} onChange={(v) => set("dealer_whatsapp", v)} testid="contract-dealer-wa" />
                <Field label="E-Mail" type="email" value={form.dealer_email} onChange={(v) => set("dealer_email", v)} testid="contract-dealer-email" />
              </div>
              <Field label="Adresse *" required value={form.dealer_address} onChange={(v) => set("dealer_address", v)} testid="contract-dealer-address" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="PLZ *" required value={form.dealer_zip} onChange={(v) => set("dealer_zip", v)} testid="contract-dealer-zip" />
                <Field label="Ort *" required value={form.dealer_city} onChange={(v) => set("dealer_city", v)} testid="contract-dealer-city" />
              </div>
              <div className="text-[11px] text-zinc-500 leading-relaxed">
                Erscheint als Käufer im Kaufvertrag und als Auftraggeber im Abholprotokoll.
                Aus deinen Einstellungen vorbefüllt — fehlt etwas, hier eintragen (dauerhaft unter Einstellungen).
              </div>
            </Section>
            </div>
          </div>

          {/* Fahrzeugdaten — direkt aus dem Inserat übernommen, vor
              Vertrags-Erstellung anpassbar. */}
          <Section title="Fahrzeugdaten" subtitle="Aus dem Inserat übernommen — bei Bedarf korrigieren.">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Marke" value={form.vehicle_make} onChange={(v) => set("vehicle_make", v)} testid="contract-veh-make" />
              <Field label="Modell" value={form.vehicle_model} onChange={(v) => set("vehicle_model", v)} testid="contract-veh-model" />
              <Field label="Kategorie" value={form.vehicle_category} onChange={(v) => set("vehicle_category", v)} testid="contract-veh-cat" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <Field label="Erstzulassung (MM/JJJJ)" value={form.vehicle_first_registration} onChange={(v) => set("vehicle_first_registration", v)} testid="contract-veh-ez" />
              <Field label="Kilometerstand" value={form.vehicle_mileage} onChange={(v) => set("vehicle_mileage", v)} testid="contract-veh-km" />
              <Field label="Hubraum (ccm)" value={form.vehicle_displacement} onChange={(v) => set("vehicle_displacement", v)} testid="contract-veh-ccm" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="Kraftstoff" value={form.vehicle_fuel} onChange={(v) => set("vehicle_fuel", v)} testid="contract-veh-fuel" />
              <Field label="Getriebe" value={form.vehicle_gearbox} onChange={(v) => set("vehicle_gearbox", v)} testid="contract-veh-gear" />
              <Field label="Leistung (kW)" value={form.vehicle_power_kw} onChange={(v) => set("vehicle_power_kw", v)} testid="contract-veh-kw" />
              <Field label="Leistung (PS)" value={form.vehicle_power_ps} onChange={(v) => set("vehicle_power_ps", v)} testid="contract-veh-ps" />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
              <Field label="Farbe" value={form.vehicle_color} onChange={(v) => set("vehicle_color", v)} testid="contract-veh-color" />
              <Field label="Türen" value={form.vehicle_doors} onChange={(v) => set("vehicle_doors", v)} testid="contract-veh-doors" />
              <Field label="Sitze" value={form.vehicle_seats} onChange={(v) => set("vehicle_seats", v)} testid="contract-veh-seats" />
              <Field
                label="Vorhalter"
                type="number"
                value={form.previous_owners}
                onChange={(v) => set("previous_owners", cleanIntStr(v))}
                testid="contract-veh-prev"
                inputMode="numeric"
                placeholder="z.B. 1"
                helper="Wird automatisch aus dem Inserat erkannt (Halter / Fahrzeughalter / Vorhalter / 2.Hand). Falls leer: bitte selbst eintragen."
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field label="FIN" value={form.vehicle_vin} onChange={(v) => set("vehicle_vin", v)} testid="contract-veh-fin" />
              <Field label="Kennzeichen" value={form.vehicle_license_plate} onChange={(v) => set("vehicle_license_plate", v)} testid="contract-veh-plate" />
            </div>
            <Field label="Sonstige Schäden / Hinweis (erscheint im Vertrag)" value={form.vehicle_damage_note} onChange={(v) => set("vehicle_damage_note", v)} testid="contract-veh-damage" placeholder="z.B. Motorschaden, Hagelschaden" />
          </Section>

          {/* Zusicherungen & Zustand */}
          <Section title="Zusicherungen & Zustand">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Bereifung"
                value={form.tires}
                onChange={(v) => set("tires", v)}
                options={TIRE_OPTIONS}
                testid="contract-tires"
              />
              <SelectField
                label="HU/AU vorhanden"
                value={form.hu_valid}
                onChange={(v) => set("hu_valid", v)}
                options={YN_OPTIONS}
                testid="contract-hu-valid"
              />
              <Field
                label="HU gültig bis (z.B. 06/26)"
                value={form.hu_until}
                onChange={(v) => set("hu_until", formatHuDate(v))}
                testid="contract-hu-until"
                placeholder="MM/JJ"
                disabled={form.hu_valid !== "Ja"}
                inputMode="numeric"
                maxLength={7}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Unfallfrei"
                value={form.accident_free}
                onChange={(v) => set("accident_free", v)}
                options={YN_OPTIONS}
                testid="contract-accident-free"
              />
              <Field
                label="Wenn nicht unfallfrei: wo / Beschreibung"
                value={form.accident_location}
                onChange={(v) => set("accident_location", v)}
                testid="contract-accident-loc"
                placeholder="z.B. Heckschaden rechts"
                disabled={form.accident_free !== "Nein"}
              />
              <SelectField
                label="EU-Import"
                value={form.eu_import}
                onChange={(v) => set("eu_import", v)}
                options={YN_OPTIONS}
                testid="contract-eu-import"
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <SelectField
                label="Fahrtauglich"
                value={form.drivable}
                onChange={(v) => set("drivable", v)}
                options={YN_OPTIONS}
                testid="contract-drivable"
              />
              <SelectField
                label="Gewerblich genutzt seit EZ"
                value={form.commercial_since_ez}
                onChange={(v) => set("commercial_since_ez", v)}
                options={YN_OPTIONS}
                testid="contract-commercial"
              />
              <SelectField
                label="Zulassung"
                value={form.zulassung}
                onChange={(v) => set("zulassung", v)}
                options={ZULASSUNG_OPTIONS}
                testid="contract-zulassung"
              />
            </div>
          </Section>

          <Section title="Schäden / Beschädigungen">
            <DamageSelector
              damages={form.damages}
              onChange={(list, text) =>
                setForm((f) => ({ ...f, damages: list, damages_text: text }))
              }
            />
          </Section>

          <Section title="Konditionen">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field
                label="Kaufpreis (€) *" type="number" required
                value={form.purchase_price} onChange={(v) => set("purchase_price", v)}
                testid="contract-price" placeholder="z.B. 8900"
              />
              <SelectField
                label="Zahlungsart *"
                required
                value={form.payment_method}
                onChange={(v) => set("payment_method", v)}
                options={PAYMENT_OPTIONS}
                testid="contract-payment"
              />
            </div>
            <label className="flex items-start gap-2 rounded-lg border px-3 py-2.5 cursor-pointer"
                   style={{ borderColor: "var(--border-default)" }}
                   data-testid="contract-show-vat">
              <input type="checkbox" checked={!!form.show_vat}
                     onChange={(e) => set("show_vat", e.target.checked)}
                     className="mt-0.5 accent-red-500" />
              <span className="text-sm">
                <span className="font-semibold">MwSt (19 %) im Vertrag ausweisen</span>
                <span className="block text-[11px] text-zinc-500">
                  Für gewerbliche Verkäufe (Regelbesteuerung): der Kaufpreis gilt
                  als Brutto, der Vertrag zeigt Netto und Steuer.
                  {form.show_vat && form.purchase_price > 0 && (
                    <> {" "}Netto {(form.purchase_price / 1.19).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} € ·
                    MwSt {(form.purchase_price - form.purchase_price / 1.19).toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €</>
                  )}
                </span>
              </span>
            </label>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Abholdatum" type="date" value={form.pickup_date} onChange={(v) => set("pickup_date", v)} testid="contract-pickup-date" />
              <Field label="Abholuhrzeit" type="time" value={form.pickup_time} onChange={(v) => set("pickup_time", v)} testid="contract-pickup-time" />
            </div>
            <Field label="Besondere Vereinbarungen" value={form.additional_terms} onChange={(v) => set("additional_terms", v)} multiline rows={4} testid="contract-terms"
                   helper="Aus deinen Einstellungen vorausgefüllt — hier nur für diesen Vertrag anpassbar." />
            <Field label="Notizen (intern)" value={form.notes} onChange={(v) => set("notes", v)} multiline testid="contract-notes" />
          </Section>

          {/* Runde 22 (11.09.2026): Empfangsbestätigung wie auf der
              Papiervorlage — landet im Vertrag unter "Unterschriften". */}
          <Section
            title="Übergabe & Empfangsbestätigung"
            subtitle="Wie im Vertrag unter „Unterschriften“. Leere Kästchen erscheinen im PDF zum Ankreuzen von Hand."
          >
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="rounded-lg border px-3 py-3 space-y-2"
                   style={{ borderColor: "var(--border-default)" }}
                   data-testid="contract-empfang-kaeufer">
                <div className="text-sm font-semibold">Käufer (du) bestätigt Empfang von:</div>
                <CheckRow
                  checked={form.empfang_zulassungsbescheinigung}
                  onChange={(c) => set("empfang_zulassungsbescheinigung", c)}
                  testid="contract-empfang-zb"
                >
                  Zulassungsbescheinigung Teil I &amp; II
                </CheckRow>
                <CheckRow
                  checked={form.empfang_schluessel}
                  onChange={(c) => set("empfang_schluessel", c)}
                  testid="contract-empfang-schluessel"
                >
                  <span>KFZ mit</span>
                  <input
                    type="text"
                    inputMode="numeric"
                    maxLength={2}
                    value={form.schluessel_anzahl}
                    onChange={(e) => setSchluesselAnzahl(e.target.value)}
                    placeholder="__"
                    aria-label="Anzahl Schlüssel"
                    data-testid="contract-schluessel-anzahl"
                    className="input-base w-14 text-center"
                    style={{ padding: "0.25rem 0.5rem" }}
                  />
                  <span>Schlüssel(n)</span>
                </CheckRow>
                <Field label="Ort (Käufer)" value={form.empfang_ort_kaeufer} onChange={(v) => set("empfang_ort_kaeufer", v)} testid="contract-empfang-ort-kaeufer" />
              </div>
              <div className="rounded-lg border px-3 py-3 space-y-2"
                   style={{ borderColor: "var(--border-default)" }}
                   data-testid="contract-empfang-verkaeufer">
                <div className="text-sm font-semibold">Verkäufer bestätigt Empfang von:</div>
                <CheckRow
                  checked={form.empfang_kaufpreis}
                  onChange={(c) => set("empfang_kaufpreis", c)}
                  testid="contract-empfang-kaufpreis"
                >
                  Kaufpreis
                </CheckRow>
                <Field label="Ort (Verkäufer)" value={form.empfang_ort_verkaeufer} onChange={(v) => set("empfang_ort_verkaeufer", v)} testid="contract-empfang-ort-verkaeufer" />
              </div>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <Field
                label="Datum"
                type="date"
                value={form.empfang_datum}
                onChange={(v) => set("empfang_datum", v)}
                testid="contract-empfang-datum"
                helper="Gilt für beide Empfangsbestätigungen. Folgt dem Abholdatum, bis du es hier änderst."
              />
            </div>
          </Section>

          <Section title="Fahrzeugbeschreibung (vom Inserat)">
            <Field
              label="Beschreibungstext"
              value={form.vehicle_description}
              onChange={(v) => set("vehicle_description", v)}
              multiline
              rows={6}
              testid="contract-vehicle-description"
              helper="Wurde automatisch aus dem Inserat übernommen und landet im PDF. Frei editierbar."
            />
          </Section>

          <Section title="Allgemeine Geschäftsbedingungen (AGB)">
            <Field
              label="AGB-Text"
              value={form.agb_text}
              onChange={(v) => set("agb_text", v)}
              multiline
              rows={8}
              testid="contract-agb-text"
              helper="Aus deinen Einstellungen geladen. Änderungen hier gelten nur für diesen einen Vertrag."
            />
          </Section>

          <div className="flex flex-wrap items-center justify-end gap-3 pt-2 sticky bottom-0 bg-[var(--bg-surface)] py-3 -mx-6 px-6 border-t"
               style={{ borderColor: "var(--border-default)" }}>
            <button type="button" onClick={onClose}
                    className="apple-btn apple-btn-secondary" data-testid="cancel-contract">
              Abbrechen
            </button>
            <button type="button" onClick={openPreview} disabled={previewing || loading}
                    className="apple-btn apple-btn-secondary disabled:opacity-60" data-testid="preview-contract-btn">
              {previewing ? <Loader2 size={14} className="animate-spin" /> : <Eye size={14} />}
              {previewing ? "Erzeuge…" : "Vorschau"}
            </button>
            <button type="submit" disabled={loading || previewing} data-testid="submit-contract"
                    className="apple-btn apple-btn-primary disabled:opacity-60">
              {loading ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
              {loading ? "Erstelle PDF…" : "PDF erstellen"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

const Section = ({ title, subtitle, children }) => (
  <div>
    <div className="overline mb-1">{title}</div>
    {subtitle && <div className="text-[11px] text-zinc-500 mb-3">{subtitle}</div>}
    {!subtitle && <div className="mb-3" />}
    <div className="space-y-3">{children}</div>
  </div>
);

const Field = ({ label, value, onChange, type = "text", multiline, rows = 2, required, testid, placeholder, disabled, helper, inputMode, maxLength }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    {multiline ? (
      <textarea data-testid={testid} value={value} onChange={(e) => onChange(e.target.value)} required={required}
                rows={rows} className="input-base w-full mt-1" placeholder={placeholder} disabled={disabled} />
    ) : (
      <input data-testid={testid} type={type} value={value} onChange={(e) => onChange(e.target.value)}
             required={required} className="input-base w-full mt-1 disabled:opacity-50"
             placeholder={placeholder} disabled={disabled}
             inputMode={inputMode} maxLength={maxLength} />
    )}
    {helper && <div className="text-[11px] text-zinc-500 mt-1 leading-snug">{helper}</div>}
  </div>
);

// Runde 22 (11.09.2026): optionales required (Zahlungsart ist Pflicht).
const SelectField = ({ label, value, onChange, options, testid, required }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    <select data-testid={testid} value={value} onChange={(e) => onChange(e.target.value)}
            required={required}
            className="input-base w-full mt-1 appearance-none">
      {options.map((o) => (
        <option key={o.value} value={o.value} className="bg-zinc-900">{o.label}</option>
      ))}
    </select>
  </div>
);

// Runde 22 (11.09.2026): Kästchen der Empfangsbestätigung — gestaltet wie
// der MwSt-Kasten, die ganze Zeile ist klickbar. Ein Zahlenfeld innerhalb
// der Zeile (Schlüsselanzahl) schaltet das Kästchen beim Hineinklicken
// nicht um (Browser-Regel für interaktive Elemente in einem label).
const CheckRow = ({ checked, onChange, testid, children }) => (
  <label className="flex flex-wrap items-center gap-2 rounded-lg border px-3 py-2.5 cursor-pointer text-sm"
         style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
    <input type="checkbox" checked={!!checked}
           onChange={(e) => onChange(e.target.checked)}
           data-testid={testid}
           className="h-4 w-4 shrink-0 accent-red-500 cursor-pointer" />
    {children}
  </label>
);
