import StatusSchild from "@/components/StatusSchild";
import { inseratText, kaufvorgangText, lesbar } from "@/lib/fahrzeugStatus";
import {
  VERSANDWEG, datum, datumZeit, einkaufAus, eur, terminStand, vertragStand,
} from "./akteDaten";

/*
 * Wunsch Ahmad 24.11.2026: "Übersicht" der Fahrzeugakte — fünf kompakte
 * Kacheln (Fahrzeug, Kauf, Inserat, Abholung/Fahrer, Vertrag & Bestätigung),
 * auf großem Bildschirm in einer Reihe, auf dem Handy untereinander. Ein
 * Klick auf eine Kachel öffnet den passenden Reiter.
 */

function Kachel({ titel, testid, onClick, children, fuss }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag type={onClick ? "button" : undefined} onClick={onClick} data-testid={testid}
         className={`tactical-card p-3.5 text-left min-w-0 flex flex-col ${onClick ? "hover:opacity-90 transition cursor-pointer" : ""}`}>
      <div className="text-[11px] font-bold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{titel}</div>
      <div className="mt-1.5 flex-1 min-w-0 text-sm space-y-0.5">{children}</div>
      {fuss && <div className="mt-2 text-[11px]" style={{ color: "var(--text-secondary)" }}>{fuss}</div>}
    </Tag>
  );
}

function Zeile({ k, v, testid }) {
  if (v == null || v === "") return null;
  return (
    <div className="flex justify-between gap-2 min-w-0" data-testid={testid}>
      <span className="shrink-0" style={{ color: "var(--text-secondary)" }}>{k}</span>
      <span className="text-right truncate" style={{ color: "var(--text-primary)" }} title={String(v)}>{v}</span>
    </div>
  );
}

const fmtKm = (n) => {
  const z = typeof n === "number" ? n : Number(n);
  return Number.isFinite(z) && z > 0 ? `${z.toLocaleString("de-DE")} km` : null;
};

export default function AkteUebersicht({ akte, sucher, marktplatz, befundOffen, onReiter }) {
  const v = akte.vehicle || {};
  const d = v.data || {};
  const einkauf = einkaufAus(akte);
  const kv = (akte.kaufvorgaenge || [])[0];
  const listing = (akte.listings || [])[0];
  const termin = terminStand(akte);
  const vertrag = vertragStand(akte);
  const protokolle = akte.protocols_gesamt ?? (akte.protocols || []).length;
  const fahrer = termin?.driver_name || akte.pickup_report?.driver_name || null;

  return (
    <div className="mt-4 grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}
         data-testid="akte-uebersicht">
      <Kachel titel="Fahrzeug" testid="akte-kachel-fahrzeug">
        <Zeile k="EZ" v={d.first_registration} />
        <Zeile k="km" v={fmtKm(d.mileage)} />
        <Zeile k="Antrieb" v={[d.fuel_label || d.fuel, d.gearbox_label || d.gearbox].filter(Boolean).join(" · ")} />
        <Zeile k="Leistung" v={d.power_ps ? `${d.power_ps} PS` : null} />
        <Zeile k="FIN" v={d.vin} />
        {(v.known_defects || []).length > 0 && (
          <div className="text-[12px]" style={{ color: "var(--tx-amber)" }}>
            {v.known_defects.length} bekannte(r) Mangel/Mängel
          </div>
        )}
      </Kachel>

      <Kachel titel="Kauf" testid="akte-kachel-kauf" onClick={() => onReiter("vertrag")}
              fuss={akte.kaufvorgaenge_gesamt > 1 ? `${akte.kaufvorgaenge_gesamt} Kaufvorgänge` : null}>
        <div className="text-lg font-bold" style={{ color: "var(--text-primary)" }} data-testid="akte-kachel-preis">
          {eur(einkauf.preis) || "—"}
        </div>
        {einkauf.hinweis && <div className="text-[12px]" style={{ color: "var(--text-secondary)" }}>{einkauf.hinweis}</div>}
        {kv && (
          <div className="pt-1">
            <StatusSchild status={kv.status} text={kaufvorgangText(kv.status)} />
          </div>
        )}
        {!sucher && akte.owner && <Zeile k="Bearbeiter" v={akte.owner.name} />}
      </Kachel>

      <Kachel titel="Inserat" testid="akte-kachel-inserat">
        {sucher ? (
          <div style={{ color: "var(--text-secondary)" }}>Den Weiterverkauf macht der Chef.</div>
        ) : !marktplatz ? (
          <div style={{ color: "var(--text-secondary)" }}>Marktplatz noch nicht freigeschaltet.</div>
        ) : listing ? (
          <>
            <StatusSchild status={listing.status} text={inseratText(listing.status)} />
            <Zeile k="Preis" v={eur(listing.prices?.public ?? listing.prices?.b2b)} />
            <Zeile k="Seit" v={datum(listing.created_at)} />
          </>
        ) : (
          <div style={{ color: "var(--text-secondary)" }}>Noch kein Inserat.</div>
        )}
      </Kachel>

      <Kachel titel="Abholung · Fahrer" testid="akte-kachel-abholung" onClick={() => onReiter("abholung")}
              fuss={befundOffen ? "Abholprotokoll: Daten noch übernehmen" : null}>
        {termin ? (
          <>
            <Zeile k="Termin" v={`${datum(termin.pickup_date) || "ohne Datum"}${termin.pickup_time ? ` · ${termin.pickup_time}` : ""}`} />
            <Zeile k="Stand" v={lesbar(termin.status || "offen")} />
            <Zeile k="Fahrer" v={fahrer || "noch keiner"} testid="akte-kachel-fahrer" />
          </>
        ) : (
          <div style={{ color: "var(--text-secondary)" }}>Kein Abholtermin.</div>
        )}
        {protokolle > 0 && <Zeile k="Protokolle" v={String(protokolle)} />}
      </Kachel>

      <Kachel titel="Vertrag & Bestätigung" testid="akte-kachel-vertrag" onClick={() => onReiter("vertrag")}
              fuss={akte.contracts_gesamt > 1 ? `${akte.contracts_gesamt} Kaufverträge` : null}>
        {vertrag ? (
          <>
            <Zeile k="Vertrag" v={`${vertrag.nummer}${vertrag.fassung ? ` · Fassung ${vertrag.fassung}` : ""}`} />
            <Zeile k="Verschickt" testid="akte-kachel-versand"
                   v={vertrag.versandAm
                     ? `${datumZeit(vertrag.versandAm)}${VERSANDWEG[vertrag.versandWeg] ? ` ${VERSANDWEG[vertrag.versandWeg]}` : ""}`
                     : "noch nicht"} />
            <Zeile k="Bestätigt" testid="akte-kachel-bestaetigt"
                   v={vertrag.unterschriebenAm
                     ? `unterschrieben ${datumZeit(vertrag.unterschriebenAm)}`
                     : vertrag.portalStatus ? lesbar(vertrag.portalStatus) : "noch nicht"} />
          </>
        ) : (
          <div style={{ color: "var(--text-secondary)" }}>Noch kein Kaufvertrag.</div>
        )}
      </Kachel>
    </div>
  );
}
