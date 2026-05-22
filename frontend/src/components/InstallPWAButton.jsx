import { useEffect, useState } from "react";
import { Download, Share } from "lucide-react";

/**
 * Zeigt auf Android/Desktop-Chrome den "Zum Homebildschirm"-Prompt,
 * auf iOS (kein beforeinstallprompt) eine Anleitung via Share-Menü.
 * Versteckt sich automatisch, wenn die App bereits als PWA läuft.
 */
export default function InstallPWAButton({ compact = false }) {
  const [evt, setEvt] = useState(null);
  const [installed, setInstalled] = useState(false);
  const [showIOSHint, setShowIOSHint] = useState(false);

  useEffect(() => {
    const inStandalone =
      window.matchMedia?.("(display-mode: standalone)").matches ||
      window.navigator.standalone;
    if (inStandalone) { setInstalled(true); return; }

    const onPrompt = (e) => { e.preventDefault(); setEvt(e); };
    const onInstalled = () => setInstalled(true);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed) return null;

  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);

  const click = async () => {
    if (evt) {
      evt.prompt();
      await evt.userChoice;
      setEvt(null);
    } else if (isIOS) {
      setShowIOSHint((v) => !v);
    }
  };

  // Keinen Button zeigen, wenn weder installierbar noch iOS (z.B. Firefox).
  if (!evt && !isIOS) return null;

  if (compact) {
    return (
      <button
        onClick={click}
        data-testid="pwa-install-btn"
        className="flex items-center gap-1.5 text-xs text-zinc-300 hover:text-white px-3 py-2 rounded-sm bg-white/5"
      >
        <Download size={13} /> App installieren
      </button>
    );
  }

  return (
    <div>
      <button
        onClick={click}
        data-testid="pwa-install-btn"
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-sm text-sm font-semibold bg-white/5 hover:bg-white/10"
      >
        <Download size={14} /> Zum Homebildschirm hinzufügen
      </button>
      {showIOSHint && (
        <div className="mt-3 p-3 rounded-sm text-xs text-zinc-300 leading-relaxed"
             style={{ background: "rgba(255,255,255,0.04)" }}>
          Tippe unten in Safari auf <Share size={11} className="inline" /> Teilen →
          <b> „Zum Home-Bildschirm“</b>. Dann öffnet sich das Fahrer-Portal wie eine App.
        </div>
      )}
    </div>
  );
}
