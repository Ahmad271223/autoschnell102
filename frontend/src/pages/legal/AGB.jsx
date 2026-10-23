import LegalLayout, { H2 } from "./LegalLayout";

/** Allgemeine Geschäftsbedingungen der Plattform (reines B2B-Angebot). */
export default function AGB() {
  return (
    <LegalLayout title="Allgemeine Geschäftsbedingungen (AGB)">
      <p className="text-zinc-500">
        AutoSchnell (nachfolgend „Anbieter"). Stand: September 2026.
      </p>

      <H2>1. Geltungsbereich</H2>
      <p>
        Diese AGB gelten für die Nutzung der Software-Plattform AutoSchnell
        (Fahrzeugvergleich, Vertragserstellung, Bestandsverwaltung,
        B2B-Marktplatz, Fahrer-App). Das Angebot richtet sich ausschließlich
        an Unternehmer im Sinne von § 14 BGB (Autohändler, Zwischenhändler,
        selbstständige Fahrer sowie Mitarbeiter und Beauftragte dieser
        Unternehmen). Eine Nutzung durch Verbraucher ist ausgeschlossen.
        In der Zugangs-Anfrage bestätigt der Nutzer über eine
        Pflicht-Checkbox, als Unternehmer (B2B) bzw. im Auftrag eines
        Unternehmers zu handeln, akzeptiert diese AGB und nimmt die
        Datenschutzerklärung zur Kenntnis; der Anbieter hält den Zeitpunkt
        fest. Legt der Anbieter ein Konto ohne vorherige Zugangs-Anfrage an,
        holt er diese Bestätigung vor der Übergabe der Zugangsdaten ein und
        dokumentiert sie (bei Zwischenhändlern zusammen mit dem Nachweis der
        Unternehmereigenschaft). Die Angabe der USt-IdNr. oder
        Handelsregister-Nummer ist freiwillig und dient der Prüfung der
        Unternehmereigenschaft.
      </p>
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Kontonummer (13.09.2026) —
          §1, §3, §4 und §11 wurden auf die Anmeldung mit Kontonummer
          umgestellt (Konten legt nur der Anbieter an, E-Mail optional).
          Wortlaut von Ahmad am 14.09.2026 freigegeben (Vorschau der
          Aenderungen in §1, §3, §4, §11); rechtliche Pruefung steht aus. */}

      <H2>2. Leistungen des Anbieters</H2>
      <p>
        Der Anbieter stellt eine Software-Plattform bereit, mit der Händler
        Fahrzeug-Inserate vergleichen, Kaufvertrags-Dokumente erstellen,
        ihren Bestand verwalten, Abholungen organisieren und Fahrzeuge auf
        einem B2B-Marktplatz anbieten können. Der Anbieter ist{" "}
        <b>nicht Vertragspartei</b> der über die Plattform angebahnten
        Fahrzeuggeschäfte — Kaufverträge kommen ausschließlich zwischen den
        beteiligten Parteien (Händler, Verkäufer, Käufer) zustande.
      </p>

      <H2>3. Accounts</H2>
      <ul className="list-disc pl-6 space-y-1">
        <li>Die Angaben in der Zugangs-Anfrage müssen wahrheitsgemäß und
            vollständig sein.</li>
        <li>Accounts legt ausschließlich der Anbieter an. Die Anmeldung
            erfolgt mit der vom Anbieter vergebenen Kontonummer und einem
            Passwort. Passwörter vergibt und setzt ausschließlich der
            Anbieter; Fahrer können ihr Passwort in der Fahrer-App zusätzlich
            selbst ändern.</li>
        <li>Zugangsdaten sind geheim zu halten. Pro Account ist nur eine
            aktive Sitzung zulässig.</li>
        <li>Der Händler-Hauptaccount ist für die Unteraccounts (Sucher)
            seiner Firma und deren Handlungen verantwortlich.</li>
        <li>Der Anbieter kann Accounts bei Missbrauch, Zahlungsverzug oder
            Verstößen gegen diese AGB sperren.</li>
      </ul>

      <H2>4. Preise und Zahlung</H2>
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Stand 05.09.2026 — die
          Liste bildet die TATSAECHLICH eingestellten Preise ab. Kostenlos
          sind Marktplatz-Zugang (MARKTPLATZ_KOSTENLOS=true in
          backend/routes/marketplace.py) und das Verkaufen/Veroeffentlichen
          von Fahrzeugen (VERKAUF_KOSTENLOS=true in backend/routes/team.py).
          Bezahlt wird nur das Sucher-Abo, manuell per Rechnung -> netto
          zzgl. USt. Werden die Schalter auf false gestellt, muessen die
          Preise fuer Marktplatz-Zugang (BUYER_ACCESS_PRICE, aktuell 20,00 €
          brutto) und Verkaufspakete hier WIEDER aufgenommen werden. Gilt die
          Kleinunternehmerregelung (§ 19 UStG), sind die USt-Aussagen und
          die Rechnungsangaben anzupassen. */}
      <ul className="list-disc pl-6 space-y-1">
        <li>Der Händler-Hauptaccount (Verwalten &amp; Verkaufen) ist kostenlos.</li>
        <li>Das Einstellen und Veröffentlichen von Fahrzeugen ist kostenlos
            und in der Anzahl nicht begrenzt.</li>
        <li>Käuferkonten legt der Anbieter nach Anfrage an; der Zugang zum
            B2B-Marktplatz ist kostenlos.</li>
        <li>Sucher-Abo (Suche &amp; Vergleich, pro Nutzer): 150 € / Monat
            (30 Tage) oder 1.500 € / Jahr (365 Tage) — jeweils netto zzgl.
            gesetzlicher Umsatzsteuer, Abrechnung per Rechnung; die
            Freischaltung erfolgt nach Zahlungseingang durch den Anbieter.
            Innerhalb des Abos ist die Zahl der Vergleiche nicht begrenzt;
            neue Inserats-Abrufe bei den Portalen sind zum Schutz vor
            Missbrauch auf 400 je Nutzer und Kalendertag begrenzt (bereits
            abgerufene Inserate zählen nicht mit).</li>
        <li>Das Erstellen, die Vorschau und der Versand von
            Kaufvertrags-Dokumenten setzen ein aktives Sucher-Abo (des Nutzers
            oder seiner Firma) voraus.</li>
        <li>Rechnungen weisen die Umsatzsteuer gesondert aus. Die aktuell
            gültigen Preise werden in der Anwendung angezeigt. Kostenlose
            Leistungen können mit einer Ankündigungsfrist von einem Monat
            kostenpflichtig gestellt werden; laufende Abos bleiben davon bis
            zum Ende der bezahlten Laufzeit unberührt.</li>
      </ul>

      <H2>5. Laufzeit und Kündigung</H2>
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Startpruefung 27.09.2026 (R6) —
          es gibt im Code keine automatische Verlaengerung (Abo endet mit expires_at,
          Verlaengerung nur per Anfrage + Freischaltung, Kuendigung des Chefs per
          POST /dealer/subscription/cancel). Text entsprechend angeglichen;
          rechtliche Pruefung steht aus. */}
      <p>
        Sucher-Abos laufen für die gebuchte Laufzeit (30 bzw. 365 Tage) und
        enden mit deren Ablauf automatisch; eine stillschweigende
        Verlängerung findet nicht statt. Eine Verlängerung kann jederzeit in
        der Anwendung oder formlos in Textform (z. B. per E-Mail) angefragt
        werden und wird nach Zahlungseingang durch den Anbieter
        freigeschaltet; sie schließt an die Restlaufzeit an. Der
        Händler-Hauptaccount kann ein laufendes Abo in der Anwendung zum
        Laufzeitende kündigen; bereits gezahlte Entgelte werden nicht
        erstattet. Kostenlose Accounts können jederzeit gelöscht werden. Das
        Recht zur außerordentlichen Kündigung aus wichtigem Grund bleibt
        unberührt.
      </p>

      <H2>6. Pflichten der Nutzer</H2>
      <ul className="list-disc pl-6 space-y-1">
        <li>Fahrzeugdaten, Preise und Zustandsangaben in Inseraten müssen
            zutreffend sein; der einstellende Händler ist für seine Inhalte
            allein verantwortlich.</li>
        <li>Der Nutzer stellt sicher, dass er an hochgeladenen Fotos und
            Dokumenten die erforderlichen Rechte besitzt.</li>
        <li>Automatisiertes Auslesen der Plattform, Weitergabe von
            Zugangsdaten sowie jede missbräuchliche Nutzung sind untersagt.</li>
        <li>Die mit der Plattform erzeugten Vertragsdokumente sind
            Arbeitshilfen; die rechtliche Prüfung und der rechtskonforme
            Einsatz obliegen dem Händler.</li>
      </ul>

      <H2>7. B2B-Marktplatz</H2>
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Startpruefung 27.09.2026 (R8) —
          der Code (routes/marketplace.py) fuehrt Anfrage/Gegenangebot/Annahme mit
          Reservierung und agreed_price; ob die Annahme schon ein Kaufvertrag ist,
          muss der Anwalt festlegen. Marktplatz ist per MARKTPLATZ_AKTIV derzeit aus. */}
      <p>
        Inserate auf dem Marktplatz richten sich ausschließlich an gewerbliche
        Käufer. Der Käufer stellt über die Plattform eine Kaufanfrage — mit
        oder ohne eigenes Preisangebot; beide Seiten können Gegenangebote
        machen. Nimmt der anbietende Händler eine Anfrage an, wird das
        Fahrzeug für diesen Käufer zum vereinbarten Preis reserviert; wurde
        kein Preis angeboten, gilt der im Inserat für die Käuferstufe
        angezeigte Preis. Die Annahme ist eine verbindliche Reservierung
        zwischen Käufer und Händler; den Kaufvertrag schließen Käufer und
        Händler unmittelbar miteinander. Der Anbieter übernimmt keine Gewähr
        für Zustand, Verfügbarkeit oder Eigenschaften der angebotenen
        Fahrzeuge und ist an den Kaufverträgen nicht beteiligt.
      </p>

      <H2>8. Verfügbarkeit</H2>
      <p>
        Der Anbieter bemüht sich um eine hohe Verfügbarkeit der Plattform,
        schuldet jedoch keine ununterbrochene Erreichbarkeit. Wartungsarbeiten
        und Störungen (auch bei Drittquellen wie externen Inserats-Portalen)
        können zu vorübergehenden Einschränkungen führen.
      </p>

      <H2>9. Daten und Datensicherung</H2>
      <p>
        Die Plattformdaten werden täglich gesichert. Dem Nutzer wird
        empfohlen, wichtige Dokumente (z.B. Vertrags-PDFs) zusätzlich selbst
        zu speichern. Einzelheiten zur Verarbeitung personenbezogener Daten
        regelt die Datenschutzerklärung.
      </p>

      <H2>10. Haftung</H2>
      <p>
        Der Anbieter haftet unbeschränkt bei Vorsatz, grober Fahrlässigkeit
        sowie bei Verletzung von Leben, Körper oder Gesundheit. Bei einfacher
        Fahrlässigkeit haftet der Anbieter nur für die Verletzung
        wesentlicher Vertragspflichten (Kardinalpflichten), begrenzt auf den
        vertragstypischen, vorhersehbaren Schaden. Eine Haftung für
        entgangenen Gewinn, mittelbare Schäden oder Datenverlust, der durch
        zumutbare eigene Sicherung vermeidbar gewesen wäre, ist bei einfacher
        Fahrlässigkeit ausgeschlossen. Die Haftung nach dem
        Produkthaftungsgesetz bleibt unberührt.
      </p>

      <H2>11. Änderungen der AGB</H2>
      <p>
        Der Anbieter kann diese AGB mit Wirkung für die Zukunft ändern.
        Änderungen werden mindestens vier Wochen vor Inkrafttreten in der
        Plattform und zusätzlich per E-Mail angekündigt, soweit eine
        Kontaktadresse hinterlegt ist. Widerspricht der Nutzer
        nicht innerhalb der Frist oder nutzt er die Plattform weiter, gelten
        die geänderten AGB als angenommen; hierauf wird in der Ankündigung
        hingewiesen.
      </p>

      <H2>12. Schlussbestimmungen</H2>
      <p>
        Es gilt das Recht der Bundesrepublik Deutschland. Gerichtsstand für
        alle Streitigkeiten aus diesem Vertragsverhältnis ist Hannover,
        sofern der Nutzer Kaufmann, juristische Person des öffentlichen
        Rechts oder öffentlich-rechtliches Sondervermögen ist. Sollten
        einzelne Bestimmungen dieser AGB unwirksam sein, bleibt die
        Wirksamkeit der übrigen Bestimmungen unberührt.
      </p>
    </LegalLayout>
  );
}
