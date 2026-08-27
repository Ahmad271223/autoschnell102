import LegalLayout, { H2 } from "./LegalLayout";

export default function Impressum() {
  return (
    <LegalLayout title="Impressum">
      <H2>Angaben gemäß § 5 DDG</H2>
      <p>
        AutoSchnell<br />
        Inhaber: Ahmad Fakih<br />
        Baldurstraße 5<br />
        30657 Hannover<br />
        Deutschland
      </p>

      <H2>Kontakt</H2>
      <p>
        Telefon: 0178 3563025<br />
        E-Mail: info@autoschnell.de
      </p>

      <H2>Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV</H2>
      <p>
        Ahmad Fakih<br />
        Baldurstraße 5, 30657 Hannover
      </p>

      <H2>Verbraucherstreitbeilegung / Universalschlichtungsstelle</H2>
      <p>
        Die Plattform richtet sich ausschließlich an gewerbliche Nutzer
        (Autohändler und Zwischenhändler). Wir sind nicht bereit oder
        verpflichtet, an Streitbeilegungsverfahren vor einer
        Verbraucherschlichtungsstelle teilzunehmen.
      </p>

      <H2>Haftung für Inhalte</H2>
      <p>
        Als Diensteanbieter sind wir für eigene Inhalte auf diesen Seiten nach
        den allgemeinen Gesetzen verantwortlich. Für die von Händlern auf dem
        B2B-Marktplatz eingestellten Fahrzeug-Inserate sind die jeweiligen
        Händler selbst verantwortlich; Kaufverträge kommen ausschließlich
        zwischen den beteiligten Parteien zustande.
      </p>
    </LegalLayout>
  );
}
