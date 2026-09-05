import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { useAuth } from "@/context/AuthContext";
import { X, Eye, FileText, Loader2 } from "lucide-react";
import DamageSelector, { damagesToText } from "./DamageSelector";

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

export default function ContractDialog({ open, onClose, vehicle, vehicleId, onCreated }) {
  const { dealer } = useAuth();
  const v = vehicle || {};
  const [form, setForm] = useState({
    seller_name: v.seller_name || "",
    seller_address: v.seller_address || "",
    seller_zip: v.seller_zip || "",
    seller_city: v.seller_city || "",
    seller_phone: v.seller_phone || "",
    seller_email: v.seller_email || "",
    purchase_price: "",
    payment_method: "Bar / Überweisung",
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
    dealer_company: dealer?.company_name || "",
    dealer_contact: dealer?.contact_person || "",
    dealer_phone: dealer?.phone || "",
    dealer_whatsapp: dealer?.whatsapp_number || dealer?.phone || "",
    dealer_email: dealer?.email || "",
    dealer_address: dealer?.address || "",
    dealer_zip: dealer?.zip_code || "",
    dealer_city: dealer?.city || "",
  });
  const [loading, setLoading] = useState(false);
  const [previewing, setPreviewing] = useState(false);

  if (!open) return null;

  const set = (k, v) => setForm({ ...form, [k]: v });

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
    if (!form.purchase_price || Number(form.purchase_price) <= 0) {
      toast.error("Bitte Kaufpreis manuell eingeben");
      return;
    }
    setLoading(true);
    try {
      const { data } = await api.post("/contracts", buildPayload());
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

            <Section title="Käufer (Händler — du)">
              <Field label="Firma" value={form.dealer_company} onChange={(v) => set("dealer_company", v)} testid="contract-dealer-company" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="Ansprechpartner" value={form.dealer_contact} onChange={(v) => set("dealer_contact", v)} testid="contract-dealer-contact" />
                <Field label="Telefon" value={form.dealer_phone} onChange={(v) => set("dealer_phone", v)} testid="contract-dealer-phone" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <Field label="WhatsApp" value={form.dealer_whatsapp} onChange={(v) => set("dealer_whatsapp", v)} testid="contract-dealer-wa" />
                <Field label="E-Mail" type="email" value={form.dealer_email} onChange={(v) => set("dealer_email", v)} testid="contract-dealer-email" />
              </div>
              <Field label="Adresse" value={form.dealer_address} onChange={(v) => set("dealer_address", v)} testid="contract-dealer-address" />
              <div className="grid grid-cols-2 gap-3">
                <Field label="PLZ" value={form.dealer_zip} onChange={(v) => set("dealer_zip", v)} testid="contract-dealer-zip" />
                <Field label="Ort" value={form.dealer_city} onChange={(v) => set("dealer_city", v)} testid="contract-dealer-city" />
              </div>
              <div className="text-[11px] text-zinc-500 leading-relaxed">
                Aus deinem Profil vorbefüllt — Änderungen hier gelten nur für diesen Vertrag.
                Dauerhaft anpassen unter <strong>Einstellungen</strong>.
              </div>
            </Section>
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
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
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
              <Field label="Zahlungsart" value={form.payment_method} onChange={(v) => set("payment_method", v)} testid="contract-payment" />
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

const SelectField = ({ label, value, onChange, options, testid }) => (
  <div>
    <label className="text-xs text-zinc-400">{label}</label>
    <select data-testid={testid} value={value} onChange={(e) => onChange(e.target.value)}
            className="input-base w-full mt-1 appearance-none">
      {options.map((o) => (
        <option key={o.value} value={o.value} className="bg-zinc-900">{o.label}</option>
      ))}
    </select>
  </div>
);
