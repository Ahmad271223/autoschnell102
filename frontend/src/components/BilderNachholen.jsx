import { useState } from "react";
import { toast } from "sonner";
import { Image as ImageIcon, Loader2, RefreshCw } from "lucide-react";
import { api, errMsg } from "@/lib/api";
import { postWithRetry503 } from "@/lib/linkCheck";

// Wunsch Ahmad 03.10.2026: Kamen beim Auslesen eines Inserats nur die Daten,
// aber keine Fotos, holt dieser Knopf das Inserat noch einmal komplett beim
// Anbieter. Der Server erlaubt das nur, wenn der erste Abruf geklappt hat und
// dabei keine Fotos kamen (je Inserat höchstens drei Versuche, einer je Minute).
export default function BilderNachholen({ url, onBilder }) {
  const [laeuft, setLaeuft] = useState(false);
  const [hinweis, setHinweis] = useState("");
  const [gesperrt, setGesperrt] = useState(false);

  async function nachholen() {
    if (laeuft || gesperrt || !url) return;
    setLaeuft(true);
    setHinweis("");
    try {
      // 503 (Inserat wird gerade abgerufen) wiederholt der Helfer selbst.
      const { data } = await postWithRetry503(api, "/mobile/bilder-nachholen", { url });
      if (data?.bilder > 0) {
        toast.success(data.bilder === 1 ? "1 Foto nachgeholt." : `${data.bilder} Fotos nachgeholt.`);
        onBilder?.(data);
      } else if (data?.laeuft) {
        // Befund Ahmad 10.10.2026: der Abruf läuft im Hintergrund weiter (Frist des Servers) — kein Fehler,
        // der Knopf bleibt; der nächste Klick findet die Fotos im Speicher
        setHinweis(data.hinweis || "Der Abruf läuft noch — gleich noch einmal „Bilder nachholen“ drücken.");
      } else {
        setHinweis(data?.hinweis || "Auch beim neuen Abruf kamen keine Fotos mit.");
        if (!data?.versuche_uebrig) setGesperrt(true);
      }
    } catch (err) {
      // Befund Ahmad 10.10.2026: Cloudflare/Proxy-Abbruch (502/504/524, englischer HTML-Text) -> deutscher Hinweis
      const status = err?.response?.status;
      const roh = typeof err?.response?.data === "string" ? err.response.data : "";
      const gateway = [502, 504, 524].includes(status) || /cloudflare|origin web server/i.test(roh);
      setHinweis(gateway
        ? "Der Abruf dauert gerade zu lange — bitte in einer Minute noch einmal „Bilder nachholen“ drücken."
        : errMsg(err, "Die Fotos konnten nicht nachgeholt werden"));
      // 409: nicht (mehr) möglich — z. B. dreimal ohne Fotos, Link neu auslesen
      if (status === 409) setGesperrt(true);
    } finally {
      setLaeuft(false);
    }
  }

  return (
    <div className="mt-6 pt-5 border-t" style={{ borderColor: "var(--hairline)" }}
         data-testid="bilder-nachholen">
      <div className="overline mb-2 flex items-center gap-1.5">
        <ImageIcon size={11} /> Fotos vom Inserat
      </div>
      <p className="text-sm mb-3" style={{ color: "var(--text-secondary)" }}>
        Beim Auslesen kamen die Daten, aber keine Fotos mit. „Bilder nachholen“ ruft
        das Inserat noch einmal komplett ab.
      </p>
      {hinweis && (
        <div className="text-sm rounded-xl p-3 mb-3" data-testid="bilder-nachholen-hinweis"
             style={{ background: "#f59e0b1c", color: "var(--tx-amber)" }}>
          {hinweis}
        </div>
      )}
      {!gesperrt && (
        <button type="button" data-testid="bilder-nachholen-btn" disabled={laeuft}
                onClick={nachholen}
                className="apple-btn apple-btn-secondary !py-2.5 disabled:opacity-60">
          {laeuft ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
          {laeuft ? "Fotos werden geholt …" : "Bilder nachholen"}
        </button>
      )}
    </div>
  );
}
