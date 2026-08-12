import LegalLayout, { H2 } from "./LegalLayout";

export default function Datenschutz() {
  return (
    <LegalLayout title="Datenschutzerklärung" draft>
      <H2>1. Verantwortlicher</H2>
      <p>
        Verantwortlicher im Sinne der DSGVO:<br />
        [Firmenname]<br />
        [Straße und Hausnummer], [PLZ und Ort]<br />
        E-Mail: [E-Mail-Adresse] · Telefon: [Telefonnummer]
      </p>

      <H2>2. Welche Daten wir verarbeiten</H2>
      <p>Bei der Nutzung der Plattform verarbeiten wir folgende Daten:</p>
      <ul className="list-disc pl-6 space-y-1">
        <li><b>Account-Daten:</b> Firmenname, Ansprechpartner, E-Mail-Adresse,
            Telefonnummer, Passwort (verschlüsselt als Hash gespeichert)</li>
        <li><b>Geschäftsdaten:</b> Fahrzeugdaten, Kaufverträge, Verkäuferdaten
            (Name, Adresse, Kontaktdaten der Fahrzeugverkäufer), Termine,
            Abholberichte inkl. Fotos</li>
        <li><b>Nutzungsdaten:</b> Anmeldezeitpunkte, durchgeführte Aktionen
            (Audit-Log), IP-Adressen bei sicherheitsrelevanten Vorgängen</li>
        <li><b>Abrechnungsdaten:</b> gebuchte Abos/Pakete und deren Laufzeiten</li>
      </ul>

      <H2>3. Zwecke und Rechtsgrundlagen</H2>
      <ul className="list-disc pl-6 space-y-1">
        <li>Bereitstellung der Plattform und Vertragserfüllung
            (Art. 6 Abs. 1 lit. b DSGVO)</li>
        <li>Erstellung von Kaufverträgen und Beweis-Archiven im Auftrag des
            Händlers (Art. 6 Abs. 1 lit. b und f DSGVO)</li>
        <li>Sicherheit der Plattform, Missbrauchs-Abwehr, Audit-Log
            (Art. 6 Abs. 1 lit. f DSGVO)</li>
        <li>Gesetzliche Aufbewahrungspflichten (Art. 6 Abs. 1 lit. c DSGVO)</li>
      </ul>

      <H2>4. Auftragsverarbeitung / Empfänger</H2>
      <p>
        [Anpassen je nach tatsächlichem Hosting/Diensten:] Unsere Server werden
        betrieben bei [Hosting-Anbieter, z.B. Hetzner Online GmbH, Deutschland].
        Zum Laden von Inseratsdaten werden öffentlich zugängliche
        Fahrzeug-Inserate (z.B. kleinanzeigen.de) abgerufen. [Falls genutzt:]
        Für die Analyse der App-Nutzung setzen wir PostHog ein — nur nach
        deiner Einwilligung über den Cookie-Banner (Art. 6 Abs. 1 lit. a DSGVO).
      </p>

      <H2>5. Speicherdauer</H2>
      <p>
        Account- und Vertragsdaten speichern wir für die Dauer der
        Geschäftsbeziehung und gemäß den gesetzlichen Aufbewahrungsfristen
        (6 bzw. 10 Jahre nach HGB/AO). Fahrzeug-Fotos aus Abholungen werden
        nach [7/14] Tagen automatisch gelöscht; Bestandsfahrzeug-Daten nach
        50 Tagen archiviert. Beweis-Snapshots von Inseraten bewahren wir zur
        Dokumentation des Vertragsschlusses auf.
      </p>

      <H2>6. Deine Rechte</H2>
      <p>
        Du hast das Recht auf Auskunft (Art. 15), Berichtigung (Art. 16),
        Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18),
        Datenübertragbarkeit (Art. 20) und Widerspruch (Art. 21 DSGVO).
        Wende dich dazu an die oben genannte E-Mail-Adresse. Außerdem hast du
        ein Beschwerderecht bei einer Datenschutz-Aufsichtsbehörde, z.B.
        [zuständige Landesdatenschutzbehörde].
      </p>

      <H2>7. Cookies & lokale Speicherung</H2>
      <p>
        Für die Anmeldung speichern wir ein technisches Sitzungs-Token im
        lokalen Speicher deines Browsers (kein Tracking, technisch
        erforderlich). Analyse-Cookies werden nur nach Einwilligung über den
        Cookie-Banner gesetzt; die Einwilligung kannst du jederzeit widerrufen.
      </p>

      <H2>8. Datensicherheit</H2>
      <p>
        Passwörter werden ausschließlich als bcrypt-Hash gespeichert. Die
        Übertragung erfolgt verschlüsselt über HTTPS. Pro Account ist nur eine
        aktive Sitzung zulässig; sicherheitsrelevante Aktionen werden
        protokolliert.
      </p>

      <p className="text-zinc-500 text-sm">Stand: [Monat/Jahr]</p>
    </LegalLayout>
  );
}
