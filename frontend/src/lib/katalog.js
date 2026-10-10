/**
 * Marken/Modelle-Katalog (GET /manual/makes, rund 150 KB) einmal je Sitzung
 * laden. Beide Aufrufer (manuelle Suche, Marktplatz-Filter) teilen sich
 * dieselbe Antwort — vorher lud jeder Seitenwechsel die Liste neu
 * (Nachpruefung Runde 10). Der Server erlaubt zusaetzlich einen Tag
 * Browser-Cache (Cache-Control: private).
 */
let _makes = null;

export function ladeMakes(client) {
  if (!_makes) {
    _makes = client.get("/manual/makes")
      .then((r) => r.data)
      .catch((e) => { _makes = null; throw e; });
  }
  return _makes;
}

/** Nur fuer Tests und Abmeldung: Cache verwerfen. */
export function katalogVergessen() {
  _makes = null;
}
