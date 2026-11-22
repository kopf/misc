# Protomaps political map of Europe

A self-contained Python script producing a **PNG and genuinely vector SVG** of
country borders and country-specific administrative divisions, with **country
and regional names**. It uses
Protomaps v4 PMTiles directly, projects the geometry to Europe Lambert azimuthal
equal-area (EPSG:3035), and renders it with Matplotlib. No browser, API key,
Node.js, or full-planet download is required. Source boundaries are not simplified
to the PNG's display resolution: the SVG retains the source detail, and
supplementary boundaries use the full-resolution GeoJSON, not simplified downloads.
Only internal subdivision edges are overlaid from those supplements: their older
coastlines and country perimeters are not mistaken for additional political lines.

Only land/sea, coastlines, political boundaries and optional administrative labels
are rendered. There are **no mountains, rivers, lakes, roads, city layers, or
landcover**. City-state/department names such as Berlin or Paris are included
because they are themselves selected administrative units, not as city markers.

## Run

Install [uv](https://docs.astral.sh/uv/) if necessary, then:

```bash
cd "20261122 - protomaps-europe-political-map"
./main.py
```

`uv` obtains the script's inline dependencies automatically. Outputs:

- `output/europe.png` — **12000 × 9750 pixels** by default, to make room for regional names.
- `output/europe.svg` — vector paths, including outlined label glyphs for consistent
  typography across viewers; not an embedded PNG. `--editable-svg-text` keeps
  text editable instead, but depends on the viewer's fonts.
- `output/europe.sources.json` — archive identity, source URLs and hashes,
  division choices, coverage diagnostics, supplementary data and limitations.

The first run downloads only the necessary ranges of the Source Cooperative
Protomaps archive, plus source documents. Expect a few minutes and potentially
hundreds of MB, depending on the archive. The full export can use around 1 GB
of RAM while merging detailed geometry; the larger labelled export can use
roughly 2–3 GB including the image canvas and label source polygons. Download and country-selection loops
show progress bars. Subsequent runs reuse `.cache/`. Both generated files and
the cache are ignored by Git.

The default archive is the Protomaps v4 mirror published on Source Cooperative
on 31 March 2026. “Modern counties” describes the administrative system rather
than guaranteeing that this snapshot contains every subsequent reorganisation.
For newer data, pass a newer Protomaps v4 archive or a local cutout.

## Options

```bash
# Larger labelled export
./main.py --width 16000 --height 13000

# Country labels only (no regional names/insets)
./main.py --no-region-labels

# No labels at all, restoring a smaller export
./main.py --width 3200 --height 2600 --no-labels

# Adjust text size or use the main map without inset panels
./main.py --region-fontsize 10 --country-fontsize 24
./main.py --no-insets

# A region/subset: use three-letter country codes, KOS for Kosovo
./main.py --countries IRL,GBR --bounds -11 49 3 61 --output output/britain-ireland

# Use a local Protomaps cutout or an immutable URL for a newer v4 build
./main.py --pmtiles europe.pmtiles
./main.py --pmtiles https://your-storage.example/europe.pmtiles

# Change styling: line widths are in points
./main.py --land-color '#ffffff' --sea-color '#e9eef3' \
  --country-width 1.2 --admin-width 0.45

# Inspect every country's configured division level
./main.py --list-divisions

./main.py --help
```

A local archive must contain the `earth` and `boundaries` MVT layers and zooms
**6 through 12** for the default labelled map with detailed inset coastlines
(z12 for Monaco/Malta, z10 for the other small-state insets). With `--no-insets`,
zoom levels **6 through 10** suffice. Restricting an extract to the map's visual
zoom (roughly 4–5) would silently lose counties and départements. This script
explicitly reads z8 boundaries for OSM admin levels 5/6 and z10 for levels 7/8,
regardless of the output image size. HTTP servers must support genuine byte
range requests; the script refuses a non-ranged response rather than downloading
the approximately 130 GB planet file. ETags/Last-Modified protect the cache
against archive changes. It does not hotlink the Protomaps daily-build service
by default.

## Geographic extent

The default viewport is **32°W–68°E, 27°N–72°N**. It includes Iceland, the entire
Nordic mainland, the whole island of Cyprus (including the north, buffer zone
and British base areas), all of Turkey, the Azores, Madeira and the Canary Islands. Russia's mainland is clipped to **Natural Earth's geographic Europe
polygon**, not simply to an arbitrary meridian. Georgia, Armenia and Azerbaijan
are excluded, as are other non-selected countries.

High Arctic islands such as Svalbard are outside the default viewport. Russian
Arctic islands are outside the script's mainland-Europe definition. Extent is
configurable with `--bounds`; changing bounds does not remove the Russia mask.
Overseas territories outside the viewport are not shown. Åland is included with
Finland. Non-sovereign territories stored separately by Natural Earth (e.g.
Jersey, Guernsey and the Faroe Islands) are not separate configured countries.

## Label legibility

Country names are larger, bold and uppercase; regional names are smaller and
use the source's English name when available, otherwise its local name. The
script measures text at the final output size and checks **padded label
rectangles for overlap**. Names are wrapped where necessary. Displaced names
have thin leader lines and dots locating the relevant administrative area—not
cities. No label is silently dropped to make the map fit: an impossible layout
fails with a request to increase `--width`/`--height`.

**Luxembourg, Andorra, Liechtenstein, Monaco, San Marino and Malta** have regional
labels in dedicated inset panels rather than unreadable text on continental-scale
areas. Their country names remain on the main map. Inset geography uses more
detailed Protomaps tiles rather than magnifying the main map's z6 outline.

The default 9 pt regional labels are about 12.5 pixels high at native PNG size.
View the PNG at full resolution or zoom into the SVG; fitting a 12000-pixel-wide
image into a small window inevitably makes those labels too small to read.
SVG glyph outlines ensure that the font metrics used for overlap checking do
not change on machines without DejaVu Sans. Label strings, placements and panel
identifiers are also recorded in the source JSON. If you want editable text,
use `--editable-svg-text` and ensure DejaVu Sans is installed in your viewer.

## Agreed administrative divisions

| Country | Divisions |
|---|---|
| Ireland / Northern Ireland | All 32 traditional counties; no modern council subdivisions in NI |
| Great Britain | England/Scotland/Wales borders, plus modern counties, council areas and unitary authorities |
| France | Départements; Métropole de Lyon is a department-equivalent unit |
| Germany | Länder |
| Spain | Provinces |
| Italy | Regions |
| Belgium | Provinces, with Brussels as a province-equivalent area |
| Portugal | Districts and autonomous island regions |
| Poland | 16 voivodeships |
| Slovenia | 12 statistical regions |
| North Macedonia | 8 statistical/planning regions |
| Latvia | 5 planning regions (2024 NUTS 3) |
| Moldova | 6 development regions; Chișinău, Gagauzia and Transnistria remain distinct |
| Switzerland | Cantons |
| Austria | States |
| Greece | 13 regions, plus the special-status Mount Athos area |
| Lithuania | 10 traditional counties |
| Bosnia and Herzegovina | Two entities and Brčko District; no Federation cantons |
| Serbia | Administrative districts |
| Kosovo | 7 districts |
| Iceland | 8 traditional regions |
| Other countries | First-level divisions as listed by `--list-divisions` |

Protomaps stores the **minimum OSM admin level of each shared boundary segment**.
Consequently, the script includes shallower levels that form part of the
selected divisions' edges, not just segments tagged with exactly the target
level. UK country borders get an intermediate stroke weight. Level selection is
country-specific; there is no blanket “ADM1” or “ADM2” assumption.

Only Poland, Slovenia, North Macedonia, Latvia and Moldova were changed to the
coarser regional choices above. Every other country's agreed level—including
all 32 Irish counties, French départements, UK modern counties and Spanish
provinces—remains unchanged. The larger divisions outside Poland are planning
or statistical regions, not Länder-style regional governments.

## Supplementary data, licensing, and limitations

1. **Protomaps v4 / OpenStreetMap** supply the coastlines and most boundary lines.
   Attribution: [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
   ODbL. Country boundaries follow the archive's treatment of disputed areas;
   lines flagged as disputed are dashed. This is not an assertion of sovereignty.
2. **Natural Earth 10m** (public domain) supplies country-selection masks and
   Russia's continental-Europe mask, not the rendered political lines. The
   version is pinned in the script. A 5 km mask tolerance accommodates differences
   between the two datasets; this is not suitable for cadastral/legal use.
3. **Traditional Irish counties** come from
   [Jonny McCullagh's 32-county GeoJSON repository](https://github.com/jonnymccullagh/ireland-counties-geojson),
   pinned to a commit. The repository's GPL-3.0 licence, README and original
   source geometries are retained in the cache and referenced in the manifest.
   These are community mapping-task boundaries, not legally authoritative
   boundaries. Some files contain trailing commas, accepted via a JSON5 fallback.
   When redistributing derived county data, retain the original source/licence
   and review the GPL's source-provision obligations.
4. **Monaco's quartiers** are omitted by Protomaps because their OSM admin level
   exceeds 8. A pinned geoBoundaries gbOpen ADM2 dataset supplies nine quartiers
   from a **2017 snapshot** (ODbL/OpenStreetMap). It does not represent recent
   reclamation or administrative changes. Vetted, pinned fallbacks also supply
   **Cyprus's six de jure districts** (2017), **Kosovo's seven districts** (2021),
   and Iceland's eight traditional regions when absent from Protomaps. Kosovo's
   source metadata declares CC BY-SA 2.0; other supplements declare ODbL. The
   Kosovo metadata misleadingly describes 48 municipalities, but the pinned
   GeoJSON contains exactly the seven named districts: the script checks both
   their count and their names, and records the metadata discrepancy.
5. **Slovenia, Latvia and North Macedonia's regional divisions** use
   [Eurostat GISCO NUTS/statistical regions 2024](https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2024_4326_LEVL_3.geojson),
   at the finest published 1:1 million scale. The script checks the respective
   unit counts (12, 5 and 8) and uses the downloaded geometry without further
   simplification. Latvia's 2024 five NUTS 3 regions match its planning regions;
   the older six-region NUTS scheme is not used. Attribution: Eurostat GISCO /
   © EuroGeographics for the administrative boundaries. GISCO data have their
   own reuse conditions and third-party rights; review the
   [Eurostat copyright notice](https://ec.europa.eu/eurostat/help/copyright-notice)
   before redistribution rather than assuming these data use ODbL.
6. **Moldova's six development regions** are constructed by dissolving the
   full-resolution geoBoundaries district polygons according to the explicitly
   recorded development-region membership. All 37 source units must be accounted
   for exactly once. Bălți is part of North; Chișinău and Gagauzia are distinct;
   Bender is grouped with Transnistria according to its de facto administration,
   while Dubăsari district remains in Centre. Membership and source dates are
   recorded in the manifest. Internal district edges are removed, not simply
   drawn more faintly. The grouping follows the development-region framework,
   not a claim about sovereignty or the existence of six regional governments.

### Sources for regional names

`geolabels.py` supplies label polygons independently of the drawing layers, so
adding text does not replace or alter the agreed political boundaries:

- Natural Earth named administrative polygons are used where their level matches
  the selected divisions. Italian provinces are grouped into the **20 regions**;
  Åland's municipality polygons are grouped into one Finnish autonomous region.
- ONS December 2025 county/unitary-authority polygons supply Great Britain's
  modern names. Metropolitan districts are grouped into their **six metropolitan
  counties**; London boroughs into **Greater London**, while the **City of London**
  remains separate. NI still uses the six traditional Irish county polygons.
  UK constituent-country names are labelled as well. ONS data use OGL v3.0;
  attribution: Contains OS data © Crown copyright and database right 2025.
- Kartverket's official county API supplies Norway's **15 modern counties**,
  avoiding outdated 11- or 19-county name lists.
- The existing GISCO regions supply Slovenia, Latvia and North Macedonia's names;
  Moldova's district polygons are dissolved into the same six development regions
  used for the boundaries. Irish names use the original 32 traditional counties.
- geoBoundaries supplies appropriately scoped names for several special cases.
  Hungary's actual source file contains 19 counties despite metadata saying 20;
  Budapest is added as the distinct capital unit, not as an ordinary city label.
- A single cached OSM/Nominatim administrative-relation lookup supplies Métropole
  de Lyon, Montenegro's newer Tuzi/Zeta/updated Podgorica polygons, and Malta's
  six regional areas. It checks their administrative levels and does not use
  arbitrary city-search results. Source URLs, hashes and licence notes are kept
  in the manifest. Labels use the same snapshot caveats as their source data.

The script **fails by default if no target-level lines are detected for a
country**, writes diagnostics to the manifest, and suggests a custom overlay.
This catches absent levels but **does not establish that every individual unit
is present or current**. A very tight crop entirely within one subdivision can
also have no detectable subdivision lines.

You can replace a country's administrative data with a WGS84 GeoJSON
FeatureCollection containing polygons or lines:

```bash
./main.py --overlay GBR=/path/to/county-boundaries.geojson
```

With regional labels enabled, custom overlay features must also have a
`properties.name` (or `shapeName`/`NUTS_NAME`) and geometry. Polygons allow
interior placement; named lines get callouts. Use `--no-region-labels` for an
unnamed overlay.

Retain your overlay's source and licensing separately. `--allow-missing` permits
known omissions explicitly and records warnings; it is not the default.

## Tests

```bash
./test_main.py
```

The offline tests exercise division choices, tile-coordinate orientation,
polygon holes, zoom/level filtering, safe HTTP range handling, CLI validation,
and an end-to-end synthetic PMTiles export. They also test dense label placement,
explicit failure when labels cannot fit, UK county grouping (not borough labels),
and Italian regional grouping (not province labels). The export test checks PNG
dimensions and confirms that the SVG contains paths and no raster image element.
