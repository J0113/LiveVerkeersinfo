# 03 — Roadworks, closures, temporary measures & zones

All DATEX II **v3** `SituationPublication` feeds (except emission zones, which is
a ControlledZoneTable). They share the same `mc:messageContainer` envelope as
`actueel_beeld` — only the `situationRecord/@xsi:type` differs. A single generic
DATEX-v3-situation parser handles feeds 1–4 below.

---

## planningsfeed_wegwerkzaamheden_en_evenementen.xml.gz — roadworks & events

- **Format**: DATEX II v3 `sit:SituationPublication`. **Decompressed** ~big (21M
  compressed). **Refresh**: frequently (planning feed, ~15min/daily).
- **Record type**: `sit:MaintenanceWorks` (roadworks) and event situations.
- **Content**: planned & ongoing roadworks + events, national. Long validity
  windows (records can be created years earlier, e.g. `creationTime` 2022).

### Key fields
```
sit:situation id="NDW03_166417_PRJ_2"
└─ sit:situationRecord xsi:type="sit:MaintenanceWorks"
   ├─ creation/version times
   ├─ sit:probabilityOfOccurrence            (probable…)
   ├─ sit:source/com:sourceName              (e.g. "Gemeente Gorinchem")
   ├─ sit:validity/com:validityTimeSpecification (start/end window)
   └─ sit:locationReference                   (point or linear)
```
- **Postgres**: feed into shared `situation` table with `category='roadworks'`.

---

## planningsfeed_brugopeningen.xml.gz — bridge openings

- **Format**: DATEX II v3 `sit:SituationPublication`. **Refresh** ~60s.
- **Record type**: `sit:GeneralNetworkManagement`.
- **Content**: scheduled/predicted bridge openings (`probabilityOfOccurrence=riskOf`),
  source e.g. `BMS01`. Situation id encodes bridge object id.
- **Postgres**: shared `situation` table, `category='bridge_opening'`.

---

## tijdelijke_verkeersmaatregelen_afsluitingen.xml.gz — temporary closures

- **Format**: DATEX II v3 `sit:SituationPublication`. **Refresh** ~60s.
- **Record type**: `sit:RoadOrCarriagewayOrLaneManagement` (e.g. `CLOSED_LANES`).
- **Content**: active temporary road/lane closures. Source e.g. RWS districts.
- **Postgres**: shared `situation` table, `category='closure'`.

---

## tijdelijke_verkeersmaatregelen_maximum_snelheden.xml.gz — temporary speed limits

- **Format**: DATEX II v3 `sit:SituationPublication`. **Refresh** ~60s. Small (21K).
- **Record type**: `sit:SpeedManagement`.
- **Content**: active temporary maximum-speed orders. Source e.g. RWS districts.
- **Postgres**: shared `situation` table, `category='speed_limit'` (capture the
  ordered speed value from the SpeedManagement record).

---

## emissiezones.xml.gz — low-emission / environmental zones

- **Format**: DATEX II **v3**, payload `cz:ControlledZoneTablePublication`
  (namespaces `controlledZone`, `trafficRegulation`). **Refresh** ~daily.
- **Content**: municipal environmental/low-emission zones (`controlledZoneType=lowEmissionZone`).

### Structure
```
cz:ControlledZoneTablePublication
└─ cz:controlledZoneTable
   └─ cz:urbanVehicleAccessRegulation id=… version=…
      ├─ cz:name                       (e.g. "Milieuzone Arnhem")
      ├─ cz:controlledZoneType         (lowEmissionZone)
      ├─ cz:urlForFurtherInformation
      ├─ cz:status                     (active)
      └─ cz:trafficRegulationOrder
         └─ tro:issuingAuthority       (e.g. "Arnhem")
            (+ zone geometry / vehicle-class conditions further in record)
```
- **Postgres**: `emission_zone` (id, name, type, status, authority, info_url,
  geom POLYGON, conditions JSONB).
