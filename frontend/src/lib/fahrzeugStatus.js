/*
 * Lesbare Status-Texte fuer Fahrzeuge, Kaufvorgaenge, Termine und Inserate.
 *
 * 11.09.2026 (Befund Ahmad): In der Fahrzeugakte stand "abholung_geplant" als
 * Kennung (Status oben und bei den Kaufvorgaengen), auf der Bestandskarte war
 * das Status-Schild abgeschnitten. Alle Seiten holen die Texte jetzt hier —
 * unbekannte Kennungen werden wenigstens lesbar ("nicht_abgeholt" ->
 * "Nicht abgeholt") statt roh angezeigt.
 */

// Alle Zustaende aus backend/lifecycle.py (LIFECYCLE_STATES)
export const LIFECYCLE_LABELS = {
  gefunden: "Gefunden", geloescht: "Gelöscht",
  verglichen: "Verglichen", besichtigung: "Besichtigung",
  verhandlung: "Verhandlung", vertrag_erstellt: "Vertrag erstellt",
  gekauft: "Gekauft", abholung_geplant: "Abholung geplant",
  abgeholt: "Abgeholt", bestand: "Im Bestand",
  verkaufsentwurf: "Verkaufsentwurf", verkaufsbereit: "Verkaufsbereit",
  veroeffentlicht: "Veröffentlicht", reserviert: "Reserviert",
  verkauft: "Verkauft", nicht_abgeholt: "Nicht abgeholt",
  storniert: "Storniert", archiviert: "Archiviert",
};

// Kaufvorgang je Vertrag (backend/kaufvorgang.py, STATUS)
export const KAUFVORGANG_LABELS = {
  vertrag_erstellt: "Vertrag erstellt", gesendet: "Vertrag gesendet",
  abholung_geplant: "Abholung geplant", abgeholt: "Abgeholt",
  nicht_abgeholt: "Nicht abgeholt", storniert: "Storniert",
};

export const INSERAT_LABELS = {
  entwurf: "Entwurf", verkaufsbereit: "Verkaufsbereit",
  veroeffentlicht: "Veröffentlicht", reserviert: "Reserviert",
  verkauft: "Verkauft", zurueckgezogen: "Zurückgezogen",
};

const FARBEN = {
  abholung_geplant: "#0a84ff", nicht_abgeholt: "#ff3b30",
  abgeholt: "#f59e0b", bestand: "#0ea5e9", verkaufsentwurf: "#a855f7",
  verkaufsbereit: "#34c759", veroeffentlicht: "#34c759",
  reserviert: "#eab308", verkauft: "#71717a", archiviert: "#52525b",
};
const FARBE_STANDARD = "#71717a";

/** Kennung lesbar machen: "nicht_abgeholt" -> "Nicht abgeholt". */
export function lesbar(wert) {
  const s = String(wert ?? "").replace(/[_\s]+/g, " ").trim();
  if (!s) return "—";
  return s.charAt(0).toUpperCase() + s.slice(1);
}
export const lifecycleText = (lc) => LIFECYCLE_LABELS[lc] || lesbar(lc);
export const kaufvorgangText = (s) => KAUFVORGANG_LABELS[s] || lesbar(s);
export const inseratText = (s) => INSERAT_LABELS[s] || lesbar(s);
export const statusFarbe = (s) => FARBEN[s] || FARBE_STANDARD;

// Historie der Fahrzeugakte (activity_log.action aus backend/lifecycle.py,
// routes/bestand.py, resale.py, appointments.py, contracts.py, drivers.py).
const AKTION_TEXTE = {
  "abholung.bericht": "Abholbericht vom Fahrer eingereicht",
  "abholung.bericht.korrektur": "Abholbericht vom Fahrer korrigiert",
  "inserat.foto.aus_abholbericht": "Fahrerfotos ins Inserat übernommen",
  "inserat.foto.hinzugefuegt": "Foto zum Inserat hinzugefügt",
  "inserat.foto.entfernt": "Foto aus dem Inserat entfernt",
  "inserat.entwurf": "Inseratsentwurf erstellt",
  "inserat.geaendert": "Inserat geändert",
  "inserat.geloescht": "Inserat gelöscht",
  "fahrzeug.manuell.angelegt": "Fahrzeug manuell angelegt",
  "fahrzeug.manuell.geaendert": "Fahrzeugdaten geändert",
  "fahrzeug.zugewiesen": "Bearbeiter geändert",
  "fahrzeug.abweichungen.uebernommen": "Abweichungen aus der Abholung übernommen",
  "fahrzeug.entscheidung.bestand": "Entscheidung: nur speichern",
  "fahrzeug.entscheidung.verkaufsentwurf": "Entscheidung: weiterverkaufen",
  "fahrzeug.entscheidung.loeschen": "Entscheidung: löschen",
  "fahrzeug.entscheidung.geloescht": "Fahrzeug gelöscht",
  "termin.erstellt": "Abholtermin angelegt",
  "termin.aktualisiert": "Abholtermin geändert",
  "termin.geloescht": "Abholtermin gelöscht",
  "vertrag.abholtermin.geaendert": "Abholtermin im Vertrag geändert",
  "vertrag.link.abgerufen": "Vertrag über den Download-Link abgerufen",
  "vertrag.geloescht.manuell": "Kaufvertrag gelöscht",
};

/**
 * Historie-Kennung lesbar: "fahrzeug.status.abholung_geplant" ->
 * "Status: Abholung geplant"; Unbekanntes wenigstens ohne Punkte/Unterstriche.
 */
export function aktionText(a) {
  const s = String(a ?? "").trim();
  if (AKTION_TEXTE[s]) return AKTION_TEXTE[s];
  if (s.startsWith("fahrzeug.status.")) return `Status: ${lifecycleText(s.slice("fahrzeug.status.".length))}`;
  if (s.startsWith("vertrag.geloescht.")) return "Kaufvertrag gelöscht";
  const inserat = /^inserat\.([a-z_]+)$/.exec(s);
  if (inserat && INSERAT_LABELS[inserat[1]]) return `Inserat: ${INSERAT_LABELS[inserat[1]]}`;
  return lesbar(s.replace(/\./g, " "));
}

/**
 * mobile.de trennt die Ausstattungs-Kuerzel mit "*" ("i ADVANTAGE*AUTOM*5TRG").
 * Ohne Leerzeichen kann der Browser dort nicht umbrechen — auf der Karte
 * blieb deshalb nur "i…" stehen. Anzeige mit " · " statt "*".
 */
export function beschreibungLesbar(s) {
  return String(s ?? "")
    .split("*")
    .map((t) => t.trim())
    .filter(Boolean)
    .join(" · ");
}

/** "2026-09-25" -> "25.09.2026"; alles andere unveraendert. */
export function datumDE(s) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s ?? "").trim());
  if (m) return `${m[3]}.${m[2]}.${m[1]}`;
  return s || "—";
}
