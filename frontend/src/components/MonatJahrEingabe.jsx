import { useLayoutEffect, useRef, useState } from "react";
import {
  monatJahrAusText, monatJahrPruefen, monatJahrTippen, ziffernVorCursor as cursorZiffern,
} from "@/lib/monatJahr";

/**
 * Eingabe fuer Erstzulassung und HU/TUEV im Format MM/JJJJ (Wunsch Ahmad,
 * 12.09.2026): Zifferntastatur am Handy, der "/" kommt von selbst.
 * Regeln: lib/monatJahr.js.
 *
 * Bewusst type="text" mit inputMode="numeric": type="number" verliert die
 * fuehrende Null und den "/", type="month" verhaelt sich je Browser anders.
 *
 * Ein alter Wert, der nicht dem Format entspricht, wird NICHT beim Anzeigen
 * umgeschrieben ("2018" oder "Neu" aus einem Inserat bliebe sonst kaputt) —
 * erst wenn jemand ins Feld tippt, und nur wenn er lesbar ist.
 */
export default function MonatJahrEingabe({
  value, onChange, art = "ez", className, style, disabled, testid,
  placeholder = "MM/JJJJ", id,
}) {
  const feld = useRef(null);
  const ziffernVorCursor = useRef(null);
  const [hinweis, setHinweis] = useState("");
  const wert = String(value ?? "");
  // Nur getippte Werte (Ziffern und "/") pruefen — "keine HU" oder ein
  // Altwert wie "Neu" sind kein Tippfehler.
  const getippt = /^[\d/]+$/.test(wert);

  // Nach dem Umformatieren den Cursor hinter dieselbe Anzahl Ziffern setzen —
  // sonst springt er beim Tippen mitten im Text ans Ende.
  useLayoutEffect(() => {
    const el = feld.current;
    if (ziffernVorCursor.current == null || !el || document.activeElement !== el) return;
    let rest = ziffernVorCursor.current;
    let pos = 0;
    while (pos < wert.length && rest > 0) {
      if (/\d/.test(wert[pos])) rest -= 1;
      pos += 1;
    }
    if (wert[pos] === "/" && pos === 2) pos += 1;
    el.setSelectionRange(pos, pos);
    ziffernVorCursor.current = null;
  });

  const aendern = (e) => {
    const roh = e.target.value;
    const loeschen = String(e.nativeEvent?.inputType || "").startsWith("delete");
    const r = monatJahrTippen(roh, wert, { loeschen });
    ziffernVorCursor.current = cursorZiffern(roh, e.target.selectionStart, r.wert);
    setHinweis(r.hinweis);
    onChange(r.wert);
  };

  const einfuegen = (e) => {
    const text = e.clipboardData?.getData("text") || "";
    e.preventDefault();
    const r = monatJahrAusText(text, { art });
    if (r) {
      setHinweis("");
      onChange(r);
    } else {
      setHinweis("Format nicht erkannt – bitte MM/JJJJ");
    }
  };

  const fokus = () => {
    if (wert && !/^\d{2}\/\d{4}$/.test(wert)) {
      const r = monatJahrAusText(wert, { art });
      if (r) onChange(r);
    }
  };

  const verlassen = () => {
    if (!getippt) { setHinweis(""); return; }
    const p = monatJahrPruefen(wert, { art });
    setHinweis(p.fehler || p.hinweis);
  };

  const ungueltig = Boolean(wert) && getippt && !monatJahrPruefen(wert, { art }).ok;

  return (
    <span className="block">
      <input
        ref={feld}
        id={id}
        type="text"
        inputMode="numeric"
        pattern="[0-9/]*"
        maxLength={7}
        autoComplete="off"
        enterKeyHint="next"
        value={wert}
        onChange={aendern}
        onPaste={einfuegen}
        onFocus={fokus}
        onBlur={verlassen}
        placeholder={placeholder}
        disabled={disabled}
        aria-invalid={ungueltig || undefined}
        data-testid={testid}
        className={className}
        style={style}
      />
      {hinweis && (
        <span className="block text-[11px] mt-1" style={{ color: "#fbbf24" }}
              data-testid={testid ? `${testid}-hinweis` : undefined}>
          {hinweis}
        </span>
      )}
    </span>
  );
}
