import { useState } from "react";
import { api, errMsg } from "@/lib/api";
import { toast } from "sonner";
import { Search, ShieldAlert, ShieldCheck } from "lucide-react";

/**
 * Konto pruefen (14.09.2026, Ahmad: "Fahrer-Login mit 10004 gibt 401, die
 * B2B-Anmeldung geht nicht"): Der Super-Admin gibt eine Kontonummer oder
 * einen Kaeufer-Code ein und sieht, WELCHE Kontoart dahintersteckt, wo sie
 * sich anmeldet, ob das Konto aktiv ist, ein Passwort hat und ob eine
 * Anmeldesperre laeuft. Die Anmeldemasken selbst sagen bewusst nur
 * "Kontonummer oder Passwort falsch".
 */
const ART_TON = { fahrer: "#60a5fa", firma: "#34d399", sucher: "#a78bfa", kaeufer: "#fbbf24" };

export default function KontoPruefen() {
  const [kennung, setKennung] = useState("");
  const [busy, setBusy] = useState(false);
  const [erg, setErg] = useState(null);

  const pruefen = async (e) => {
    e?.preventDefault?.();
    const k = kennung.trim();
    if (!k) return;
    setBusy(true);
    try {
      const { data } = await api.get("/admin/konten/pruefen", { params: { kennung: k } });
      setErg(data);
    } catch (err) {
      toast.error(errMsg(err, "Prüfung fehlgeschlagen"));
    } finally {
      setBusy(false);
    }
  };

  const zeile = (label, wert, testid) => (
    <div className="flex items-baseline justify-between gap-3 py-1" data-testid={testid}>
      <span className="text-[12px] text-zinc-500">{label}</span>
      <span className="text-[13px] text-white text-right">{wert}</span>
    </div>
  );

  return (
    <div className="rounded-2xl p-4 mb-5" data-testid="konto-pruefen"
         style={{ background: "var(--bg-elevated)", border: "1px solid var(--wa-10)" }}>
      <div className="text-[13px] font-semibold text-white">Konto prüfen</div>
      <div className="text-[12px] text-zinc-500 mt-0.5">
        Kontonummer (z. B. 10023, 10023-2), Käufer-Code (z. B. 6FE7K2M) oder Fahrer-ID (z. B. FD-7K2M9QX4) eingeben — zeigt Kontoart,
        Anmeldeseite, Passwort und Anmeldesperre. Hilft, wenn jemand „Kontonummer oder Passwort falsch“ bekommt.
      </div>
      <form onSubmit={pruefen} className="mt-3 flex gap-2">
        <input value={kennung} onChange={(e) => setKennung(e.target.value)}
               placeholder="Kontonummer, Käufer-Code oder Fahrer-ID" data-testid="konto-pruefen-kennung"
               autoCapitalize="characters" spellCheck={false}
               className="h-10 px-3 rounded-xl text-[14px] flex-1 outline-none focus:ring-2 focus:ring-red-500/40"
               style={{ background: "var(--bg-input-solid)", color: "var(--text-primary)", border: "1px solid var(--wa-12)" }} />
        <button type="submit" disabled={busy || !kennung.trim()} data-testid="konto-pruefen-submit"
                className="h-10 px-4 rounded-xl text-[13px] font-medium text-white inline-flex items-center gap-1.5 disabled:opacity-50"
                style={{ background: "var(--accent-red)" }}>
          <Search size={14} /> Prüfen
        </button>
      </form>

      {erg && (
        <div className="mt-4 rounded-xl p-3" data-testid="konto-pruefen-ergebnis"
             style={{ background: "var(--bg-input-solid)", border: "1px solid var(--wa-10)" }}>
          {!erg.gefunden ? (
            <div className="flex items-start gap-2 text-[13px] text-amber-300">
              <ShieldAlert size={16} className="mt-0.5 shrink-0" />
              <div>
                <div className="font-semibold">Kein Konto zu „{erg.kennung}“</div>
                <div className="text-zinc-400 mt-1">{erg.hinweis}</div>
                {erg.fehlversuche > 0 && (
                  <div className="text-zinc-400 mt-1">
                    {erg.fehlversuche} Fehlversuch(e) im aktuellen Fenster{erg.anmeldesperre ? " — Anmeldesperre aktiv" : ""}.
                  </div>
                )}
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 text-[13px] font-semibold"
                   style={{ color: ART_TON[erg.art] || "var(--text-strong)" }}>
                {erg.aktiv && erg.passwort_gesetzt && !erg.anmeldesperre
                  ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
                <span data-testid="konto-pruefen-art">{erg.art_text}</span>
              </div>
              <div className="mt-2 divide-y divide-white/5">
                {zeile("Kennung", <span className="font-mono">{erg.kennung}</span>, "konto-pruefen-kennung-wert")}
                {zeile("Name", erg.name || "—")}
                {erg.firma && zeile("Firma", erg.firma)}
                {erg.driver_code && erg.driver_code !== erg.kennung && zeile("Fahrer-ID", <span className="font-mono">{erg.driver_code}</span>)}
                {zeile("Anmeldeseite", <span className="font-mono">{erg.anmeldeseite}</span>, "konto-pruefen-seite")}
                {zeile("Konto", erg.aktiv ? "aktiv" : "GESPERRT (deaktiviert)", "konto-pruefen-aktiv")}
                {zeile("Passwort", erg.passwort_gesetzt ? "gesetzt" : "FEHLT")}
                {zeile("Anmeldesperre",
                  erg.anmeldesperre ? `AKTIV (${erg.fehlversuche} Fehlversuche)`
                    : erg.fehlversuche > 0 ? `nein (${erg.fehlversuche} Fehlversuche in ${erg.fenster_minuten} Min.)` : "nein",
                  "konto-pruefen-sperre")}
                {zeile("Bekannte Geräte", String(erg.bekannte_geraete ?? 0))}
              </div>
              <ul className="mt-3 space-y-1 text-[12.5px] text-zinc-300 list-disc pl-4">
                {(erg.hinweise || []).map((h, i) => <li key={i}>{h}</li>)}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
