import LegalLayout, { H2 } from "./LegalLayout";

export default function Datenschutz() {
  return (
    <LegalLayout title="Datenschutzerklärung">
      <H2>1. Verantwortlicher</H2>
      <p>
        Verantwortlicher im Sinne der DSGVO:<br />
        AutoSchnell — Inhaber Ahmad Fakih<br />
        Baldurstraße 5, 30657 Hannover<br />
        E-Mail: info@autoschnell.de · Telefon: 0178 3563025
      </p>

      <H2>2. Welche Daten wir verarbeiten</H2>
      <p>Bei der Nutzung der Plattform verarbeiten wir folgende Daten:</p>
      <ul className="list-disc pl-6 space-y-1">
        <li><b>Account-Daten:</b> Firmenname, Ansprechpartner, E-Mail-Adresse,
            Telefonnummer, Passwort (verschlüsselt als Hash gespeichert)</li>
        <li><b>Geschäftsdaten:</b> Fahrzeugdaten, Kaufverträge, Verkäuferdaten
            (Name, Adresse, Kontaktdaten der Fahrzeugverkäufer), Termine,
            Abholberichte inkl. Fotos</li>
        <li><b>Marktplatz-Anfragen:</b> Kaufinteressen zwischen Käufern und
            Händlern inkl. Preisangebot, Gegenangebot und mitgeschickter
            Nachricht (kein allgemeiner Chat — die Nachrichten gehören zur
            jeweiligen Anfrage und deren Verlauf)</li>
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
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Stand 04.09.2026 —
          die Liste bildet den TATSAECHLICHEN Produktionsbetrieb ab:
          Hetzner (Hosting), Resend (E-Mail), Cloudflare R2 (Dateien),
          Apify (Inserats-Abruf). Stripe ist aktuell NICHT
          im Einsatz und deshalb als solche gekennzeichnet — werden sie
          eingeschaltet, muss der jeweilige Absatz wieder aktiv formuliert
          werden. Herkunft: backend/email_service.py, storage_service.py,
          mobile_service.py + autoscout_service.py, routes/payments.py,
          beweis_service.py (Beweisdokumente ohne externen Dienst). Der EINZIGE offene Punkt
          ist der Rechenzentrums-Standort in eckigen Klammern. */}
      <p>
        Für den Betrieb der Plattform setzen wir die folgenden Dienstleister
        ein. Soweit sie personenbezogene Daten in unserem Auftrag
        verarbeiten, geschieht das auf Grundlage von
        Auftragsverarbeitungsverträgen gemäß Art. 28 DSGVO; eine Übermittlung
        in Länder außerhalb der EU/des EWR erfolgt nur auf Grundlage eines
        Angemessenheitsbeschlusses oder von EU-Standardvertragsklauseln
        (Art. 44 ff. DSGVO).
      </p>
      <ul className="list-disc pl-6 space-y-1">
        <li><b>Server-Hosting und Datenbank:</b> Hetzner Online GmbH,
            Industriestr. 25, 91710 Gunzenhausen, Deutschland. Betrieb der
            Anwendung und Speicherung sämtlicher Plattformdaten (Account-,
            Geschäfts- und Nutzungsdaten). Rechenzentrum:
            [Standort eintragen, z.&nbsp;B. Falkenstein oder Nürnberg].</li>
        <li><b>E-Mail-Versand:</b> System- und Vertrags-E-Mails (z.&nbsp;B.
            Passwort zurücksetzen, Einladungen, Kaufvertrag an den Verkäufer)
            versenden wir über Resend, Inc., 2261 Market Street #5039,
            San Francisco, CA 94114, USA. Der Versand läuft über die
            EU-Region des Anbieters (Irland). Übermittelt werden die
            Empfänger-Adresse, der Betreff, der Inhalt der Nachricht und
            etwaige Anhänge (z.&nbsp;B. das Vertrags-PDF). Grundlage für die
            Übermittlung in die USA sind EU-Standardvertragsklauseln
            (Art.&nbsp;46 DSGVO). Datenschutzhinweise: resend.com/legal/privacy-policy.</li>
        <li><b>Zahlungsabwicklung:</b> derzeit nicht im Einsatz. Der
            Marktplatz und das Einstellen von Fahrzeugen sind kostenlos; es
            werden keine Zahlungsdaten erhoben und keine an einen
            Zahlungsdienstleister übermittelt. Sollte künftig eine
            kostenpflichtige Funktion hinzukommen, wird der dann eingesetzte
            Zahlungsdienstleister hier vorab benannt.</li>
        <li><b>Abruf von Fahrzeug-Inseraten:</b> Öffentlich zugängliche
            Inserate von mobile.de und AutoScout24 laden wir über den
            Abrufdienst der Apify Technologies s.r.o., Vodičkova 704/36,
            110 00 Prag, Tschechien (EU). Übermittelt werden nur die
            Inserats-URL bzw. die Suchparameter — keine Daten unserer Nutzer.
            Inserate von kleinanzeigen.de werden je nach Konfiguration von
            unserem Server oder direkt aus dem Browser des Nutzers abgerufen.</li>
        <li><b>Dateispeicher:</b> Fotos, Abholberichte, Vertrags-PDFs und
            die verschlüsselten Datensicherungen liegen im Objektspeicher
            Cloudflare R2. Vertragspartner ist die Cloudflare Germany GmbH,
            Rosental 7, 80331 München, Deutschland, für die Cloudflare, Inc.,
            101 Townsend St., San Francisco, CA 94107, USA. Grundlage für die
            Übermittlung in die USA sind EU-Standardvertragsklauseln
            (Art.&nbsp;46 DSGVO). Datenschutzhinweise:
            cloudflare.com/de-de/privacypolicy.</li>
        <li><b>Beweisdokumente zu Inseraten:</b> kein externer Dienst im
            Einsatz. Wird ein Inserats-Link zum ersten Mal über die Plattform
            verwendet, erstellt unser eigener Server ein PDF mit den im
            Inserat öffentlich angegebenen Fahrzeugdaten, Fotos, der
            Anzeigen-ID und der Inserats-Adresse. Es wird dafür nichts an
            Dritte übermittelt. Anbieterangaben: bei gewerblichen Anbietern
            Name, Anschrift, Telefon und E-Mail laut Inserat; bei privaten
            Anbietern nur Postleitzahl und Ort. Telefonnummern und
            E-Mail-Adressen, die private Anbieter in Titel oder Beschreibung
            nennen, machen wir unkenntlich.</li>
      </ul>
      <p>
        Schriftarten liefern wir lokal von unserem eigenen Server aus — es
        werden keine Schriften von Google Fonts, Fontshare oder anderen
        Drittanbietern nachgeladen. Wir setzen KEINE Analyse- oder
        Werbe-Tracker ein.
      </p>

      <H2>5. Speicherdauer</H2>
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Die genannten Fristen
          sind technisch konfigurierte Werte, keine rechtlich geprueften
          Zusagen — eine abschliessende rechtliche Pruefung steht noch aus.
          Quellen: backend/cleanup_service.py (VERTRAG_AUFBEWAHRUNG_TAGE=90,
          LOG_AUFBEWAHRUNG_TAGE=180, SNAPSHOT_RETENTION_DAYS=60 (nur Alt-Snapshots),
          beweis_service.py BEWEIS_AUFBEWAHRUNG_TAGE=90,
          CLEANUP_RULES 7/14 Tage fuer Inseratsfotos, FAHRERFOTO_TAGE=90
          fuer Fahrerfotos ab dem Hochladen), routes/bestand.py
          (BESTAND_RETENTION_DAYS=50), routes/listings.py
          (LISTING_CACHE_TTL_HOURS). Wird eine Umgebungsvariable geaendert,
          muss dieser Text nachgezogen werden. Stand 13.09.2026:
          LISTING_CACHE_TTL_HOURS steht per Default auf 2160 h (= 90 Tage);
          cleanup_service.inseratscache_rotieren loescht Inseratsdaten
          spaetestens INSERATSCACHE_MAX_TAGE = 90 Tage nach dem Abruf — passt
          zu "max. 90 Tage". */}
      <p>
        <b>Account-Daten</b> speichern wir für die Dauer der
        Geschäftsbeziehung; nach Löschung des Accounts werden sie entfernt,
        soweit keine gesetzlichen Aufbewahrungspflichten (z.B. für
        Rechnungen) entgegenstehen.
      </p>
      <p>
        <b>Kaufverträge:</b> Die auf der Plattform erzeugten
        Vertragsdokumente (PDF) und die darin enthaltenen personenbezogenen
        Daten (insbesondere Name, Anschrift und Kontaktdaten des
        Fahrzeugverkäufers sowie Unterschriften) werden nach Ablauf der
        eingestellten Aufbewahrungsfrist (derzeit 90 Tage nach Erstellung)
        automatisch und vollständig gelöscht. Für die handels- und
        steuerrechtliche Aufbewahrung des Kaufvertrags (6 bzw. 10 Jahre nach
        HGB/AO) ist der Händler selbst verantwortlich: Er lädt das
        Vertragsdokument herunter und bewahrt es in seinen eigenen
        Unterlagen auf. Anonymisierte Fahrzeugdaten ohne Personenbezug
        (z.B. Marke, Modell, Erstzulassung, Kaufpreis) bleiben dauerhaft
        gespeichert.
      </p>
      <p>
        Fotos, die der Fahrer bei der Abholung zu Abweichungen aufnimmt,
        werden 90 Tage nach dem Hochladen automatisch gelöscht, also etwa so
        lange, wie der zugehörige Kaufvertrag aufbewahrt wird; der Text des
        Abholberichts bleibt als Geschäftsunterlage erhalten. Aus dem
        Inserat übernommene Fahrzeugfotos werden 7 Tage nach der Abholung
        (bei nicht abgeholten Fahrzeugen nach 14 Tagen) gelöscht, sofern das
        Fahrzeug nicht in den Bestand oder Verkauf übernommen wurde.
        Bestandsfahrzeug-Daten werden nach 50 Tagen archiviert.
        Beweisdokumente zu Inseraten löschen wir 90 Tage nach ihrer
        Erstellung, außer zu dem Inserat besteht bei einem Händler noch ein
        Kaufvertrag, ein Abholtermin, ein Verkaufsinserat oder ein
        Bestandsfahrzeug — dann so lange wie dieser Vorgang. Beweis-Aufnahmen aus der Zeit vor dem
        10.09.2026 werden nach 60 Tagen gelöscht, bei einem Kaufvertrag
        mit diesem.
      </p>
      <p>Weitere Fristen:</p>
      <ul className="list-disc pl-6 space-y-1">
        <li>Zugangs- und Abo-Anfragen (erledigt oder abgelehnt): 90 Tage</li>
        <li>Fehlerprotokolle: max. 365 Tage</li>
        <li>Marktplatz-Anfragen: 180 Tage nach Abschluss</li>
        <li>Inserats-Cache (zwischengespeicherte Inseratsdaten): max. 90 Tage</li>
        <li>Backups: täglich; auf unseren Servern 14 Tage, zusätzlich
            verschlüsselt außer Haus bis zu 30 Tage</li>
      </ul>

      <H2>6. Deine Rechte</H2>
      <p>
        Du hast das Recht auf Auskunft (Art. 15), Berichtigung (Art. 16),
        Löschung (Art. 17), Einschränkung der Verarbeitung (Art. 18),
        Datenübertragbarkeit (Art. 20) und Widerspruch (Art. 21 DSGVO).
        Wende dich dazu an die oben genannte E-Mail-Adresse. Außerdem hast du
        ein Beschwerderecht bei einer Datenschutz-Aufsichtsbehörde — für uns
        zuständig: Die Landesbeauftragte für den Datenschutz Niedersachsen,
        Prinzenstraße 5, 30159 Hannover.
      </p>

      <H2>7. Cookies & lokale Speicherung</H2>
      <p>
        Für die Anmeldung speichern wir ein technisches Sitzungs-Token im
        lokalen Speicher deines Browsers (kein Tracking, technisch
        erforderlich). Darüber hinaus setzen wir keine Cookies ein —
        insbesondere keine Analyse- oder Werbe-Cookies.
      </p>
      <p>
        Außerdem merkt sich dein Browser einige technische Einstellungen: helles
        oder dunkles Design, mit welcher Anmeldung (Firma, Fahrer, Marktplatz) du
        zuletzt angemeldet warst — damit die installierte App gleich die richtige
        Anmeldeseite öffnet — und ob AutoSchnell auf diesem Gerät als App
        installiert ist. Damit sich AutoSchnell als App installieren lässt,
        richtet die Seite einen sogenannten Service Worker ein. Er speichert keine
        Inhalte und keine personenbezogenen Daten; er zeigt nur ohne
        Internetverbindung einen Hinweis an.
      </p>

      <H2>8. Datensicherheit</H2>
      <p>
        Passwörter werden ausschließlich als bcrypt-Hash gespeichert. Die
        Übertragung erfolgt verschlüsselt über HTTPS. Pro Account ist nur eine
        aktive Sitzung zulässig; sicherheitsrelevante Aktionen werden
        protokolliert.
      </p>

      <p className="text-zinc-500 text-sm">Stand: August 2026</p>
    </LegalLayout>
  );
}
