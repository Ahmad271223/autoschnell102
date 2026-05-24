import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import PortalSheet from "@/components/PortalSheet";
import {
  Search, Car, Calendar, Gauge, Zap, Fuel, Cog, Eye, ExternalLink,
  ChevronDown, X,
} from "lucide-react";

const FUELS = [
  "Benzin", "Diesel", "Elektro", "Hybrid", "Plug-in-Hybrid",
  "LPG / Autogas", "CNG / Erdgas",
];
const GEARBOXES = ["Automatik", "Manuell"];

export default function ManuelleSuche() {
  const [makes, setMakes] = useState([]);
  const [makeId, setMakeId] = useState(null);
  const [modelId, setModelId] = useState(null);
  const [makeSearch, setMakeSearch] = useState("");
  const [modelSearch, setModelSearch] = useState("");
  const [showMakes, setShowMakes] = useState(false);
  const [showModels, setShowModels] = useState(false);

  const [ezFrom, setEzFrom] = useState("");
  const [ezTo, setEzTo] = useState("");
  const [kmMin, setKmMin] = useState("");
  const [kmMax, setKmMax] = useState("");
  const [kw, setKw] = useState("");
  const [ps, setPs] = useState("");
  const [fuel, setFuel] = useState("");
  const [gearbox, setGearbox] = useState("");
  const [busy, setBusy] = useState(false);
  const [portalUrls, setPortalUrls] = useState(null); // { mobile, autoscout }

  // Marken laden — einmal beim Mount
  useEffect(() => {
    api.get("/manual/makes")
      .then(({ data }) => setMakes(data))
      .catch((e) => toast.error(errMsg(e, "Marken konnten nicht geladen werden")));
  }, []);

  const selectedMake = useMemo(
    () => makes.find((m) => m.id === makeId) || null,
    [makes, makeId],
  );
  const selectedModel = useMemo(
    () => (selectedMake?.models || []).find((mm) => mm.id === modelId) || null,
    [selectedMake, modelId],
  );

  const filteredMakes = useMemo(() => {
    const q = makeSearch.trim().toLowerCase();
    if (!q) return makes;
    return makes.filter((m) => m.name.toLowerCase().includes(q));
  }, [makes, makeSearch]);

  const filteredModels = useMemo(() => {
    const models = selectedMake?.models || [];
    const q = modelSearch.trim().toLowerCase();
    if (!q) return models;
    return models.filter((m) => m.name.toLowerCase().includes(q));
  }, [selectedMake, modelSearch]);

  // Wenn der User KW eingibt, schätzen wir PS und umgekehrt
  const onKwChange = (v) => {
    setKw(v);
    if (v && !isNaN(Number(v))) {
      setPs(String(Math.round(Number(v) * 1.359621617)));
    } else if (!v) setPs("");
  };
  const onPsChange = (v) => {
    setPs(v);
    if (v && !isNaN(Number(v))) {
      setKw(String(Math.round(Number(v) / 1.359621617)));
    } else if (!v) setKw("");
  };

  const submit = async () => {
    if (!selectedMake) {
      toast.error("Bitte zuerst eine Marke auswählen");
      return;
    }
    setBusy(true);
    try {
      const { data } = await api.post("/manual/search", {
        make: selectedMake.name,
        model: selectedModel?.name || null,
        ez_from: ezFrom ? parseInt(ezFrom, 10) : null,
        ez_to: ezTo ? parseInt(ezTo, 10) : null,
        km_min: kmMin ? parseInt(kmMin, 10) : null,
        km_max: kmMax ? parseInt(kmMax, 10) : null,
        kw: kw ? parseInt(kw, 10) : null,
        ps: ps ? parseInt(ps, 10) : null,
        fuel: fuel || null,
        gearbox: gearbox || null,
      });
      // Dialog anzeigen — User wählt welches Portal er öffnen will
      setPortalUrls({ mobile: data.mobile_url, autoscout: data.autoscout_url });
    } catch (e) {
      toast.error(errMsg(e, "Suche fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setMakeId(null); setModelId(null); setMakeSearch(""); setModelSearch("");
    setEzFrom(""); setEzTo(""); setKmMin(""); setKmMax("");
    setKw(""); setPs(""); setFuel(""); setGearbox("");
  };

  const years = useMemo(() => {
    const list = [];
    const current = new Date().getFullYear();
    for (let y = current; y >= 1960; y--) list.push(y);
    return list;
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <div className="overline">MANUELLE SUCHE · MOBILE.DE & AUTOSCOUT24</div>
        <h1 className="font-display font-black text-4xl tracking-tighter leading-none mt-2">
          Marke + Modell wählen.{" "}
          <span style={{ color: "var(--accent-red)" }}>Beide Filter öffnen.</span>
        </h1>
        <p className="text-sm mt-2 max-w-2xl" style={{ color: "var(--text-secondary)" }}>
          Wähle Marke, Modell und Filter aus — wir öffnen mobile.de und AutoScout24
          gleichzeitig in zwei Tabs mit fertigem Filter.
        </p>
      </div>

      {/* Marke + Modell */}
      <div className="grid md:grid-cols-2 gap-4">
        <PickerCard
          icon={<Car size={14} />}
          label="MARKE"
          value={selectedMake?.name || ""}
          placeholder="Marke wählen…"
          isOpen={showMakes}
          onOpen={() => setShowMakes(true)}
          onClose={() => { setShowMakes(false); setMakeSearch(""); }}
          searchValue={makeSearch}
          onSearch={setMakeSearch}
          searchPlaceholder="Marke suchen…"
          items={filteredMakes}
          onPick={(m) => { setMakeId(m.id); setModelId(null); setShowMakes(false); setMakeSearch(""); }}
          testid="manual-make"
        />
        <PickerCard
          icon={<Car size={14} />}
          label="MODELL"
          value={selectedModel?.name || ""}
          placeholder={selectedMake ? "Modell wählen…" : "Erst Marke wählen"}
          isOpen={showModels}
          onOpen={() => selectedMake && setShowModels(true)}
          onClose={() => { setShowModels(false); setModelSearch(""); }}
          searchValue={modelSearch}
          onSearch={setModelSearch}
          searchPlaceholder="Modell suchen…"
          items={filteredModels}
          onPick={(m) => { setModelId(m.id); setShowModels(false); setModelSearch(""); }}
          disabled={!selectedMake}
          testid="manual-model"
          extraItemHint={(m) => m.name}
        />
      </div>

      {/* Filter-Block */}
      <div className="apple-card p-6">
        <div className="overline mb-4 flex items-center gap-2">
          <Search size={11} /> FILTER
        </div>

        {/* Erstzulassung */}
        <FieldGroup icon={<Calendar size={14} />} label="Erstzulassung">
          <div className="grid grid-cols-2 gap-2">
            <YearSelect testid="manual-ez-from" value={ezFrom} onChange={setEzFrom} years={years} placeholder="von" />
            <YearSelect testid="manual-ez-to"   value={ezTo}   onChange={setEzTo}   years={years} placeholder="bis" />
          </div>
        </FieldGroup>

        {/* KM */}
        <FieldGroup icon={<Gauge size={14} />} label="Kilometerstand">
          <div className="grid grid-cols-2 gap-2">
            <NumInput testid="manual-km-min" value={kmMin} onChange={setKmMin} placeholder="von km" suffix="km" />
            <NumInput testid="manual-km-max" value={kmMax} onChange={setKmMax} placeholder="bis km" suffix="km" />
          </div>
        </FieldGroup>

        {/* Leistung */}
        <FieldGroup icon={<Zap size={14} />} label="Leistung">
          <div className="grid grid-cols-2 gap-2">
            <NumInput testid="manual-kw" value={kw} onChange={onKwChange} placeholder="kW" suffix="kW" />
            <NumInput testid="manual-ps" value={ps} onChange={onPsChange} placeholder="PS" suffix="PS" />
          </div>
          <p className="text-[11px] mt-1" style={{ color: "var(--text-muted)" }}>
            Wert in einem Feld → das andere wird automatisch ausgefüllt.
          </p>
        </FieldGroup>

        {/* Kraftstoff */}
        <FieldGroup icon={<Fuel size={14} />} label="Kraftstoff">
          <ChipRow value={fuel} onChange={setFuel} options={FUELS} testidPrefix="manual-fuel" />
        </FieldGroup>

        {/* Getriebe */}
        <FieldGroup icon={<Cog size={14} />} label="Getriebe">
          <ChipRow value={gearbox} onChange={setGearbox} options={GEARBOXES} testidPrefix="manual-gear" />
        </FieldGroup>

        {/* CTA */}
        {/* Portal-Auswahl-Dialog */}
        {portalUrls && (
          <PortalSheet
            mobileUrl={portalUrls.mobile}
            autoscoutUrl={portalUrls.autoscout}
            onClose={() => setPortalUrls(null)}
          />
        )}
        <div className="flex flex-wrap items-center gap-3 pt-4 mt-2"
             style={{ borderTop: "1px solid var(--hairline)" }}>
          <button
            data-testid="manual-submit"
            type="button"
            onClick={submit}
            disabled={busy || !selectedMake}
            className="apple-btn apple-btn-primary !px-6"
          >
            <Eye size={15} />
            {busy ? "Öffne…" : "mobile.de & AutoScout24 öffnen"}
            <ExternalLink size={12} />
          </button>
          <button
            data-testid="manual-reset"
            type="button"
            onClick={reset}
            className="apple-btn apple-btn-secondary"
          >
            <X size={14} /> Zurücksetzen
          </button>
          {!selectedMake && (
            <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
              Marke ist Pflicht — alle anderen Felder sind optional.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/* ───────── Sub-Components ───────── */

function FieldGroup({ icon, label, children }) {
  return (
    <div className="py-3">
      <div className="overline mb-1.5 flex items-center gap-1.5">
        {icon}{label}
      </div>
      {children}
    </div>
  );
}

function NumInput({ value, onChange, placeholder, suffix, testid }) {
  return (
    <div className="relative">
      <input
        data-testid={testid}
        inputMode="numeric"
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, ""))}
        placeholder={placeholder}
        className="w-full h-11 px-4 pr-10 rounded-xl outline-none text-[14px]"
        style={{
          background: "var(--input-bg)",
          border: "1px solid var(--divider)",
          color: "var(--text-primary)",
        }}
      />
      {suffix && (
        <span className="absolute right-3 top-1/2 -translate-y-1/2 text-[11px] font-medium tracking-wide"
              style={{ color: "var(--text-muted)" }}>{suffix}</span>
      )}
    </div>
  );
}

function YearSelect({ value, onChange, years, placeholder, testid }) {
  // <option> erbt von <select> nur sehr eingeschraenkt — explizit setzen
  // sonst rendert das OS sie auf weissem Hintergrund (Dark-Mode unlesbar).
  const optStyle = {
    background: "var(--bg-surface)",
    color: "var(--text-primary)",
  };
  const placeholderOptStyle = {
    background: "var(--bg-surface)",
    color: "var(--text-muted)",
  };
  return (
    <div className="relative">
      <select
        data-testid={testid}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full h-11 px-4 pr-9 rounded-xl outline-none text-[14px] appearance-none"
        style={{
          background: "var(--input-bg)",
          border: "1px solid var(--divider)",
          color: value ? "var(--text-primary)" : "var(--text-muted)",
        }}
      >
        <option value="" style={placeholderOptStyle}>{placeholder}</option>
        {years.map((y) => (
          <option key={y} value={y} style={optStyle}>{y}</option>
        ))}
      </select>
      <ChevronDown size={14} className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none"
                   style={{ color: "var(--text-muted)" }} />
    </div>
  );
}

function ChipRow({ value, onChange, options, testidPrefix }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const active = value === opt;
        return (
          <button
            key={opt}
            type="button"
            data-testid={`${testidPrefix}-${opt.toLowerCase().replace(/[^a-z]/g, "-")}`}
            onClick={() => onChange(active ? "" : opt)}
            className="px-3 py-1.5 rounded-lg text-[12.5px] font-medium transition-all"
            style={active ? {
              background: "var(--accent-red)", color: "white",
              border: "1px solid var(--accent-red)",
            } : {
              background: "var(--hover-bg)", color: "var(--text-secondary)",
              border: "1px solid var(--divider)",
            }}
          >
            {opt}
          </button>
        );
      })}
    </div>
  );
}

function PickerCard({
  icon, label, value, placeholder, isOpen, onOpen, onClose,
  searchValue, onSearch, searchPlaceholder, items, onPick, disabled,
  testid, extraItemHint,
}) {
  return (
    <div className="apple-card p-4 relative">
      <div className="overline mb-2 flex items-center gap-1.5">
        {icon}{label}
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={onOpen}
        data-testid={`${testid}-trigger`}
        className="w-full h-12 px-4 rounded-xl text-left flex items-center justify-between text-[14px] transition-all"
        style={{
          background: "var(--input-bg)",
          border: "1px solid var(--divider)",
          color: value ? "var(--text-primary)" : "var(--text-muted)",
          opacity: disabled ? 0.5 : 1,
          cursor: disabled ? "not-allowed" : "pointer",
        }}
      >
        <span className={value ? "font-semibold" : ""}>{value || placeholder}</span>
        <ChevronDown size={16} style={{ color: "var(--text-muted)" }} />
      </button>

      {isOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={onClose} />
          <div
            className="absolute left-4 right-4 z-50 mt-2 rounded-xl shadow-2xl overflow-hidden"
            style={{
              background: "var(--card-bg)",
              border: "1px solid var(--divider)",
              maxHeight: 380,
            }}
            data-testid={`${testid}-dropdown`}
          >
            <div className="p-2.5 flex items-center gap-2"
                 style={{ borderBottom: "1px solid var(--hairline)" }}>
              <Search size={14} style={{ color: "var(--text-muted)" }} />
              <input
                autoFocus
                data-testid={`${testid}-search`}
                value={searchValue}
                onChange={(e) => onSearch(e.target.value)}
                placeholder={searchPlaceholder}
                className="flex-1 bg-transparent border-0 outline-none text-[14px]"
                style={{ color: "var(--text-primary)" }}
              />
            </div>
            <ul className="overflow-y-auto" style={{ maxHeight: 320 }}>
              {items.length === 0 ? (
                <li className="px-4 py-6 text-center text-[13px]" style={{ color: "var(--text-muted)" }}>
                  Keine Treffer
                </li>
              ) : (
                items.map((it) => (
                  <li key={it.id}>
                    <button
                      type="button"
                      onClick={() => onPick(it)}
                      data-testid={`${testid}-item-${it.id}`}
                      className="w-full text-left px-4 py-2.5 text-[14px] transition-colors"
                      style={{ color: "var(--text-primary)" }}
                      onMouseEnter={(e) => e.currentTarget.style.background = "var(--hover-bg)"}
                      onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}
                    >
                      {extraItemHint ? extraItemHint(it) : it.name}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
