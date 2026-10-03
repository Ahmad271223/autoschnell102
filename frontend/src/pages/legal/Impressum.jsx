import LegalLayout, { H2 } from "./LegalLayout";

export default function Impressum() {
  return (
    <LegalLayout title="Impressum">
      {/* BETREIBER-HINWEIS (im Browser unsichtbar): Wunsch Ahmad 17.09.2026 —
          Inhaber, Anschrift, Telefon, E-Mail und "Verantwortlich nach § 18
          Abs. 2 MStV" vorerst entfernt. Ein Impressum mit diesen Angaben ist
          fuer geschaeftliche Webseiten Pflicht (§ 5 DDG) — vor dem
          Wiedereinsetzen hier und in Datenschutz.jsx/AGB.jsx ergaenzen. */}
      <H2>Angaben gemäß § 5 DDG</H2>
      <p>
        AutoSchnell<br />
        Die Angaben zum Anbieter werden derzeit aktualisiert.
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
