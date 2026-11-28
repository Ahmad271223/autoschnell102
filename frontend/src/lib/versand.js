/**
 * Erneuter Versand nach einer Korrektur? (Wunsch Ahmad 21.09.2026)
 *
 * In den Einstellungen gibt es einen eigenen Betreff und Text für den
 * "erneuten Versand (nach Korrektur)". Der Versand-Dialog nimmt sie, sobald
 * schon eine FRÜHERE Fassung desselben Vertrags verschickt wurde — dann ist
 * das, was jetzt rausgeht, die korrigierte Fassung.
 *
 * Jeder Versand-Eintrag trägt seit Runde 16 die Fassung ("version"); ältere
 * Einträge ohne Angabe zählen als Fassung 1. Es zählen nur echte
 * Vertragssendungen (E-Mail/WhatsApp) — keine alten Textmails mit "art" und
 * keine, die nachweislich gescheitert sind.
 */
export function nachKorrektur(contract) {
  const aktuell = Number(contract?.version) || 1;
  const eintraege = Array.isArray(contract?.send_status) ? contract.send_status : [];
  return eintraege.some((e) => e
    && !e.art
    && (e.channel === "email" || e.channel === "whatsapp")
    && e.zustellung !== "fehlgeschlagen"
    && (Number(e.version) || 1) < aktuell);
}
