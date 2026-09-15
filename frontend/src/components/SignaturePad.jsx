import { useEffect, useRef, useState } from "react";
import { Eraser } from "lucide-react";

/**
 * Unterschriftsfeld für Touch/Maus. Liefert das Ergebnis als PNG-Data-URL
 * über `onChange(dataUrl|null)`. Auf dem Handy schreibt man direkt mit dem
 * Finger — Scrollen ist im Feld deaktiviert (touch-none), damit der Strich
 * nicht abreißt.
 */
export default function SignaturePad({ label, onChange, height = 160 }) {
  const canvasRef = useRef(null);
  const drawing = useRef(false);
  const [hasInk, setHasInk] = useState(false);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    // Für scharfe Linien auf Retina-Displays.
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = height * dpr;
    const ctx = canvas.getContext("2d");
    ctx.scale(dpr, dpr);
    ctx.lineWidth = 2.2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.strokeStyle = "#111827";
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, rect.width, height);
  }, [height]);

  const pos = (e) => {
    const rect = canvasRef.current.getBoundingClientRect();
    const p = e.touches?.[0] || e;
    return { x: p.clientX - rect.left, y: p.clientY - rect.top };
  };

  const start = (e) => {
    e.preventDefault();
    drawing.current = true;
    const ctx = canvasRef.current.getContext("2d");
    const { x, y } = pos(e);
    ctx.beginPath();
    ctx.moveTo(x, y);
  };

  const move = (e) => {
    if (!drawing.current) return;
    e.preventDefault();
    const ctx = canvasRef.current.getContext("2d");
    const { x, y } = pos(e);
    ctx.lineTo(x, y);
    ctx.stroke();
    if (!hasInk) setHasInk(true);
  };

  const end = () => {
    if (!drawing.current) return;
    drawing.current = false;
    onChange?.(canvasRef.current.toDataURL("image/png"));
  };

  const clear = () => {
    const canvas = canvasRef.current;
    const ctx = canvas.getContext("2d");
    const rect = canvas.getBoundingClientRect();
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, rect.width, height);
    setHasInk(false);
    onChange?.(null);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="text-[11px] text-zinc-500">{label}</label>
        <button type="button" onClick={clear}
                className="text-[11px] text-zinc-500 hover:text-white inline-flex items-center gap-1">
          <Eraser size={11} /> löschen
        </button>
      </div>
      <canvas
        ref={canvasRef}
        style={{ height, width: "100%", touchAction: "none", borderRadius: 12 }}
        className="border bg-white"
        onMouseDown={start} onMouseMove={move} onMouseUp={end} onMouseLeave={end}
        onTouchStart={start} onTouchMove={move} onTouchEnd={end}
      />
      <div className="text-[10px] text-zinc-600 mt-1">
        {hasInk ? "Unterschrift erfasst" : "Mit dem Finger unterschreiben"}
      </div>
    </div>
  );
}
