import { useState } from "react";
import { Download, Share } from "lucide-react";
import { toast } from "sonner";
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { installieren, useInstallation } from "@/lib/installation";

/*
 * "Als App installieren": AutoSchnell als Symbol auf Taskleiste, Dock oder
 * Startbildschirm — ein Klick oeffnet die App, ohne Adresse eintippen
 * (Wege je Browser: lib/installation.js, Einstieg: lib/appstart.js).
 *   variante="voll"     breiter Knopf mit Erklaerung (Anmeldeseiten)
 *   variante="kompakt"  kleiner Textknopf (Kopfzeile der Fahrer-App)
 *   variante="symbol"   nur Symbol (Seitenleiste der Firmen-App)
 * Laeuft die Seite schon als App, ist sie hier schon installiert oder bietet
 * der Browser keinen Weg, erscheint nichts.
 */

const DANACH = "Danach öffnest du AutoSchnell mit einem Klick auf das Symbol — ohne Adresse "
  + "eintippen. Bist du noch angemeldet, geht es direkt weiter.";
// iPhone/iPad und Safari am Mac: die installierte App hat einen eigenen
// Speicher (von Apple so gewollt) — dort meldet man sich einmal neu an.
const DANACH_APPLE = "Beim ersten Öffnen meldest du dich in der App einmal an — danach startet "
  + "sie direkt. Die Anmeldung im Browser endet dabei (jedes Konto hat nur eine Sitzung).";
const DOCK = "Damit das Symbol im Dock bleibt: Rechtsklick auf das AutoSchnell-Symbol im Dock "
  + "→ „Optionen“ → „Im Dock behalten“.";
const TASKLEISTE = "Zum Anheften: Rechtsklick auf das AutoSchnell-Symbol in der Taskleiste "
  + "→ „An Taskleiste anheften“.";

function anleitung({ plattform, edge, mac }) {
  if (plattform === "ios") {
    return {
      titel: "Auf den Home-Bildschirm legen",
      schritte: [
        <>In Safari rechts neben der Adresszeile auf „…“ tippen, dann auf „Teilen“ <Share size={13} className="inline -mt-0.5" />.
          (Ältere iPhones, iPad und Chrome: das Teilen-Symbol steht direkt in der Leiste.)</>,
        <>Nach unten wischen und „Zum Home-Bildschirm“ wählen.</>,
        <>„Als Web-App öffnen“ eingeschaltet lassen und oben rechts auf „Hinzufügen“ tippen.</>,
      ],
      danach: DANACH_APPLE,
    };
  }
  if (plattform === "mac-safari") {
    return {
      titel: "Ins Dock legen",
      schritte: [
        <>Oben in der Menüleiste auf „Ablage“ klicken.</>,
        <>„Zum Dock hinzufügen …“ wählen (ab macOS 14 Sonoma).</>,
        <>Mit „Hinzufügen“ bestätigen.</>,
      ],
      danach: DANACH_APPLE,
    };
  }
  if (plattform === "android" || plattform === "android-firefox") {
    return {
      titel: "Auf den Startbildschirm legen",
      schritte: [
        <>Oben rechts auf das Menü ⋮ tippen.</>,
        <>„App installieren“ oder „Zum Startbildschirm hinzufügen“ wählen.</>,
        <>Mit „Installieren“ bzw. „Hinzufügen“ bestätigen.</>,
      ],
      danach: DANACH,
    };
  }
  const windows = plattform === "windows";
  const zusatz = windows ? [<>{TASKLEISTE}</>] : mac ? [<>{DOCK}</>] : [];
  if (edge) {
    return {
      titel: "Als App installieren",
      schritte: [
        <>Oben in der Adresszeile auf das Symbol „App verfügbar“ klicken.</>,
        <>Kein Symbol zu sehen? Menü … oben rechts → „Apps“ → „Diese Website als App installieren“.</>,
        windows
          ? <>Mit „Installieren“ bestätigen und im nächsten Fenster „An Taskleiste anheften“ ankreuzen.</>
          : <>Mit „Installieren“ bestätigen.</>,
        ...(windows ? [] : zusatz),
      ],
      danach: DANACH,
    };
  }
  return {
    titel: "Als App installieren",
    schritte: [
      <>Oben in der Adresszeile ganz rechts auf das Installieren-Symbol klicken (kleiner Bildschirm mit Pfeil).</>,
      <>Kein Symbol zu sehen? Menü ⋮ oben rechts → „Streamen, speichern und teilen“ → „Seite als App installieren …“
        (bzw. „AutoSchnell installieren …“).</>,
      <>Mit „Installieren“ bestätigen.</>,
      ...zusatz,
    ],
    danach: DANACH,
  };
}

export default function InstallPWAButton({ variante = "voll" }) {
  const stand = useInstallation();
  const { art, plattform } = stand;
  const [offen, setOffen] = useState(false);
  if (!art) return null;

  const klick = async () => {
    if (art === "direkt") {
      const ergebnis = await installieren();
      if (ergebnis === "angenommen") {
        toast.success("AutoSchnell ist installiert", {
          description: plattform === "windows" ? `Tipp — ${TASKLEISTE}`
            : stand.mac ? `Tipp — ${DOCK}`
              : "Ab jetzt öffnet sich AutoSchnell mit einem Klick auf das Symbol.",
          duration: 10000,
        });
      }
      if (ergebnis !== null) return;
      // null: der Browser hat den Dialog verweigert -> Anleitung zeigen
    }
    setOffen(true);
  };

  const a = anleitung(stand);
  // Farben ueber die Design-Variablen: der Dialog liegt im <body> (Portal)
  // und muss im hellen wie im dunklen Design lesbar sein.
  const dialog = (
    <Dialog open={offen} onOpenChange={setOffen}>
      <DialogContent className="max-w-md" data-testid="pwa-anleitung"
                     style={{ background: "var(--bg-surface)", color: "var(--text-primary)", borderColor: "var(--border-default)" }}>
        <DialogHeader>
          <DialogTitle>{a.titel}</DialogTitle>
          <DialogDescription style={{ color: "var(--text-secondary)" }}>
            So kommt AutoSchnell als Symbol auf dein Gerät:
          </DialogDescription>
        </DialogHeader>
        <ol className="space-y-3 text-sm">
          {a.schritte.map((schritt, i) => (
            <li key={i} className="flex gap-3">
              <span className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white"
                    style={{ background: "var(--accent-red)" }}>
                {i + 1}
              </span>
              <span className="pt-0.5 leading-relaxed">{schritt}</span>
            </li>
          ))}
        </ol>
        <p className="text-xs leading-relaxed" style={{ color: "var(--text-secondary)" }}>{a.danach}</p>
      </DialogContent>
    </Dialog>
  );

  if (variante === "symbol") {
    return (
      <>
        <button type="button" onClick={klick} data-testid="pwa-install-btn"
                className="p-2 rounded-md hover:bg-white/5" style={{ color: "var(--text-secondary)" }}
                title="Als App installieren" aria-label="Als App installieren">
          <Download size={16} />
        </button>
        {dialog}
      </>
    );
  }

  if (variante === "kompakt") {
    return (
      <>
        <button type="button" onClick={klick} data-testid="pwa-install-btn"
                className="flex items-center gap-1.5 text-xs text-zinc-300 hover:text-white px-3 py-2 rounded-sm bg-white/5">
          <Download size={13} /> App installieren
        </button>
        {dialog}
      </>
    );
  }

  const mobil = plattform === "ios" || plattform.startsWith("android");
  return (
    <div>
      <button type="button" onClick={klick} data-testid="pwa-install-btn"
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm text-sm font-semibold border hover:bg-white/5"
              style={{ borderColor: "var(--border-default)", color: "var(--text-primary)" }}>
        <Download size={14} /> Als App installieren
      </button>
      <p className="mt-2 text-xs text-center leading-relaxed" style={{ color: "var(--text-secondary)" }}>
        {mobil ? "Symbol auf dem Startbildschirm" : "Symbol auf Taskleiste oder Dock"} —
        AutoSchnell öffnet sich mit einem Klick, ohne Adresse eintippen.
      </p>
      {dialog}
    </div>
  );
}
