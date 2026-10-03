// Global Setup UND Teardown: Reste frueherer Laeufe (@e2etest-mail.de) entfernen,
// damit jeder Lauf sauber startet und keine Testkonten im Backend liegen bleiben.
const { sweepLeftovers } = require("./helpers");

module.exports = async () => {
  try {
    await sweepLeftovers();
  } catch (e) {
    console.warn(`[e2e] Aufraeumen uebersprungen: ${e?.message || e}`);
  }
};
