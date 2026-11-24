/**
 * IBAN in der Fahrer-App (Wunsch Ahmad 24.11.2026): nur Anzeige und ein
 * schneller Hinweis beim Tippen. Die verbindliche Prüfung macht der Server
 * (backend/ueberweisung.py: SEPA-Land, Länge je Land, Prüfziffer).
 */

/** Ohne Leerzeichen/Striche, Großbuchstaben. */
export function ibanNormal(eingabe) {
  return String(eingabe ?? "").replace(/[\s.-]/g, "").toUpperCase();
}

/** In Vierergruppen: "DE89 3704 0044 0532 0130 00". */
export function ibanGruppiert(eingabe) {
  const s = ibanNormal(eingabe);
  return s.replace(/(.{4})(?=.)/g, "$1 ");
}

/** Prüfziffer (Mod-97) — true, wenn die IBAN rechnerisch stimmt. */
export function ibanPruefzifferOk(eingabe) {
  const s = ibanNormal(eingabe);
  if (!/^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$/.test(s)) return false;
  const umgestellt = s.slice(4) + s.slice(0, 4);
  let rest = 0;
  for (const z of umgestellt) {
    const wert = parseInt(z, 36);
    if (Number.isNaN(wert)) return false;
    for (const ziffer of String(wert)) rest = (rest * 10 + Number(ziffer)) % 97;
  }
  return rest === 1;
}

/** Kurzer Hinweis beim Tippen ("" = alles gut oder noch zu kurz für ein Urteil). */
export function ibanHinweis(eingabe) {
  const s = ibanNormal(eingabe);
  if (!s) return "";
  if (!/^[A-Z]{0,2}$|^[A-Z]{2}\d{0,2}$|^[A-Z]{2}\d{2}[A-Z0-9]*$/.test(s)) {
    return "Die IBAN beginnt mit zwei Buchstaben (z. B. DE) und zwei Ziffern.";
  }
  if (s.startsWith("DE") && s.length > 22) return "Eine deutsche IBAN hat 22 Stellen.";
  if (s.startsWith("DE") && s.length === 22 && !ibanPruefzifferOk(s)) {
    return "Die IBAN stimmt nicht — bitte Zeichen für Zeichen prüfen.";
  }
  return "";
}
