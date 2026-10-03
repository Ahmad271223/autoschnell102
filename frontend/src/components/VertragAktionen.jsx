import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CalendarPlus, Eye, FileText, Mail, MoreHorizontal, PenLine, Pencil, Send, Trash2, UserRoundPen,
} from "lucide-react";

// Wunsch Ahmad 03.10.2026: Im Vertragsarchiv standen acht runde Symbol-Knöpfe
// nebeneinander — man wusste nicht mehr, was wofür ist. Jetzt sind nur die zwei
// häufigsten Aktionen sichtbar und beschriftet ("Ansehen", "Senden"); alles
// andere steht mit Text und kurzer Erklärung im Menü "Mehr", nach Zweck
// gruppiert. Ein fehlender Abholtermin bleibt als eigener Hinweis-Knopf sichtbar.
// Die Kennungen (data-testid) der einzelnen Aktionen sind dieselben wie vorher.

const MENU_BREITE = 300;

const knopfStil = { background: "var(--apple-btn-secondary-bg)", color: "var(--text-primary)" };
const knopfKlasse = "h-10 px-3.5 rounded-full inline-flex items-center gap-1.5 text-[13px] font-semibold "
  + "transition-colors hover:brightness-125 disabled:opacity-50 whitespace-nowrap";

function Eintrag({ testid, icon: Icon, titel, text, onClick, disabled, farbe, schliessen }) {
  return (
    <button type="button" role="menuitem" data-testid={testid} disabled={disabled}
            onClick={() => { schliessen(); onClick(); }}
            className="w-full text-left flex items-start gap-3 px-3 py-2.5 rounded-xl transition-colors
                       hover:bg-[var(--hover-bg)] focus-visible:bg-[var(--hover-bg)] outline-none disabled:opacity-50"
            style={{ color: farbe || "var(--text-primary)" }}>
      <Icon size={16} className="mt-0.5 shrink-0" />
      <span className="min-w-0">
        <span className="block text-[13px] font-semibold leading-tight">{titel}</span>
        {text && (
          <span className="block text-[11.5px] leading-snug mt-0.5" style={{ color: "var(--text-muted)" }}>
            {text}
          </span>
        )}
      </span>
    </button>
  );
}

function Gruppe({ titel, children }) {
  return (
    <div className="py-1">
      <div className="px-3 pt-1 pb-1 text-[10.5px] font-semibold uppercase tracking-wider"
           style={{ color: "var(--text-muted)" }}>{titel}</div>
      {children}
    </div>
  );
}

/**
 * it: der Vertrag; a: Aktionen {ansehen, digital, senden, portal, folgeMail, aendern, korrektur,
 * loeschen, termin}; zustand: {termin: bool (Knopf zeigen), terminLaeuft, pdfLaeuft, loeschtId,
 * portalUnterschrieben: bool}
 */
export default function VertragAktionen({ it, a, zustand = {} }) {
  const [offen, setOffen] = useState(false);
  const [pos, setPos] = useState(null);
  const knopfRef = useRef(null);
  const menuRef = useRef(null);
  // Nur bei Tastatur (Enter/Leertaste, event.detail 0) springt der Fokus in das Menü —
  // beim Antippen sah der Fokusrahmen sonst wie eine Vorauswahl aus.
  const perTastatur = useRef(false);
  const pdfGesperrt = !!zustand.pdfLaeuft;

  const schliessen = () => setOffen(false);

  const umschalten = (e) => {
    if (offen) { setOffen(false); return; }
    perTastatur.current = e?.detail === 0;
    const r = knopfRef.current?.getBoundingClientRect?.() || { left: 0, right: 0, top: 0, bottom: 0 };
    const vw = window.innerWidth || 1024;
    const vh = window.innerHeight || 768;
    const left = Math.max(8, Math.min(r.right - MENU_BREITE, vw - MENU_BREITE - 8));
    const platzUnten = vh - r.bottom;
    // Zu wenig Platz unten (Karte ganz unten im Bild): Menü nach oben öffnen.
    const nachOben = platzUnten < 360 && r.top > platzUnten;
    setPos(nachOben
      ? { left, bottom: vh - r.top + 6, maxHeight: Math.max(200, r.top - 16) }
      : { left, top: r.bottom + 6, maxHeight: Math.max(200, platzUnten - 16) });
    setOffen(true);
  };

  useEffect(() => {
    if (!offen) return undefined;
    const draussen = (e) => {
      if (menuRef.current?.contains(e.target) || knopfRef.current?.contains(e.target)) return;
      setOffen(false);
    };
    const taste = (e) => { if (e.key === "Escape") { setOffen(false); knopfRef.current?.focus?.(); } };
    // Das Menü steht fest im Fenster — beim Scrollen oder Größe ändern schließen.
    const weg = (e) => { if (!menuRef.current?.contains(e.target)) setOffen(false); };
    document.addEventListener("mousedown", draussen);
    document.addEventListener("touchstart", draussen);
    document.addEventListener("keydown", taste);
    window.addEventListener("scroll", weg, true);
    window.addEventListener("resize", weg);
    if (perTastatur.current) {
      menuRef.current?.querySelector?.('[role="menuitem"]:not([disabled])')?.focus?.({ preventScroll: true });
    }
    return () => {
      document.removeEventListener("mousedown", draussen);
      document.removeEventListener("touchstart", draussen);
      document.removeEventListener("keydown", taste);
      window.removeEventListener("scroll", weg, true);
      window.removeEventListener("resize", weg);
    };
  }, [offen]);

  const menu = offen && pos && createPortal(
    <div ref={menuRef} role="menu" aria-label="Weitere Aktionen zum Vertrag"
         data-testid={`pdf-mehr-menu-${it.id}`}
         className="fixed z-[60] rounded-2xl border p-1.5 overflow-y-auto"
         style={{ ...pos, width: MENU_BREITE, background: "var(--card-bg)",
                  borderColor: "var(--border-default)", boxShadow: "0 18px 48px rgba(0,0,0,0.35)" }}>
      <Gruppe titel="Ansehen">
        <Eintrag testid={`open-pdf-digital-${it.id}`} icon={FileText} schliessen={schliessen}
                 titel="Digitale Fassung öffnen" disabled={pdfGesperrt} onClick={a.digital}
                 text="So, wie der Verkäufer ihn per E-Mail oder WhatsApp bekommt" />
      </Gruppe>
      <Gruppe titel="Unterschrift & Nachrichten">
        <Eintrag testid={`kundenportal-${it.id}`} icon={PenLine} schliessen={schliessen}
                 onClick={a.portal}
                 farbe={zustand.portalUnterschrieben ? "var(--tx-gruen)" : undefined}
                 titel={zustand.portalUnterschrieben ? "Online unterschrieben – ansehen" : "Online unterschreiben lassen"}
                 text={zustand.portalUnterschrieben ? "Der Kunde hat auf eurer Firmenseite unterschrieben"
                   : "Code erzeugen – der Kunde unterschreibt auf eurer Firmenseite"} />
        <Eintrag testid={`folgemail-${it.id}`} icon={Mail} schliessen={schliessen} onClick={a.folgeMail}
                 titel="Hinweis nach dem Kauf / Bahnverbindung"
                 text="Per E-Mail schicken oder zum Kopieren" />
      </Gruppe>
      <Gruppe titel="Ändern">
        <Eintrag testid={`vertrag-aendern-${it.id}`} icon={Pencil} schliessen={schliessen} onClick={a.aendern}
                 titel="Kaufvertrag ändern"
                 text="Alle Angaben wie beim Anlegen – ergibt eine neue Fassung" />
        <Eintrag testid={`verkaeufer-korrektur-${it.id}`} icon={UserRoundPen} schliessen={schliessen}
                 onClick={a.korrektur} titel="Verkäuferdaten korrigieren"
                 text="Name, Anschrift, Kontakt – ergibt eine neue Fassung" />
      </Gruppe>
      <div className="my-1 border-t" style={{ borderColor: "var(--border-default)" }} />
      <Eintrag testid={`del-pdf-${it.id}`} icon={Trash2} schliessen={schliessen} onClick={a.loeschen}
               disabled={!!zustand.loeschtId} farbe="var(--st-rot)" titel="Vertrag löschen" />
    </div>,
    document.body,
  );

  return (
    <div className="flex flex-wrap items-center justify-end gap-2" data-testid={`vertrag-aktionen-${it.id}`}>
      {zustand.termin && (
        <button type="button" onClick={a.termin} data-testid={`termin-anlegen-${it.id}`}
                disabled={!!zustand.terminLaeuft}
                className={knopfKlasse}
                style={{ background: "rgba(255,159,10,0.14)", color: "var(--tx-amber)" }}
                title="Zu diesem Vertrag gibt es keinen offenen Abholtermin">
          <CalendarPlus size={15} /> Termin anlegen
        </button>
      )}
      <button type="button" onClick={a.ansehen} data-testid={`open-pdf-${it.id}`}
              disabled={pdfGesperrt} aria-busy={zustand.pdfLaeuft === `${it.id}:druck`}
              className={knopfKlasse} style={knopfStil}
              title="Vertrag öffnen (Druckfassung mit Unterschriftsfeldern)">
        <Eye size={15} /> Ansehen
      </button>
      <button type="button" onClick={a.senden} data-testid={`senden-${it.id}`}
              className={knopfKlasse} style={knopfStil}
              title="Vertrag an den Verkäufer senden (WhatsApp oder E-Mail)">
        <Send size={15} /> Senden
      </button>
      <button type="button" ref={knopfRef} onClick={umschalten} data-testid={`pdf-mehr-${it.id}`}
              aria-haspopup="menu" aria-expanded={offen}
              className={knopfKlasse} style={knopfStil}
              title="Weitere Aktionen: digitale Fassung, online unterschreiben, Hinweis-Mail, ändern, löschen">
        <MoreHorizontal size={15} /> Mehr
      </button>
      {menu}
    </div>
  );
}
