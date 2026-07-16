# Open-source field and feed audit

Status: P0/P1 field-preservation implementation complete; retirement gates and
maximum-speed activation hierarchy remain staged work.

This audit combines current code/data inspection with official NDW, PDOK, RWS
and OSM documentation. It does not authorize source/schema removal without the
shadow and rollback gates below.

## Recommended product source set

```text
Local OSM PBF directed graph
+ NDW measurement_current + trafficspeed
+ NDW actueel_beeld
+ NDW MSI XML + MSI shapefile
+ NDW DRIP
+ selected WEGGEG layers
+ compact VILD fallback
+ targeted traffic-sign bootstrap/events
```

There is no better free/open replacement for NDW live speed, MSI, DRIP or live
situations. OSM remains road/topology authority; NDW remains live observation
authority. WEGGEG and VILD are evidence/enrichment, never independent overrides
of an ambiguous OSM direction.

## Field/source decisions

| Source | Decision | Underused or missing fields | Action |
|---|---|---|---|
| OSM PBF | Keep + extend | `maxspeed:conditional`, `destination:ref:lanes`, `placement`, `width:lanes`, shoulder/access lane tags | Normalize selectively; fail closed |
| `measurement_current` | Keep + extend first | full OpenLR coordinates/offsets/FRC/FOW/orientation/side, Alert-C table/version/offset, carriageway, computation method, accuracy | Persist typed and use before candidate ranking |
| `trafficspeed` | Keep + quality | `dataError`, supplier quality, computational method, incomplete inputs | Distinguish error, no traffic and valid standstill |
| VILD point/line/TMC | Keep compact | table/version/offset validation | Primary fixed-sensor direction; OpenLR cross-check/fallback |
| Meetlocaties shapefile | Retire | no runtime consumer | Stop feed/ingest; drop schema only later |
| WEGGEG | Keep selected layers | typed begin/end km, `VNRWOL`, `VOLGNRSTRK`, Rijbanen, Convergenties, Divergenties, Maximum snelheid | Prebind to OSM outside request path |
| Full NWB GeoPackage | Replace | only `wvk_id`, hectometrage/crosswalk remain useful | Build compact offline crosswalk, then stop full ingest |
| MSI XML + shapefile | Keep both | `ts_event`, message id, provenance | XML state + shapefile geometry are complementary |
| DRIP | Keep + compact | source carriageway, version/publication metadata | Add carriageway; lazy route-only image payload |
| `actueel_beeld` | Keep + redesign | actual subtype, carriageway/lane scope, bearing, Alert-C/OpenLR, GML itinerary, impact/status/validity | Classify per record; canonical bind before display |
| Separate SRTI/closure/temp-speed | Retire after shadow equality | duplicate records and category overwrite risk | Compare latency/content, then disable |
| `traveltime` | Disable or integrate | not consumed by canonical speed model | Remove from 60s hot path until useful |
| National traffic-sign CSV | Replace | `blackCode`, validation, direction/linear refs underused | Target A1/A2 bootstrap + v4 event cursor |

## P0 correctness backlog

Implemented in migration `0a1b2c3d4e5f` and matcher version
`ndw-osm-v4-vild-primary-direction`:

1. Rebuild situation classification around each record's actual `xsi:type` and
   subtype. Never label all `actueel_beeld` records as incidents.
2. Preserve situation carriageway, point bearing, Alert-C direction/offsets,
   complete GML itinerary, lane impact, operator status, validity and cause.
3. Shadow-deduplicate records shared by `actueel_beeld` and specialized feeds
   using record id, version and provenance before retiring feeds.
4. Parse speed `dataError`, calculated quality, method and incomplete inputs.
   Preserve `no_traffic` separately from `unknown` and valid standstill.
5. Persist full MST OpenLR/Alert-C/carriageway/accuracy metadata and use it as
   bounded candidate evidence. Do not replace fail-closed margin checks.
   For fixed speed sensors, use oriented VILD as the nationally complete
   direction source; use OpenLR as cross-check/fallback and reject conflicts.

## P1 coverage backlog

Items 6–8 are ingested/preserved. Item 10 remains deliberately inactive until
the separate source hierarchy and temporal evaluation are tested end to end;
merely storing a field must never change a displayed maximum speed.

6. Ingest WEGGEG Rijbanen, Convergenties, Divergenties and Maximum snelheid;
   store linear-reference fields typed rather than querying JSON `raw`.
7. Add DRIP carriageway to model, parser and canonical live-object binding.
8. Normalize selected OSM conditional/lane/destination/placement fields.
9. Use targeted current A1/A2 traffic signs plus incremental events as static
   maximum-speed validation, not as a live traffic source.
10. Build one maximum-speed hierarchy: active MSI/lane state, active bound
    SpeedManagement, WEGGEG Rijksweg limit, valid bound sign, OSM lane/directional
    limit, evaluable conditional limit, otherwise unknown.

## P2 resource backlog

Items 11 and 14 are disabled by default. Item 15 is implemented as an opt-in
image payload. Items 12–13 retain their old code/schema behind feature flags
until their stated comparison gates pass.

11. Stop the unused meetlocaties-shapefile ingest (about 80 MB locally).
12. Replace the roughly 1 GB local NWB full ingest with a compact crosswalk.
13. After shadow validation, use `actueel_beeld` instead of duplicate live
    SRTI/closure/temporary-speed polls.
14. Remove `traveltime` from the 60-second hot path until it supplies a proven
    corridor observation.
15. Keep DRIP graphics out of default map/API payload; load only on the confirmed
    route when the driver view needs them.
16. Keep raw Speed Points, WEGGEG, VILD and NWB layers diagnostic-only.

## Official references

- [NDW traffic-data profile](https://docs.ndw.nu/dataformaten/datex2-v3/verkeersgegevens/)
- [NDW situation messages v3](https://docs.ndw.nu/producten/situatieberichten-v3/)
- [NDW road/carriageway/lane management](https://docs.ndw.nu/dataformaten/datex2-v3/elementen/payloadpublication/situationpublication/specialisaties/generiek/roadorcarriagewayorlanemanagement/)
- [NDW location referencing](https://docs.ndw.nu/locatiereferentie/)
- [NDW MSI](https://docs.ndw.nu/producten/msi/)
- [NDW shapefiles](https://docs.ndw.nu/producten/shapefiles/)
- [NDW traffic-sign API v4](https://docs.ndw.nu/data-uitwisseling/interface-beschrijvingen/verkeersborden-api/)
- [PDOK WEGGEG](https://api.pdok.nl/rws/weggegevens/ogc/v1?f=html&lang=nl)
- [PDOK NWB roads](https://api.pdok.nl/rws/nationaal-wegenbestand-wegen/ogc/v1?f=html&lang=nl)
- [RWS WEGGEG user information](https://downloads.rijkswaterstaatdata.nl/weggeg/geogegevens/shapefile/Documentatie/Gebruikersinformatie%20WEGGEG%20v2.5.pdf)

## Required removal gates

Before retiring a feed, run representative motorway, provincial-road,
parallel-carriageway, ramp and tunnel comparisons for at least freshness,
record count, direction/carriageway scope and missing unique records. Keep the
previous feed feature-flagged for rollback until those comparisons pass.
