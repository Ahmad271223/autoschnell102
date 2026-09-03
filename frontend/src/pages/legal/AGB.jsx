import LegalLayout, { H2 } from "./LegalLayout";

/** Allgemeine Geschäftsbedingungen der Plattform (reines B2B-Angebot). */
export default function AGB() {
  return (
    <LegalLayout title="Allgemeine Geschäftsbedingungen (AGB)">
      <p className="text-zinc-500">
        AutoSchnell — Inhaber Ahmad Fakih, Baldurstraße 5, 30657 Hannover
        (nachfolgend „Anbieter"). Stand: August 2026.
      </p>

      <H2>1. Geltungsbereich</H2>
      <p>
        Diese AGB gelten für die Nutzung der Software-Plattform AutoSchnell
        (Fahrzeugvergleich, Vertragserstellung, Bestandsverwaltung,
        B2B-Marktplatz, Fahrer-App). Das Angebot richtet sich ausschließlich
        an Unternehmer im Sinne von § 14 BGB (Autohändler, Zwischenhändler
        und deren Mitarbeiter). Eine Nutzung durch Verbraucher ist
        ausgeschlossen. Mit der Registrierung bestätigt der Nutzer
        ausdrücklich, als Unternehmer (B2B) zu handeln, und akzeptiert diese
        AGB sowie die Datenschutzerklärung. Bei der Registrierung als
        Zwischenhändler auf dem Marktplatz erfolgt diese Bestätigung über
        eine Pflicht-Checkbox; die Angabe der USt-IdNr. oder
        Handelsregister-Nummer ist freiwillig und dient der Prüfung der
        Unternehmereigenschaft.
      </p>

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

      <H2>3. Registrierung und Accounts</H2>
      <ul className="list-disc pl-6 space-y-1">
        <li>Die Angaben bei der Registrierung müssen wahrheitsgemäß und
            vollständig sein.</li>
        <li>Zugangsdaten sind geheim zu halten. Pro Account ist nur eine
            aktive Sitzung zulässig.</li>
        <li>Der Händler-Hauptaccount ist für die von ihm angelegten
            Unteraccounts (Sucher) und deren Handlungen verantwortlich;
            Passwörter der Sucher verwaltet ausschließlich der Hauptaccount.</li>
        <li>Der Anbieter kann Accounts bei Missbrauch, Zahlungsverzug oder
            Verstößen gegen diese AGB sperren.</li>
      </ul>

      <H2>4. Preise und Zahlung</H2>
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Preis-/USt-Darstellung
          bitte vor dem Live-Gang bestaetigen. Technischer Stand 09/2026:
          Stripe zieht fuer den Marktplatz-Zugang exakt 20,00 € ein
          (BUYER_ACCESS_PRICE in backend/routes/marketplace.py, kein
          USt-Aufschlag) -> hier als Brutto-Endpreis ausgewiesen. Sucher-Abo
          150/1.500 € und Verkaufspakete werden manuell per Rechnung
          abgerechnet -> hier netto zzgl. USt. Gilt die
          Kleinunternehmerregelung (§ 19 UStG), sind die USt-Aussagen und
          die Rechnungsangaben anzupassen. */}
      <ul className="list-disc pl-6 space-y-1">
        <li>Der Händler-Hauptaccount (Verwalten &amp; Verkaufen) ist kostenlos.</li>
        <li>Sucher-Abo (Suche &amp; Vergleich, pro Nutzer): 150 € / Monat
            (30 Tage) oder 1.500 € / Jahr (365 Tage) — jeweils netto zzgl.
            gesetzlicher Umsatzsteuer, Abrechnung per Rechnung; die
            Freischaltung erfolgt nach Zahlungseingang durch den Anbieter.</li>
        <li>Marktplatz-Zugang für Zwischenhändler: 20 € / Monat (30 Tage) —
            Endpreis inkl. gesetzlicher Umsatzsteuer, Zahlung im Voraus über
            Stripe oder per Rechnung.</li>
        <li>Verkaufspakete (Anzahl Veröffentlichungen pro Monat) gemäß
            aktueller Preisliste, netto zzgl. gesetzlicher Umsatzsteuer.
            Eine Veröffentlichung zählt auch dann, wenn das Inserat im
            selben Zeitraum wieder gelöscht wird.</li>
        <li>Rechnungen weisen die Umsatzsteuer gesondert aus. Die aktuell
            gültigen Preise werden in der Anwendung angezeigt.</li>
      </ul>

      <H2>5. Laufzeit und Kündigung</H2>
      <p>
        Monats-Abos verlängern sich jeweils um einen Monat und sind zum Ende
        des laufenden Abrechnungsmonats kündbar. Jahres-Abos verlängern sich
        um ein weiteres Jahr, wenn sie nicht spätestens vier Wochen vor
        Laufzeitende gekündigt werden. Kostenlose Accounts können jederzeit
        gelöscht werden. Die Kündigung ist formlos per E-Mail an
        info@autoschnell.de möglich. Das Recht zur außerordentlichen
        Kündigung aus wichtigem Grund bleibt unberührt.
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
      <p>
        Inserate auf dem Marktplatz richten sich ausschließlich an gewerbliche
        Käufer. Die Kontaktaufnahme erfolgt direkt (telefonisch) zwischen
        Käufer und anbietendem Händler. Der Anbieter übernimmt keine Gewähr
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
        Änderungen werden mindestens vier Wochen vor Inkrafttreten per
        E-Mail oder in der Plattform angekündigt. Widerspricht der Nutzer
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
