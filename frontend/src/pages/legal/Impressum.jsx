import LegalLayout, { H2 } from "./LegalLayout";

export default function Impressum() {
  return (
    <LegalLayout title="Impressum" draft>
      <H2>Angaben gemäß § 5 DDG</H2>
      <p>
        [Firmenname, z.B. Mustermann Automobile GmbH]<br />
        [Straße und Hausnummer]<br />
        [PLZ und Ort]<br />
        Deutschland
      </p>

      <H2>Vertreten durch</H2>
      <p>[Vor- und Nachname des Geschäftsführers/Inhabers]</p>

      <H2>Kontakt</H2>
      <p>
        Telefon: [Telefonnummer]<br />
        E-Mail: [E-Mail-Adresse]
      </p>

      <H2>Registereintrag</H2>
      <p>
        [Falls vorhanden:] Eintragung im Handelsregister.<br />
        Registergericht: [Amtsgericht]<br />
        Registernummer: [HRB-Nummer]
      </p>

      <H2>Umsatzsteuer-ID</H2>
      <p>
        Umsatzsteuer-Identifikationsnummer gemäß § 27a UStG:<br />
        [USt-IdNr., z.B. DE123456789]
      </p>

      <H2>Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV</H2>
      <p>
        [Vor- und Nachname]<br />
        [Anschrift wie oben]
      </p>

      <H2>EU-Streitschlichtung</H2>
      <p>
        Die Europäische Kommission stellt eine Plattform zur
        Online-Streitbeilegung (OS) bereit:{" "}
        <a href="https://ec.europa.eu/consumers/odr/" target="_blank" rel="noreferrer"
           className="text-white underline">https://ec.europa.eu/consumers/odr/</a>.
        Unsere E-Mail-Adresse findest du oben im Impressum.
      </p>

      <H2>Verbraucherstreitbeilegung / Universalschlichtungsstelle</H2>
      <p>
        Wir sind nicht bereit oder verpflichtet, an Streitbeilegungsverfahren
        vor einer Verbraucherschlichtungsstelle teilzunehmen.
      </p>
    </LegalLayout>
  );
}
