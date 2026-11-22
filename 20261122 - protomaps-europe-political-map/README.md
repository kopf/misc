# Protomaps political map of Europe

A self-contained Python script producing a **PNG and genuinely vector SVG** of
country borders and country-specific administrative divisions. It uses
Protomaps v4 PMTiles directly, projects the geometry to Europe Lambert azimuthal
equal-area (EPSG:3035), and renders it with Matplotlib. No browser, API key,
Node.js, or full-planet download is required. Source boundaries are not simplified
to the PNG's display resolution: the SVG retains the source detail, and
supplementary boundaries use the full-resolution GeoJSON, not simplified downloads.
Only internal subdivision edges are overlaid from those supplements: their older
coastlines and country perimeters are not mistaken for additional political lines.

Only land/sea, coastlines, political boundaries and optional country labels are
rendered. There are **no mountains, rivers, lakes, roads, cities, or landcover**.

## Run

Install [uv](https://docs.astral.sh/uv/) if necessary, then:

```bash
cd "20261122 - protomaps-europe-political-map"
./main.py
```

`uv` obtains the script's inline dependencies automatically. Outputs:

- `output/europe.png` — 3200 × 2600 pixels by default.
- `output/europe.svg` — vector paths and editable text, not an embedded PNG.
- `output/europe.sources.json` — archive identity, source URLs and hashes,
  division choices, coverage diagnostics, supplementary data and limitations.

The first run downloads only the necessary ranges of the Source Cooperative
Protomaps archive, plus source documents. Expect a few minutes and potentially
hundreds of MB, depending on the archive. The full export can use around 1 GB
of RAM while merging detailed geometry. Download and country-selection loops
show progress bars. Subsequent runs reuse `.cache/`. Both generated files and
the cache are ignored by Git.

The default archive is the Protomaps v4 mirror published on Source Cooperative
on 31 March 2026. “Modern counties” describes the administrative system rather
than guaranteeing that this snapshot contains every subsequent reorganisation.
For newer data, pass a newer Protomaps v4 archive or a local cutout.

## Options

```bash
# Larger export, without country labels
./main.py --width 6000 --height 4800 --no-labels

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
**6 through 10** for the full map. Restricting an extract to the map's visual
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

Small states and fine subdivisions remain present in the SVG, but may be hard
to distinguish at continental scale. Some crowded country labels are omitted;
no administrative-unit names are drawn. Increase the resolution or render a
regional subset to inspect fine detail.

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

Retain your overlay's source and licensing separately. `--allow-missing` permits
known omissions explicitly and records warnings; it is not the default.

## Tests

```bash
./test_main.py
```

The offline tests exercise division choices, tile-coordinate orientation,
polygon holes, zoom/level filtering, safe HTTP range handling, CLI validation,
and an end-to-end synthetic PMTiles export. The export test checks PNG dimensions
and confirms that the SVG contains paths and no raster image element.
