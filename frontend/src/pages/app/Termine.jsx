import { useEffect, useMemo, useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import {
  Calendar as CalIcon, FileText, Edit3, X, ChevronLeft, ChevronRight,
  Plus, MapPin, Phone, User as UserIcon, Trash2, Clock, Printer,
  ClipboardCheck, Download,
} from "lucide-react";
import {
  openContractPdf, printContractPdf,
  openPickupOrderPdf, printPickupOrderPdf, downloadPickupOrderPdf,
} from "@/lib/pdf";
import SnapshotCard from "@/components/SnapshotCard";
import PhotoGallery from "@/components/PhotoGallery";
import {
  startOfMonth, endOfMonth, startOfWeek, endOfWeek, eachDayOfInterval,
  format, isSameMonth, isSameDay, addMonths, parseISO, isValid as isValidDate,
} from "date-fns";
import { de } from "date-fns/locale";

const STATUSES = ["offen", "abgeholt", "nicht abgeholt", "verschoben", "erledigt"];

const STATUS_META = {
  "offen":           { dot: "#0a84ff", chipClass: "st-offen-bg",         text: "st-offen" },
  "abgeholt":        { dot: "#34c759", chipClass: "st-abgeholt-bg",      text: "st-abgeholt" },
  "nicht abgeholt":  { dot: "#ff3b30", chipClass: "st-nicht-abgeholt-bg",text: "st-nicht-abgeholt" },
  "verschoben":      { dot: "#ff9f0a", chipClass: "st-verschoben-bg",    text: "st-verschoben" },
  "erledigt":        { dot: "#8e8e93", chipClass: "st-erledigt-bg",      text: "st-erledigt" },
};

const safeParse = (s) => {
  if (!s) return null;
  try {
    const d = parseISO(s);
    return isValidDate(d) ? d : null;
  } catch { return null; }
};

export default function Termine() {
  const [items, setItems] = useState([]);
  const [drivers, setDrivers] = useState([]);
  const [view, setView] = useState("month");          // 'month' | 'list'
  const [cursor, setCursor] = useState(new Date());   // current month for month view
  const [selectedDay, setSelectedDay] = useState(new Date());
  const [filter, setFilter] = useState("");
  const [editing, setEditing] = useState(null);       // appt being edited
  const [creating, setCreating] = useState(false);

  const load = async () => {
    const { data } = await api.get("/appointments", { params: filter ? { status: filter } : {} });
    setItems(data);
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);
  useEffect(() => { api.get("/drivers").then((r) => setDrivers(r.data)); }, []);

  const save = async (a) => {
    try {
      if (a.id) {
        const { data } = await api.put(`/appointments/${a.id}`, a);
        toast.success("Termin gespeichert");
        if (data.pickup_date_changed && a.contract_id) {
          if (window.confirm("Abholdatum wurde geändert. Neue PDF mit aktualisiertem Abholdatum erstellen?")) {
            const c = await api.get(`/contracts/${a.contract_id}`);
            const newC = {
              ...c.data.contract_data,
              vehicle_id: c.data.vehicle_id,
              pickup_date: a.pickup_date,
              pickup_time: a.pickup_time,
            };
            // Backend creates a new contract AND auto-creates a fresh
            // appointment for it (see server.py auto-create block).
            // Therefore we must NOT manually POST another appointment
            // here, and we delete the original appointment we just
            // updated — otherwise the rescheduled date would show the
            // termin three times (old updated + auto-created + manual).
            await api.post("/contracts", newC);
            await api.delete(`/appointments/${a.id}`);
            toast.success("Neue PDF erstellt & verknüpft");
          }
        }
      } else {
        await api.post(`/appointments`, a);
        toast.success("Termin angelegt");
      }
      setEditing(null);
      setCreating(false);
      load();
    } catch (err) {
      toast.error(errMsg(err, "Fehler beim Speichern"));
    }
  };

  const remove = async (id) => {
    if (!window.confirm("Termin löschen?")) return;
    await api.delete(`/appointments/${id}`);
    toast.success("Gelöscht");
    setEditing(null);
    load();
  };

  // Group appointments by day (yyyy-MM-dd)
  const apptsByDay = useMemo(() => {
    const m = new Map();
    for (const a of items) {
      const key = a.pickup_date || "";
      if (!key) continue;
      if (!m.has(key)) m.set(key, []);
      m.get(key).push(a);
    }
    // sort each day by time
    for (const [k, arr] of m.entries()) {
      arr.sort((x, y) => (x.pickup_time || "").localeCompare(y.pickup_time || ""));
    }
    return m;
  }, [items]);

  const daysOfMonth = useMemo(() => {
    const start = startOfWeek(startOfMonth(cursor), { weekStartsOn: 1 });
    const end = endOfWeek(endOfMonth(cursor), { weekStartsOn: 1 });
    return eachDayOfInterval({ start, end });
  }, [cursor]);

  const selectedKey = format(selectedDay, "yyyy-MM-dd");
  const selectedAppts = apptsByDay.get(selectedKey) || [];

  const upcomingAppts = useMemo(() => {
    const todayKey = format(new Date(), "yyyy-MM-dd");
    return items
      .filter((a) => (a.pickup_date || "") >= todayKey)
      .slice(0, 8);
  }, [items]);

  return (
    <div className="p-3 sm:p-6 lg:p-10 max-w-[1480px] mx-auto" data-testid="termine-page">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <div className="overline">Terminplaner</div>
          <h1 className="font-display font-black text-3xl lg:text-4xl tracking-tighter mt-1">Abholtermine</h1>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <div className="apple-segment" role="tablist">
            <button onClick={() => setView("month")} data-testid="view-month"
                    className={`apple-segment-item ${view === "month" ? "active" : ""}`}>Monat</button>
            <button onClick={() => setView("list")} data-testid="view-list"
                    className={`apple-segment-item ${view === "list" ? "active" : ""}`}>Liste</button>
          </div>
          <button onClick={() => setCreating(true)} data-testid="new-appt-btn"
                  className="apple-btn apple-btn-primary">
            <Plus size={15} /> Neuer Termin
          </button>
        </div>
      </div>

      {/* Filter chips (always visible) */}
      <div className="flex flex-wrap gap-1.5 mb-6">
        {["", ...STATUSES].map((s) => (
          <button key={s || "all"} onClick={() => setFilter(s)}
                  data-testid={`filter-status-${s || "all"}`}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors border ${
                    filter === s
                      ? "bg-white/10 text-white border-white/15"
                      : "bg-white/[0.03] text-zinc-400 border-white/[0.06] hover:bg-white/[0.06] hover:text-white"
                  }`}>
            {s ? (
              <span className="inline-flex items-center gap-1.5">
                <span className="cal-dot" style={{ background: STATUS_META[s]?.dot }} />
                {s}
              </span>
            ) : "Alle"}
          </button>
        ))}
      </div>

      {view === "month" ? (
        <MonthView
          cursor={cursor} setCursor={setCursor}
          days={daysOfMonth}
          apptsByDay={apptsByDay}
          selectedDay={selectedDay} setSelectedDay={setSelectedDay}
          selectedAppts={selectedAppts}
          upcomingAppts={upcomingAppts}
          onEdit={setEditing}
        />
      ) : (
        <ListView items={items} onEdit={setEditing} />
      )}

      {/* Floating + button (mobile-friendly) */}
      <button onClick={() => setCreating(true)} className="apple-fab lg:hidden" aria-label="Neu">
        <Plus size={22} />
      </button>

      {(editing || creating) && (
        <EditDialog
          appt={editing || { pickup_date: format(selectedDay, "yyyy-MM-dd"), status: "offen", title: "" }}
          drivers={drivers}
          isNew={creating}
          onClose={() => { setEditing(null); setCreating(false); }}
          onSave={save}
          onDelete={editing ? () => remove(editing.id) : null}
        />
      )}
    </div>
  );
}

/* ───────────────────────── Month View ───────────────────────── */

function MonthView({ cursor, setCursor, days, apptsByDay, selectedDay, setSelectedDay, selectedAppts, upcomingAppts, onEdit }) {
  return (
    <div className="grid lg:grid-cols-[1fr_380px] gap-5">
      {/* Calendar */}
      <div className="apple-surface-gloss p-4 lg:p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="font-display font-bold text-2xl lg:text-3xl tracking-tight">
            {format(cursor, "LLLL yyyy", { locale: de })}
          </div>
          <div className="flex items-center gap-1">
            <button onClick={() => setCursor(addMonths(cursor, -1))} data-testid="cal-prev"
                    className="apple-btn apple-btn-ghost !px-2.5"><ChevronLeft size={16} /></button>
            <button onClick={() => { setCursor(new Date()); setSelectedDay(new Date()); }} data-testid="cal-today"
                    className="apple-btn apple-btn-secondary">Heute</button>
            <button onClick={() => setCursor(addMonths(cursor, 1))} data-testid="cal-next"
                    className="apple-btn apple-btn-ghost !px-2.5"><ChevronRight size={16} /></button>
          </div>
        </div>

        <div className="cal-grid">
          {["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"].map((d) => (
            <div key={d} className="cal-weekday">{d}</div>
          ))}
          {days.map((d) => {
            const key = format(d, "yyyy-MM-dd");
            const dayAppts = apptsByDay.get(key) || [];
            const muted = !isSameMonth(d, cursor);
            const isToday = isSameDay(d, new Date());
            const isSelected = isSameDay(d, selectedDay);
            const wd = d.getDay();
            const visible = dayAppts.slice(0, 3);
            const more = dayAppts.length - visible.length;
            return (
              <div key={key}
                   data-testid={`cal-day-${key}`}
                   onClick={() => setSelectedDay(d)}
                   className={`cal-day ${muted ? "muted" : ""} ${isToday ? "is-today" : ""} ${isSelected ? "selected" : ""} ${wd === 0 || wd === 6 ? "is-weekend" : ""}`}>
                <div className="flex items-center justify-between">
                  <span className="cal-daynum">{format(d, "d")}</span>
                  {dayAppts.length > 0 && !visible.length && (
                    <span className="cal-dot" style={{ background: STATUS_META[dayAppts[0].status]?.dot || "#0a84ff" }} />
                  )}
                </div>
                <div className="flex flex-col gap-[3px] overflow-hidden">
                  {visible.map((a) => {
                    const meta = STATUS_META[a.status] || STATUS_META.offen;
                    return (
                      <div key={a.id}
                           onClick={(e) => { e.stopPropagation(); onEdit(a); }}
                           className={`cal-event ${meta.chipClass} ${meta.text}`}
                           title={`${a.pickup_time || ""} ${a.title}`}>
                        {a.pickup_time ? <span className="opacity-70 mr-1">{a.pickup_time}</span> : null}
                        <span className="text-white/90">{a.title}</span>
                      </div>
                    );
                  })}
                  {more > 0 && <div className="cal-event-more">+{more} weitere</div>}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Side panel: selected day + upcoming */}
      <div className="space-y-5">
        <div className="apple-surface-gloss p-5" data-testid="day-panel">
          <div className="flex items-center justify-between mb-4">
            <div>
              <div className="overline">{isSameDay(selectedDay, new Date()) ? "Heute" : "Ausgewählt"}</div>
              <div className="font-display font-bold text-xl mt-0.5">
                {format(selectedDay, "EEEE, d. LLLL", { locale: de })}
              </div>
            </div>
            <span className="text-xs text-zinc-500">{selectedAppts.length} Termin{selectedAppts.length !== 1 ? "e" : ""}</span>
          </div>
          {selectedAppts.length === 0 ? (
            <div className="text-center text-zinc-500 py-8">
              <CalIcon className="mx-auto mb-2 opacity-40" size={28} />
              <div className="text-sm">Keine Termine an diesem Tag.</div>
            </div>
          ) : (
            <div className="space-y-2">
              {selectedAppts.map((a) => <DayApptItem key={a.id} a={a} onEdit={onEdit} />)}
            </div>
          )}
        </div>

        <div className="apple-surface-gloss p-5">
          <div className="overline mb-3">Bevorstehend</div>
          {upcomingAppts.length === 0 ? (
            <div className="text-sm text-zinc-500">Keine bevorstehenden Termine.</div>
          ) : (
            <div className="space-y-2">
              {upcomingAppts.map((a) => <DayApptItem key={a.id} a={a} onEdit={onEdit} compact />)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function DayApptItem({ a, onEdit, compact }) {
  const meta = STATUS_META[a.status] || STATUS_META.offen;
  const v = a.vehicle?.data;
  const date = safeParse(a.pickup_date);
  return (
    <button onClick={() => onEdit(a)} data-testid={`appt-row-${a.id}`}
            className="w-full text-left apple-card-gloss p-3 flex gap-3 items-start">
      <div className="flex flex-col items-center pt-0.5 min-w-[44px]">
        <div className="cal-dot mb-1" style={{ background: meta.dot, width: 8, height: 8 }} />
        <div className="text-[11px] font-mono font-semibold tabular-nums text-zinc-300">
          {a.pickup_time || "—:—"}
        </div>
        {compact && date && (
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mt-0.5">
            {format(date, "d. LLL", { locale: de })}
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm leading-snug truncate">{a.title}</div>
        {v && !compact && (
          <div className="text-xs text-zinc-500 mt-0.5 truncate">
            {v.first_registration} · {v.mileage?.toLocaleString("de-DE")} km · {v.power_ps} PS
          </div>
        )}
        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
          <span className={`text-[10px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded-md ${meta.chipClass} ${meta.text}`}>
            {a.status}
          </span>
          {a.driver?.name && (
            <span className="inline-flex items-center gap-1 text-[11px] text-zinc-400">
              <UserIcon size={10} /> {a.driver.name}
            </span>
          )}
          {a.contract_id && (
            <span className="inline-flex items-center gap-1 text-[11px]" style={{ color: "#34c759" }}>
              <FileText size={10} /> PDF
            </span>
          )}
        </div>
        {a.vehicle_id && !compact && (
          <div className="mt-2" onClick={(e) => e.stopPropagation()}>
            <SnapshotCard vehicleId={a.vehicle_id} compact />
          </div>
        )}
      </div>
    </button>
  );
}

/* ───────────────────────── List View ───────────────────────── */

function ListView({ items, onEdit }) {
  if (!items.length) {
    return (
      <div className="apple-surface-gloss p-12 text-center text-zinc-500">
        <CalIcon className="mx-auto mb-3 opacity-50" />
        Keine Termine. Erstelle einen Vertrag oder klicke „Neuer Termin“.
      </div>
    );
  }
  // group by date
  const groups = items.reduce((acc, a) => {
    const k = a.pickup_date || "ohne Datum";
    if (!acc[k]) acc[k] = [];
    acc[k].push(a);
    return acc;
  }, {});
  const keys = Object.keys(groups).sort();
  return (
    <div className="space-y-5">
      {keys.map((k) => {
        const date = safeParse(k);
        return (
          <div key={k}>
            <div className="flex items-center gap-3 mb-2">
              <div className="font-display font-bold text-base">
                {date ? format(date, "EEEE, d. LLLL yyyy", { locale: de }) : "ohne Datum"}
              </div>
              <div className="flex-1 h-px bg-white/[0.06]" />
              <div className="text-xs text-zinc-500">{groups[k].length} Termin{groups[k].length !== 1 ? "e" : ""}</div>
            </div>
            <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-3">
              {groups[k].sort((x, y) => (x.pickup_time || "").localeCompare(y.pickup_time || "")).map((a) => (
                <DayApptItem key={a.id} a={a} onEdit={onEdit} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ───────────────────────── Modal ───────────────────────── */

function EditDialog({ appt, drivers, isNew, onClose, onSave, onDelete }) {
  const [a, setA] = useState({ ...appt });
  const set = (k, v) => setA({ ...a, [k]: v });
  const [conflict, setConflict] = useState(null);

  // Warnung: schon eine Fahrt am selben Tag?
  useEffect(() => {
    setConflict(null);
    if (!a.driver_id || !a.pickup_date) return;
    let cancelled = false;
    api.get(`/drivers/${a.driver_id}/conflicts`, { params: { date: a.pickup_date } })
      .then((r) => {
        if (cancelled) return;
        const list = (r.data?.conflicts || []).filter((c) => c.id !== a.id);
        setConflict(list.length > 0 ? list : null);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [a.driver_id, a.pickup_date, a.id]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 apple-modal-backdrop"
         onClick={onClose}>
      <div className="apple-modal w-full max-w-xl max-h-[90vh] overflow-y-auto"
           onClick={(e) => e.stopPropagation()}
           data-testid="edit-appt-dialog">
        <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.08]">
          <div>
            <div className="overline">{isNew ? "Neu" : "Bearbeiten"}</div>
            <div className="font-display font-bold text-xl mt-0.5">
              {isNew ? "Neuer Termin" : "Termin bearbeiten"}
            </div>
          </div>
          <button onClick={onClose} className="apple-btn apple-btn-ghost !p-2">
            <X size={18} />
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* Title */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Titel</label>
            <input data-testid="edit-title" value={a.title || ""}
                   onChange={(e) => set("title", e.target.value)}
                   placeholder="z. B. Mercedes E 220d abholen"
                   className="apple-input" />
          </div>

          {/* Date + Time */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <CalIcon size={12} /> Datum
              </label>
              <input data-testid="edit-pickup-date" type="date" value={a.pickup_date || ""}
                     onChange={(e) => set("pickup_date", e.target.value)}
                     className="apple-input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <Clock size={12} /> Uhrzeit
              </label>
              <input data-testid="edit-pickup-time" type="time" value={a.pickup_time || ""}
                     onChange={(e) => set("pickup_time", e.target.value)}
                     className="apple-input" />
            </div>
          </div>

          {/* Status + Driver */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Status</label>
              <div className="grid grid-cols-3 gap-1.5">
                {STATUSES.map((s) => {
                  const meta = STATUS_META[s];
                  const active = (a.status || "offen") === s;
                  return (
                    <button key={s} onClick={() => set("status", s)} data-testid={`status-${s}`}
                            type="button"
                            className={`text-[11px] font-medium px-2 py-2 rounded-lg border transition-all flex items-center justify-center gap-1.5 ${
                              active ? `${meta.chipClass} ${meta.text} border-current/40` : "bg-white/[0.03] text-zinc-500 border-white/[0.06] hover:text-zinc-300"
                            }`}>
                      <span className="cal-dot" style={{ background: meta.dot }} />
                      {s}
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <UserIcon size={12} /> Fahrer
              </label>
              <select data-testid="edit-driver" value={a.driver_id || ""}
                      onChange={(e) => set("driver_id", e.target.value || null)}
                      className="apple-input">
                <option value="">— kein Fahrer —</option>
                {drivers.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </select>
              {conflict && (
                <div data-testid="driver-conflict-warning"
                     className="mt-2 p-2.5 rounded-sm text-xs leading-relaxed"
                     style={{ background: "rgba(255,149,0,0.12)",
                              border: "1px solid rgba(255,149,0,0.35)",
                              color: "#FFB020" }}>
                  ⚠️ Fahrer ist am {a.pickup_date} bereits einer Fahrt zugeordnet
                  ({conflict.length}×). Du kannst trotzdem zuweisen.
                </div>
              )}
            </div>
          </div>

          {/* Address + Contact */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
              <MapPin size={12} /> Abhol-Adresse
            </label>
            <input data-testid="edit-address" value={a.pickup_address || ""}
                   onChange={(e) => set("pickup_address", e.target.value)}
                   placeholder="Straße, PLZ Ort"
                   className="apple-input" />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Verkäufer</label>
              <input value={a.seller_name || ""}
                     onChange={(e) => set("seller_name", e.target.value)}
                     className="apple-input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider flex items-center gap-1.5">
                <Phone size={12} /> Telefon
              </label>
              <input value={a.seller_phone || ""}
                     onChange={(e) => set("seller_phone", e.target.value)}
                     className="apple-input" />
            </div>
          </div>

          {/* Pricing */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Endpreis (€)</label>
              <input data-testid="edit-final-price" type="number" value={a.final_price || ""}
                     onChange={(e) => set("final_price", Number(e.target.value) || null)}
                     className="apple-input" />
            </div>
            <div className="space-y-1.5">
              <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Sonstige Kosten (€)</label>
              <input data-testid="edit-extra-costs" type="number" value={a.extra_costs || ""}
                     onChange={(e) => set("extra_costs", Number(e.target.value) || null)}
                     className="apple-input" />
            </div>
          </div>

          {/* Notes */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">Notizen</label>
            <textarea data-testid="edit-notes" rows={3} value={a.notes || ""}
                      onChange={(e) => set("notes", e.target.value)}
                      placeholder="Zusatzinfos, Treffpunkt, Schlüsselübergabe …"
                      className="apple-input resize-none" />
          </div>

          {/* PDF link if connected — „Öffnen" behält altes Verhalten
              (neuer Tab), „Drucken" zeigt direkt den Druckdialog. */}
          {a.contract_id && (
            <div className="grid grid-cols-2 gap-2" data-testid="contract-actions">
              <button type="button"
                      onClick={() => openContractPdf(a.contract_id)}
                      data-testid="contract-open-btn"
                      className="apple-btn apple-btn-secondary">
                <FileText size={14} /> Kaufvertrag öffnen
              </button>
              <button type="button"
                      onClick={() => printContractPdf(a.contract_id)}
                      data-testid="contract-print-btn"
                      className="apple-btn apple-btn-secondary">
                <Printer size={14} /> Drucken
              </button>
            </div>
          )}

          {/* Abholauftrag / Übergabeprotokoll für den Fahrer.
              Steht für jeden Termin mit Fahrzeug zur Verfügung, auch ohne
              verknüpften Kaufvertrag — in dem Fall werden die Felder auf
              dem Protokoll einfach leergelassen und der Fahrer füllt sie
              vor Ort aus. Der erste Klick erzeugt das PDF on-demand,
              nachträgliche Aufrufe sind jederzeit möglich. */}
          {a.id && a.vehicle_id && (
            <div className="rounded-xl p-3"
                 style={{ background: "var(--hover-bg)", border: "1px solid var(--divider)" }}
                 data-testid="pickup-order-actions">
              <div className="flex items-center gap-2 mb-2">
                <ClipboardCheck size={14} className="text-[var(--accent-red)]" />
                <div className="text-[11px] font-bold uppercase tracking-wider"
                     style={{ color: "var(--text-primary)" }}>
                  Abholprotokoll (Fahrer)
                </div>
              </div>
              <div className="text-[10.5px] mb-2"
                   style={{ color: "var(--text-muted)" }}>
                Übergabeprotokoll mit Fahrzeugdaten-, Ausstattungs- &amp;
                Schadens-Check. Fahrer prüft alle Punkte vor Ort ab.
              </div>
              <div className="grid grid-cols-3 gap-2">
                <button type="button"
                        onClick={() => openPickupOrderPdf(a.id)}
                        data-testid="pickup-open-btn"
                        className="apple-btn apple-btn-primary !py-2 !text-[11px]">
                  <FileText size={12} /> Öffnen
                </button>
                <button type="button"
                        onClick={() => printPickupOrderPdf(a.id)}
                        data-testid="pickup-print-btn"
                        className="apple-btn apple-btn-secondary !py-2 !text-[11px]">
                  <Printer size={12} /> Drucken
                </button>
                <button type="button"
                        onClick={() => downloadPickupOrderPdf(
                          a.id,
                          `Abholauftrag_${(a.title || "Termin").replace(/[^\w\- ]+/g, "_")}.pdf`,
                        )}
                        data-testid="pickup-download-btn"
                        className="apple-btn apple-btn-secondary !py-2 !text-[11px]">
                  <Download size={12} /> Download
                </button>
              </div>
            </div>
          )}

          {a.vehicle_id && (
            <div className="mt-4">
              <SnapshotCard vehicleId={a.vehicle_id} />
            </div>
          )}

          {/* Fahrzeug-Fotos aus dem Inserat (soweit beim Vergleich mit
              gescraped). Händler sieht hier exakt das, was auch der Fahrer
              später in der Fahrer-App sieht. */}
          {(() => {
            const d = a.vehicle?.data || {};
            const photos = (
              d.image_urls || d.images || d.photos || d.pictures || []
            ).filter(Boolean);
            if (photos.length === 0) return null;
            return (
              <div className="mt-4 rounded-xl p-4"
                   style={{ background: "var(--hover-bg)", border: "1px solid var(--divider)" }}>
                <PhotoGallery photos={photos} label="Inserat-Fotos" />
              </div>
            );
          })()}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-white/[0.08] flex items-center justify-between gap-2 sticky bottom-0 bg-[rgba(20,20,22,0.98)] rounded-b-[18px]">
          <div>
            {onDelete && (
              <button onClick={onDelete} className="apple-btn apple-btn-danger" data-testid="delete-appt-btn">
                <Trash2 size={14} /> Löschen
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="apple-btn apple-btn-ghost">Abbrechen</button>
            <button onClick={() => onSave(a)} data-testid="save-appt-btn" className="apple-btn apple-btn-primary">
              {isNew ? "Anlegen" : "Speichern"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
