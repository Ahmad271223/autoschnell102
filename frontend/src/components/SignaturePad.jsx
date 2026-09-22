import { useEffect, useRef, useState } from "react";
import { Eraser } from "lucide-react";

// Pruefbericht 20.09.2026 (U-156/F21): So viel Strich (in Bildschirm-Punkten)
// braucht es mindestens, damit ein Feld als unterschrieben gilt. Vorher
// schickte jedes Loslassen ein Bild — auch ein kurzes Antippen ohne Strich —,
// und das rechtsverbindliche Protokoll trug dann eine leere Unterschrift.
// Der Server prueft zusaetzlich, dass wirklich etwas gezeichnet ist.
export const MIN_STRICH_PX = 25;

/**
 * Unterschriftsfeld für Touch/Maus. Liefert das Ergebnis als PNG-Data-URL
 * über `onChange(dataUrl|null)`. Auf dem Handy schreibt man direkt mit dem
 * Finger — Scrollen ist im Feld deaktiviert (touch-none), damit der Strich
 * nicht abreißt.
 */
export default function SignaturePad({ label, onChange, height = 160, startBild = null }) {
  const canvasRef = useRef(null);
  const drawing = useRef(false);
  const strecke = useRef(0);           // gezeichnete Strecke seit dem letzten Löschen
  const letzter = useRef(null);
  const [hasInk, setHasInk] = useState(false);

  // Rollenprüfung 22.09.2026 (RP-546): eine gesicherte Unterschrift (nach einer
  // Neuanmeldung wiederhergestellt) wieder ins Feld zeichnen. Die Seite hat das
  // Bild schon — onChange wird dafür nicht gerufen; "löschen" leert wie immer.
  useEffect(() => {
    if (!startBild) return undefined;
    let aktiv = true;
    const bild = new Image();
    bild.onload = () => {
      const canvas = canvasRef.current;
      if (!aktiv || !canvas) return;
      const rect = canvas.getBoundingClientRect();
      if (!rect.width) return;
      canvas.getContext("2d").drawImage(bild, 0, 0, rect.width, height);
      strecke.current = Math.max(strecke.current, MIN_STRICH_PX);
      setHasInk(true);
    };
    bild.src = startBild;
    return () => { aktiv = false; };
  }, [startBild, height]);

  // Leinwand auf die aktuelle Größe einstellen. U-158: beim Drehen des
  // Handys änderte sich die Anzeigebreite, die Leinwand aber nicht — der
  // Strich landete verzerrt woanders. Vorhandene Tinte wird mitskaliert.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const einrichten = () => {
      const dpr = window.devicePixelRatio || 1;
      const rect = canvas.getBoundingClientRect();
      if (!rect.width) return;
      let alt = null;
      if (canvas.width && canvas.height && strecke.current > 0) {
        alt = document.createElement("canvas");
        alt.width = canvas.width;
        alt.height = canvas.height;
        alt.getContext("2d").drawImage(canvas, 0, 0);
      }
      canvas.width = rect.width * dpr;
      canvas.height = height * dpr;
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.lineWidth = 2.2;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.strokeStyle = "#111827";
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, rect.width, height);
      if (alt) ctx.drawImage(alt, 0, 0, rect.width, height);
    };
    einrichten();
    let timer = null;
    const beiGroesse = () => {
      clearTimeout(timer);
      timer = setTimeout(einrichten, 150);
    };
    window.addEventListener("resize", beiGroesse);
    window.addEventListener("orientationchange", beiGroesse);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", beiGroesse);
      window.removeEventListener("orientationchange", beiGroesse);
    };
  }, [height]);

  const pos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const p = e.touches?.[0] || e.changedTouches?.[0] || e;
    return { x: p.clientX - rect.left, y: p.clientY - rect.top };
  };

  const start = (e) => {
    e.preventDefault();
    drawing.current = true;
    const ctx = canvasRef.current.getContext("2d");
    const p = pos(e);
    letzter.current = p;
    ctx.beginPath();
    ctx.moveTo(p.x, p.y);
  };

  const move = (e) => {
    if (!drawing.current) return;
    e.preventDefault();
    const ctx = canvasRef.current.getContext("2d");
    const p = pos(e);
    const vorher = letzter.current || p;
    strecke.current += Math.hypot(p.x - vorher.x, p.y - vorher.y);
    letzter.current = p;
    ctx.lineTo(p.x, p.y);
    ctx.stroke();
    if (!hasInk && strecke.current >= MIN_STRICH_PX) setHasInk(true);
  };

  // Auch bei abgebrochener Berührung (touchcancel, U-157) — sonst erreichte
  // ein Strich, den das System unterbrach, die App nie.
  const end = () => {
    if (!drawing.current) return;
    drawing.current = false;
    letzter.current = null;
    if (strecke.current >= MIN_STRICH_PX) {
      onChange?.(canvasRef.current.toDataURL("image/png"));
    } else {
      onChange?.(null);
    }
  };

  const clear = () => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, rect.width, height);
    strecke.current = 0;
    setHasInk(false);
    onChange?.(null);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-[11px] text-zinc-500">{label}</label>
        <button type="button" onClick={clear}
                className="text-[11px] text-zinc-500 hover:text-white inline-flex items-center gap-1 min-h-[32px] px-1">
          <Eraser size={11} /> löschen
        </button>
      </div>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={label ? `Unterschriftsfeld: ${label}` : "Unterschriftsfeld"}
        style={{ height, width: "100%", touchAction: "none", borderRadius: 12 }}
        className="border bg-white"
        onMouseDown={start} onMouseMove={move} onMouseUp={end} onMouseLeave={end}
        onTouchStart={start} onTouchMove={move} onTouchEnd={end} onTouchCancel={end}
      />
      <div className="text-[10px] text-zinc-600 mt-1">
        {hasInk ? "Unterschrift erfasst" : "Mit dem Finger unterschreiben"}
      </div>
    </div>
  );
}
