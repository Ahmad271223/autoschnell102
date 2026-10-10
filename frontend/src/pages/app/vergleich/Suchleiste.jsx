import { toast } from "sonner";
import { ArrowRight, ExternalLink, Eye, Loader2, Sparkles, X as XIcon } from "lucide-react";
import PortalBadge from "@/components/PortalBadge";
import { filterOeffnen } from "@/lib/filterOeffnen";
import { inseratsLinkAusText } from "@/lib/inseratsLink";
import { filterEintraege, VERGLEICH_LAEUFT_HINWEIS } from "./anzeige";

/**
 * Linkfeld, Auslesen/Abbrechen, Portal-Schalter und "Filter öffnen" der Vergleichsseite
 * (08.10.2026 aus Vergleich.jsx herausgezogen — nur Anzeige; die Abläufe bleiben in der Seite).
 */
export default function Suchleiste({
  url, setUrl, loading, laeuftRef, result, schalter,
  onSubmit, onAbbrechen, vielleichtStarten, ausZwischenablage,
}) {
  const {
    portalMobile, portalAutoscout, filterAuto, fensterDaneben,
    toggleMobile, toggleAutoscout, toggleFilterAuto, toggleFensterDaneben,
  } = schalter;
  return (
    <form onSubmit={onSubmit} className="mt-8">
      {/* Zeile 1: URL-Input + Buttons */}
      <div className="apple-surface !rounded-2xl !p-1.5 max-w-4xl flex items-stretch gap-1.5 flex-wrap">
        {/* URL-Input */}
        <div className="flex-1 min-w-0 flex items-center pl-4" style={{ minWidth: 200 }}>
          <Sparkles size={15} className="text-[var(--accent-red)] shrink-0 mr-2.5" />
          <input
            data-testid="vergleich-url-input"
            required
            value={url}
            onChange={(e) => {
              const vorher = url;
              setUrl(e.target.value);
              vielleichtStarten(e.target.value, vorher);
            }}
            onClick={ausZwischenablage}
            onPaste={(e) => {
              // Einfuegen genuegt: erkennt der Text einen gueltigen
              // Inserats-Link (Kleinanzeigen ODER mobile.de), startet das
              // Auslesen sofort — der Knopf bleibt fuers manuelle
              // Wiederholen. Nur echte Inserats-URLs, keine Suchseiten.
              const text = (e.clipboardData?.getData("text") || "").trim();
              // RP-409: aus "Schau mal: https://…" nur den Link übernehmen
              const link = inseratsLinkAusText(text);
              if (link && (loading || laeuftRef.current)) {
                // U-15: während eines Laufs nicht einfügen (das Ergebnis
                // gehörte sonst zum falschen Link), sondern hinweisen.
                e.preventDefault();
                toast.info(VERGLEICH_LAEUFT_HINWEIS, { id: "vergleich-laeuft" });
              } else if (link) {
                e.preventDefault();
                setUrl(link);
                vielleichtStarten(link);
              }
            }}
            placeholder="Ins Feld klicken — kopierter Link wird eingefügt (Kleinanzeigen, mobile.de, AutoScout24)"
            className="flex-1 bg-transparent py-3 text-base font-mono outline-none truncate"
            style={{ color: "var(--text-primary)" }}
            autoFocus
          />
          {url && (
            <button
              type="button"
              onClick={() => setUrl("")}
              data-testid="vergleich-url-clear"
              title="URL löschen"
              className="tipp mr-0.5 flex items-center justify-center rounded-md hover:bg-white/5 text-zinc-400 hover:text-white shrink-0"
            >
              <XIcon size={16} />
            </button>
          )}
        </div>

        {/* Trennlinie */}
        <div className="w-px self-stretch my-1" style={{ background: "var(--divider)" }} />

        {/* Auslesen */}
        <button
          data-testid="vergleich-start-btn"
          type="submit"
          disabled={loading}
          className="apple-btn apple-btn-primary !px-5 !py-2.5 disabled:opacity-60 disabled:cursor-not-allowed shrink-0"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
          <span>{loading ? "Lade…" : "Auslesen"}</span>
        </button>

        {/* Wunsch Ahmad 18.09.2026: Abbrechen, wenn es zu lange dauert. */}
        {loading && (
          <button
            type="button"
            onClick={onAbbrechen}
            data-testid="vergleich-abbrechen-btn"
            title="Abruf abbrechen"
            className="apple-btn apple-btn-secondary !px-3 !py-2.5 shrink-0"
          >
            <XIcon size={15} />
            <span className="hidden sm:inline">Abbrechen</span>
          </button>
        )}

        {/* Trennlinie */}
        <div className="w-px self-stretch my-1" style={{ background: "var(--divider)" }} />

        {/* Portal-Toggles — PortalBadge sorgt für konsistenten Look in allen Dialogen */}
        <button
          type="button"
          data-testid="toggle-mobile"
          onClick={() => toggleMobile(!portalMobile)}
          title={(portalMobile ? "mobile.de aktiv — klicken zum Deaktivieren" : "mobile.de aktivieren")
            + " (gilt auch für das Windows-Programm und die Browser-Erweiterung)"}
          aria-label="mobile.de ein-/ausschalten"
          aria-pressed={portalMobile}
          className="shrink-0 p-1.5 rounded-xl bg-transparent border-0 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-red)]"
        >
          <PortalBadge kind="mobile" active={portalMobile} size="md" />
        </button>

        <button
          type="button"
          data-testid="toggle-autoscout"
          onClick={() => toggleAutoscout(!portalAutoscout)}
          title={(portalAutoscout ? "AutoScout24 aktiv — klicken zum Deaktivieren" : "AutoScout24 aktivieren")
            + " (gilt auch für das Windows-Programm und die Browser-Erweiterung)"}
          aria-label="AutoScout24 ein-/ausschalten"
          aria-pressed={portalAutoscout}
          className="shrink-0 p-1.5 rounded-xl bg-transparent border-0 cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-red)]"
        >
          <PortalBadge kind="autoscout" active={portalAutoscout} size="md" />
        </button>

        {/* Filter öffnen */}
        <button
          type="button"
          data-testid="open-filter-btn"
          disabled={!result
            || filterEintraege(result, { mobile: portalMobile, autoscout: portalAutoscout }).length === 0}
          onClick={() => {
            // Runde 22: je Klick laesst der Browser nur EIN Fenster zu — den
            // Rest holt der Hinweis-Knopf nach (oder Pop-ups erlauben).
            filterOeffnen(filterEintraege(result, { mobile: portalMobile, autoscout: portalAutoscout }));
          }}
          className="shrink-0 apple-btn apple-btn-secondary !px-4 !py-2.5 disabled:opacity-40 disabled:cursor-not-allowed"
          title={result ? "Filter der aktiven Portale öffnen" : "Erst Vergleich auslesen"}
        >
          <Eye size={14} />
          <span>Filter öffnen</span>
          <ExternalLink size={11} />
        </button>
      </div>

      <div className="mt-3 text-xs flex flex-wrap gap-2 items-center" style={{ color: "var(--text-muted)" }}>
        {/* Runde 22 (11.09.2026): Filter nach dem Auslesen automatisch oeffnen */}
        <label
          className="inline-flex items-center gap-2 sm:ml-3 cursor-pointer select-none min-h-[40px]"
          title="Nach dem Auslesen die Filter der aktiven Portale (mobile.de / AutoScout24) automatisch öffnen"
        >
          <input
            type="checkbox"
            data-testid="toggle-filter-auto"
            checked={filterAuto}
            onChange={(e) => toggleFilterAuto(e.target.checked)}
            style={{ accentColor: "var(--accent-red)" }}
          />
          <span style={{ color: "var(--text-secondary)" }}>Filter nach dem Auslesen automatisch öffnen</span>
        </label>
        {/* 15.09.2026 (Wunsch Ahmad): daneben statt darueber — zweiter Bildschirm */}
        <label
          className="inline-flex items-center gap-2 sm:ml-3 cursor-pointer select-none min-h-[40px]"
          title="Filter-Fenster neben der App öffnen — auf dem zweiten Bildschirm, wenn der Browser es erlaubt"
        >
          <input
            type="checkbox"
            data-testid="toggle-fenster-daneben"
            checked={fensterDaneben}
            onChange={(e) => toggleFensterDaneben(e.target.checked)}
            style={{ accentColor: "var(--accent-red)" }}
          />
          <span style={{ color: "var(--text-secondary)" }}>Filter daneben öffnen (zweiter Bildschirm)</span>
        </label>
      </div>
    </form>
  );
}
