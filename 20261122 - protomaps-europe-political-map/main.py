#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click>=8.1,<9", "httpx>=0.28,<1", "json5>=0.12,<1", "mapbox-vector-tile>=2.2,<3",
#     "matplotlib>=3.10,<4", "mercantile>=1.2,<2", "numpy>=2,<3",
#     "pmtiles>=3.4,<4", "pyproj>=3.7,<4", "shapely>=2.1,<3", "tqdm>=4.67,<5",
# ]
# ///
"""Render a political Europe map from Protomaps vector tiles, without a browser."""

from __future__ import annotations

import gzip
import hashlib
import json
import mmap
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click
import httpx
import json5
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mercantile
import numpy as np
from mapbox_vector_tile.decoder import TileData as MVTDecoder
from matplotlib.collections import LineCollection
from matplotlib.patches import PathPatch
from matplotlib.path import Path as PlotPath
from pmtiles.reader import Reader
from pmtiles.tile import Compression, TileType
from pyproj import Transformer
from shapely import Geometry, STRtree, make_valid, transform, union_all
from shapely.geometry import GeometryCollection, Polygon, box, shape
from shapely.geometry.polygon import orient
from tqdm import tqdm

HERE = Path(__file__).resolve().parent
DEFAULT_PMTILES = "https://data.source.coop/protomaps/openstreetmap/v4.pmtiles"
NE_REV = "ca96624a56bd078437bca8184e78163e5039ad19"
NE_URL = (f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_REV}"
          "/geojson/ne_10m_admin_0_countries.geojson")
EUROPE_URL = (f"https://raw.githubusercontent.com/nvkelso/natural-earth-vector/{NE_REV}"
              "/geojson/ne_10m_geography_regions_polys.geojson")
IRISH_REV = "b168df19afa9874c139e39365cbc1863f10098f7"
IRISH_BASE = f"https://raw.githubusercontent.com/jonnymccullagh/ireland-counties-geojson/{IRISH_REV}"
COUNTIES = [
    "Antrim", "Armagh", "Carlow", "Cavan", "Clare", "Cork", "Derry", "Donegal",
    "Down", "Dublin", "Fermanagh", "Galway", "Kerry", "Kildare", "Kilkenny", "Laois",
    "Leitrim", "Limerick", "Longford", "Louth", "Mayo", "Meath", "Monaghan", "Offaly",
    "Roscommon", "Sligo", "Tipperary", "Tyrone", "Waterford", "Westmeath", "Wexford", "Wicklow",
]
NI_COUNTIES = {"Antrim", "Armagh", "Derry", "Down", "Fermanagh", "Tyrone"}
GISCO_NUTS3_URL = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2024_4326_LEVL_3.geojson"
GISCO_COPYRIGHT_URL = "https://ec.europa.eu/eurostat/help/copyright-notice"
REGIONAL_NUTS = {"SVN": ("SI", 12), "LVA": ("LV", 5), "MKD": ("MK", 8)}
MOLDOVA_DEVELOPMENT_UNITS = {
    "North": {"MD-BA", "MD-BR", "MD-DO", "MD-DR", "MD-ED", "MD-FA", "MD-FL", "MD-GL", "MD-OC", "MD-RI", "MD-SI", "MD-SO"},
    "Centre": {"MD-AN", "MD-CL", "MD-CR", "MD-DU", "MD-HI", "MD-IA", "MD-NI", "MD-OR", "MD-RE", "MD-ST", "MD-SD", "MD-TE", "MD-UN"},
    "South": {"MD-BS", "MD-CA", "MD-CT", "MD-CS", "MD-CM", "MD-LE", "MD-SV", "MD-TA"},
    "Chișinău": {"MD-CU"},
    "Gagauzia": {"MD-GA"},
    "Transnistria": {"MD-SN", "MD-BD"},
}
REGIONAL_OVERLAYS = {*REGIONAL_NUTS, "MDA"}
PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True).transform
FROM_MERCATOR = Transformer.from_crs("EPSG:3857", "EPSG:3035", always_xy=True).transform


@dataclass(frozen=True)
class Division:
    name: str
    level: int | None  # OSM admin_level, NOT a uniform ADM1/ADM2 convention
    description: str

    @property
    def zoom(self) -> int:
        return 10 if self.level is not None and self.level >= 7 else 8


DIVISIONS = {
    "ALB": Division("Albania", 4, "counties"),
    "AND": Division("Andorra", 7, "parishes"),
    "AUT": Division("Austria", 4, "states"),
    "BEL": Division("Belgium", 6, "provinces"),
    "BGR": Division("Bulgaria", 4, "provinces"),
    "BIH": Division("Bosnia and Herzegovina", 4, "entities and Brčko District"),
    "BLR": Division("Belarus", 4, "regions and Minsk"),
    "CHE": Division("Switzerland", 4, "cantons"),
    "CYP": Division("Cyprus", 4, "districts"),
    "CZE": Division("Czechia", 4, "regions"),
    "DEU": Division("Germany", 4, "Länder"),
    "DNK": Division("Denmark", 4, "regions"),
    "ESP": Division("Spain", 6, "provinces"),
    "EST": Division("Estonia", 6, "counties"),
    "FIN": Division("Finland", 4, "regions (including Åland)"),
    "FRA": Division("France", 6, "départements and Métropole de Lyon"),
    "GBR": Division("United Kingdom", 6, "countries; modern counties/council areas/unitary authorities; traditional NI counties"),
    "GRC": Division("Greece", 5, "13 regions and Mount Athos"),
    "HRV": Division("Croatia", 4, "counties and Zagreb"),
    "HUN": Division("Hungary", 6, "counties and Budapest"),
    "IRL": Division("Ireland", None, "26 traditional counties; six more in Northern Ireland"),
    "ISL": Division("Iceland", 5, "8 traditional regions"),
    "ITA": Division("Italy", 4, "regions"),
    "KOS": Division("Kosovo", 4, "7 districts"),
    "LIE": Division("Liechtenstein", 8, "municipalities"),
    "LTU": Division("Lithuania", 4, "10 traditional counties"),
    "LUX": Division("Luxembourg", 6, "cantons"),
    "LVA": Division("Latvia", None, "5 planning regions (2024 NUTS 3)"),
    "MCO": Division("Monaco", None, "quartiers (supplementary geoBoundaries ADM2 snapshot)"),
    "MDA": Division("Moldova", None, "6 development regions; Chișinău, Gagauzia and Transnistria distinct"),
    "MKD": Division("North Macedonia", None, "8 statistical/planning regions"),
    "MLT": Division("Malta", 7, "regions"),
    "MNE": Division("Montenegro", 6, "municipalities"),
    "NLD": Division("Netherlands", 4, "provinces"),
    "NOR": Division("Norway", 4, "counties"),
    "POL": Division("Poland", 4, "16 voivodeships"),
    "PRT": Division("Portugal", 6, "districts and autonomous island regions"),
    "ROU": Division("Romania", 4, "counties and Bucharest"),
    "RUS": Division("Russia", 4, "federal subjects in the displayed western extent"),
    "SMR": Division("San Marino", 8, "castelli"),
    "SRB": Division("Serbia", 6, "administrative districts"),
    "SVK": Division("Slovakia", 4, "regions"),
    "SVN": Division("Slovenia", None, "12 statistical regions"),
    "SWE": Division("Sweden", 4, "counties"),
    "TUR": Division("Turkey", 4, "provinces"),
    "UKR": Division("Ukraine", 4, "oblasts, Crimea, Kyiv and Sevastopol"),
    "VAT": Division("Vatican City", None, "no administrative subdivisions"),
}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


class Downloads:
    """Cache source documents and retain their URLs for the source manifest."""

    def __init__(self, cache: Path):
        self.cache = cache
        self.client = httpx.Client(timeout=90, follow_redirects=True)
        self.sources: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def document(self, url: str) -> bytes:
        filename = self.cache / "documents" / hashlib.sha256(url.encode()).hexdigest()
        if filename.exists():
            data = filename.read_bytes()
        else:
            for attempt in range(4):
                try:
                    response = self.client.get(url)
                    response.raise_for_status()
                    data = response.content
                    break
                except httpx.HTTPError:
                    if attempt == 3:
                        raise
                    time.sleep(2 ** attempt)
            atomic_write(filename, data)
        with self.lock:
            self.sources[url] = {"url": url, "cache_file": str(filename),
                                 "sha256": hashlib.sha256(data).hexdigest()}
        return data

    def json(self, url: str) -> Any:
        return json.loads(self.document(url))


class RangeSource:
    """Read only requested PMTiles byte ranges; never accidentally fetch the planet."""

    def __init__(self, url: str, cache: Path):
        self.url = url
        self.client = httpx.Client(timeout=90, follow_redirects=True)
        self.locks: dict[tuple[int, int], threading.Lock] = {}
        self.lock = threading.Lock()
        self.memory: dict[tuple[int, int], bytes] = {}
        first, identity = self.request(0, 127)
        if not identity:
            self.client.close()
            raise click.ClickException("Remote PMTiles must supply ETag or Last-Modified for safe caching.")
        self.identity = identity
        self.cache = cache / "ranges" / hashlib.sha256(f"{url}\n{identity}".encode()).hexdigest()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.memory[(0, 127)] = first

    def request(self, offset: int, length: int) -> tuple[bytes, str]:
        for attempt in range(4):
            try:
                with self.client.stream("GET", self.url,
                                        headers={"Range": f"bytes={offset}-{offset + length - 1}",
                                                 "Accept-Encoding": "identity"}) as response:
                    response.raise_for_status()
                    if response.status_code != 206:
                        raise click.ClickException("PMTiles server ignored the Range request; refusing a full-planet download.")
                    prefix = f"bytes {offset}-{offset + length - 1}/"
                    if not response.headers.get("content-range", "").startswith(prefix):
                        raise click.ClickException("PMTiles server returned an incorrect Content-Range.")
                    identity = response.headers.get("etag") or response.headers.get("last-modified", "")
                    if hasattr(self, "identity") and identity != self.identity:
                        raise click.ClickException("Remote archive changed during rendering. Retry with an immutable URL or local cutout.")
                    data = response.read()
                    if len(data) != length:
                        raise click.ClickException("Truncated PMTiles range response.")
                    return data, identity
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def __call__(self, offset: int, length: int) -> bytes:
        key = (offset, length)
        with self.lock:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            if key in self.memory:
                return self.memory[key]
            path = self.cache / f"{offset}-{length}.bin"
            if path.exists():
                data = path.read_bytes()
                if len(data) != length:
                    raise click.ClickException(f"Corrupt cache file: {path}. Remove it and retry.")
            else:
                data, _ = self.request(offset, length)
                atomic_write(path, data)
            # Headers and directories are reused heavily; keeping small chunks avoids
            # disk reads without keeping hundreds of megabytes of tiles in memory.
            if length <= 65536 and len(self.memory) < 2048:
                self.memory[key] = data
            return data


def project(geometry: Geometry) -> Geometry:
    return make_valid(transform(geometry, PROJECT, interleaved=False))


def country_masks(downloads: Downloads, codes: set[str], bounds: Geometry) -> dict[str, Geometry]:
    grouped: dict[str, list[Geometry]] = defaultdict(list)
    europe = None
    if "RUS" in codes:
        europe = union_all([make_valid(shape(f["geometry"]))
                            for f in downloads.json(EUROPE_URL)["features"]
                            if f["properties"].get("NAME") == "EUROPE"])
        if europe.is_empty:
            raise click.ClickException("Natural Earth Europe extent is missing.")
    for feature in downloads.json(NE_URL)["features"]:
        code = feature["properties"]["ADM0_A3"]
        if code == "ALD":  # Natural Earth stores Åland separately from Finland.
            code = "FIN"
        elif code in {"CYN", "CNM", "ESB", "WSB"}:
            # Draw the whole island of Cyprus, including its north, UN buffer
            # zone and British base areas. Political lines still follow OSM.
            code = "CYP"
        if code in codes:
            geometry = make_valid(shape(feature["geometry"])).intersection(bounds)
            if code == "RUS" and europe is not None:
                # Continental Europe follows Natural Earth's geographic definition,
                # rather than an arbitrary longitude through Russia. Arctic islands
                # are outside this mainland definition and the default viewport.
                geometry = geometry.intersection(europe)
            if not geometry.is_empty:
                grouped[code].append(geometry)
    return {code: union_all(parts) for code, parts in grouped.items()}


def irish_counties(downloads: Downloads, workers: int) -> dict[str, Geometry]:
    downloads.document(f"{IRISH_BASE}/LICENSE")
    downloads.document(f"{IRISH_BASE}/README.md")

    def load(name: str) -> tuple[str, Geometry]:
        # A few source files contain trailing commas; JSON5 accepts these without
        # modifying the cached originals or attempting unsafe regex repairs.
        raw = downloads.document(f"{IRISH_BASE}/{name}.geojson")
        try:
            collection = json.loads(raw)
        except json.JSONDecodeError:
            collection = json5.loads(raw)
        parts = [make_valid(shape(f["geometry"])) for f in collection["features"]]
        geometry = union_all(parts)
        if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise click.ClickException(f"Invalid traditional Irish county geometry: {name}")
        if not box(-12, 50, -5, 57).covers(geometry):
            raise click.ClickException(f"Irish county {name} lies outside Ireland.")
        return name, geometry

    with ThreadPoolExecutor(workers) as pool:
        return dict(tqdm(pool.map(load, COUNTIES), total=32, desc="Traditional Irish counties", unit="county"))


def internal_borders(regions: list[Geometry]) -> Geometry:
    """Keep subdivision edges, not an older source's coastline/country perimeter."""
    return union_all([region.boundary for region in regions]).difference(union_all(regions).boundary)


def geoboundaries_features(downloads: Downloads, code: str, level: int,
                          expected: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Pin the release instead of using a mutable 'current' API download link.
    base = f"https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/9469f09/releaseData/gbOpen/{code}/ADM{level}"
    collection = downloads.json(f"{base}/geoBoundaries-{code}-ADM{level}.geojson")
    features = collection["features"]
    if len(features) != expected:
        raise click.ClickException(f"Unexpected {code} ADM{level} unit count: {len(features)}, expected {expected}.")
    names = [f["properties"].get("shapeName", "") for f in features]
    if code == "XKX" and set(names) != {
        "District of Prishtina", "District of Mitrovica", "District of Peja",
        "District of Gjilan", "District of Gjakova", "District of Prizren", "District of Ferizaj",
    }:
        raise click.ClickException("Kosovo supplement must contain the seven districts, not municipalities.")
    metadata = downloads.json(f"{base}/geoBoundaries-{code}-ADM{level}-metaData.json")
    info = {
        "source": "geoBoundaries gbOpen, release 9469f09", "code": code, "level": level,
        "units": expected, "names": names, "metadata": metadata,
        "license": metadata["boundaryLicense"], "year": metadata["boundaryYear"],
    }
    if int(metadata["admUnitCount"]) != expected:
        info["metadata_note"] = "Upstream metadata unit count/canonical level do not match the geometry. Geometry names and unit count were checked explicitly."
    return features, info


def geoboundaries(downloads: Downloads, code: str, level: int, expected: int) -> tuple[Geometry, dict[str, Any]]:
    features, info = geoboundaries_features(downloads, code, level, expected)
    return internal_borders([make_valid(shape(f["geometry"])) for f in features]), info


def statistical_regions(downloads: Downloads, code: str) -> tuple[Geometry, dict[str, Any]]:
    country, expected = REGIONAL_NUTS[code]
    collection = downloads.json(GISCO_NUTS3_URL)
    features = [f for f in collection["features"]
                if f["properties"].get("CNTR_CODE") == country
                and f["properties"].get("LEVL_CODE") == 3]
    identifiers = {f["properties"]["NUTS_ID"] for f in features}
    if len(features) != expected or len(identifiers) != expected:
        raise click.ClickException(f"Expected {expected} distinct regional units for {code}, got {len(features)}.")
    polygons = [make_valid(shape(f["geometry"])) for f in features]
    info = {
        "source": "Eurostat GISCO NUTS/statistical regions 2024, published 1:1 million geometry",
        "url": GISCO_NUTS3_URL, "code": code, "units": expected, "year": 2024,
        "division": DIVISIONS[code].description,
        "names": [f["properties"]["NUTS_NAME"] for f in features],
        "identifiers": sorted(identifiers),
        "license": "Eurostat GISCO reuse conditions, including third-party rights",
        "copyright_notice": GISCO_COPYRIGHT_URL,
        "attribution": "Eurostat GISCO / © EuroGeographics for the administrative boundaries",
        "note": "Statistical/planning regions, not Länder-style regional governments. No further geometric simplification applied.",
    }
    return internal_borders(polygons), info


def moldova_development_regions(downloads: Downloads) -> tuple[Geometry, dict[str, Any]]:
    features, source_info = geoboundaries_features(downloads, "MDA", 1, 37)
    by_iso = {f["properties"]["shapeISO"]: make_valid(shape(f["geometry"])) for f in features}
    memberships = [iso for members in MOLDOVA_DEVELOPMENT_UNITS.values() for iso in members]
    expected = set(memberships)
    if len(memberships) != len(expected) or len(by_iso) != len(features) or set(by_iso) != expected:
        raise click.ClickException("Moldova development grouping must account for all 37 source units exactly once.")
    regions = [union_all([by_iso[iso] for iso in sorted(members)])
               for members in MOLDOVA_DEVELOPMENT_UNITS.values()]
    info = {
        "source": "geoBoundaries gbOpen districts dissolved into Moldova's six development regions",
        "code": "MDA", "division": DIVISIONS["MDA"].description, "units": 6,
        "names": list(MOLDOVA_DEVELOPMENT_UNITS), "source_units": 37,
        "membership_iso": {name: sorted(members) for name, members in MOLDOVA_DEVELOPMENT_UNITS.items()},
        "source_metadata": source_info, "license": source_info["license"], "year": source_info["year"],
        "membership_reference": "https://ro.wikipedia.org/w/index.php?title=Regiunile_de_dezvoltare_ale_Republicii_Moldova&oldid=13326517",
        "note": "Development/planning regions. Bender is grouped with Transnistria according to de facto administration; Dubăsari district remains in Centre. This is not an assertion of sovereignty.",
    }
    return internal_borders(regions), info


def tiles_for(geometry: Geometry, zoom: int) -> set[mercantile.Tile]:
    return {tile for tile in mercantile.tiles(*geometry.bounds, zooms=zoom)
            if geometry.intersects(box(*mercantile.bounds(tile)))}


@dataclass
class TileData:
    land: list[Geometry]
    boundaries: dict[tuple[int, bool, int], list[Geometry]]
    coastline: list[Geometry] = field(default_factory=list)


def read_tile(reader: Reader, tile: mercantile.Tile, compression: Compression, land: bool) -> TileData:
    data = reader.get(tile.z, tile.x, tile.y)
    if data is None:
        return TileData([], {})
    if compression == Compression.GZIP:
        data = gzip.decompress(data)
    elif compression != Compression.NONE:
        raise click.ClickException(f"Unsupported tile compression: {compression}. Use a gzip Protomaps archive.")
    # Filter the parsed protobuf before geometry decoding. Roads/buildings/POIs
    # account for most of a tile, but must not be rendered or decoded here.
    decoder = MVTDecoder(data)
    wanted = {"earth", "boundaries"} if land else {"boundaries"}
    for index in reversed(range(len(decoder.tile.layers))):
        if decoder.tile.layers[index].name not in wanted:
            del decoder.tile.layers[index]
    layers = decoder.get_message()  # Tile coordinates with Y pointing UP.
    tile_bounds = mercantile.xy_bounds(tile)
    span = tile_bounds.right - tile_bounds.left
    tile_cell = box(*tile_bounds)
    result = TileData([], defaultdict(list))
    for name in (["earth", "boundaries"] if land else ["boundaries"]):
        layer = layers.get(name)
        if layer is None:
            continue
        extent = layer["extent"]

        def mercator(x: Any, y: Any, z: Any = None, extent: int = extent) -> tuple[Any, Any]:
            return tile_bounds.left + np.asarray(x) * span / extent, tile_bounds.bottom + np.asarray(y) * span / extent

        for feature in layer["features"]:
            props = feature["properties"]
            if name == "boundaries":
                level = props.get("kind_detail")
                if level is None:
                    raise click.ClickException("Boundary tile lacks kind_detail; use the Protomaps v4 schema.")
                level = int(level)
            geometry = make_valid(shape(feature["geometry"]))
            geometry = transform(geometry, mercator, interleaved=False)
            if name == "earth":
                # Extract true coastlines BEFORE clipping/reprojection. Stroking
                # the final land union also strokes microscopic projection gaps
                # along tile edges, producing a spurious grid across countries.
                source_land = union_all(list(polygons(geometry)))
                if not source_land.is_empty:
                    coast = source_land.boundary.intersection(tile_cell)
                    coast = coast.difference(tile_cell.boundary.buffer(span / extent / 4))
                    if not coast.is_empty:
                        result.coastline.append(transform(coast.segmentize(span / 32), FROM_MERCATOR, interleaved=False))
            geometry = geometry.intersection(tile_cell)
            if geometry.is_empty:
                continue
            # Densify straight tile-edge segments before the non-linear projection;
            # otherwise projected land tiles can leave visible seams.
            geometry = make_valid(transform(geometry.segmentize(span / 32), FROM_MERCATOR, interleaved=False))
            if name == "earth":
                # Tile clipping can leave degenerate line fragments; these must
                # not turn the land union into a GeometryCollection (which has
                # no well-defined boundary).
                result.land.extend(polygons(geometry))
            else:
                result.boundaries[(level, bool(props.get("disputed", False)), tile.z)].append(geometry)
    return result


def collect_tiles(reader: Reader, jobs: set[mercantile.Tile], compression: Compression,
                  land: bool, workers: int, description: str) -> TileData:
    combined = TileData([], defaultdict(list))
    ordered = sorted(jobs, key=lambda t: (t.z, t.x, t.y))

    def load(tile: mercantile.Tile) -> TileData:
        return read_tile(reader, tile, compression, land)

    with ThreadPoolExecutor(workers) as pool:
        for result in tqdm(pool.map(load, ordered), total=len(ordered), desc=description, unit="tile"):
            combined.land.extend(result.land)
            combined.coastline.extend(result.coastline)
            for key, geometries in result.boundaries.items():
                combined.boundaries[key].extend(geometries)
    return combined


def polygons(geometry: Geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygons(part)


def lines(geometry: Geometry):
    if geometry.geom_type in {"LineString", "LinearRing"}:
        yield np.asarray(geometry.coords)[:, :2]
    elif geometry.geom_type in {"Polygon", "MultiPolygon"}:
        yield from lines(geometry.boundary)
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from lines(part)


def polygon_path(polygon: Polygon) -> PlotPath:
    # Opposite ring orientations preserve holes in both the PNG and SVG backends.
    polygon = orient(polygon, sign=1)
    vertices, codes = [], []
    for ring in [polygon.exterior, *polygon.interiors]:
        points = np.asarray(ring.coords)[:, :2]
        vertices.extend(points)
        codes.extend([PlotPath.MOVETO, *([PlotPath.LINETO] * (len(points) - 2)), PlotPath.CLOSEPOLY])
    return PlotPath(np.asarray(vertices), codes)


def add_lines(ax: Any, geometry: Geometry, color: str, width: float, zorder: int,
              dashed: bool = False) -> None:
    segments = list(lines(geometry))
    if segments:
        collection = LineCollection(segments, colors=color, linewidths=width,
                                    linestyles="dashed" if dashed else "solid", zorder=zorder)
        collection.set_capstyle("round")
        ax.add_collection(collection)


def render(land: Geometry, coastline: Geometry, admin: Geometry, countries: Geometry, disputed: Geometry,
           uk: Geometry, masks: dict[str, Geometry], output: Path, width: int, height: int,
           labels: bool, land_color: str, sea_color: str, admin_width: float,
           country_width: float, tolerance: float) -> None:
    matplotlib.rcParams["svg.fonttype"] = "none"  # Editable text, not embedded raster images.
    fig = plt.figure(figsize=(width / 100, height / 100), dpi=100, facecolor=sea_color)
    ax = fig.add_axes((0.015, 0.045, 0.97, 0.94), facecolor=sea_color)
    ax.set_aspect("equal")
    ax.set_axis_off()
    xmin, ymin, xmax, ymax = union_all(list(masks.values())).bounds
    padding = max(xmax - xmin, ymax - ymin) * 0.025
    ax.set_xlim(xmin - padding, xmax + padding)
    ax.set_ylim(ymin - padding, ymax + padding)
    for polygon in polygons(land.simplify(tolerance, preserve_topology=True)):
        ax.add_patch(PathPatch(polygon_path(polygon), facecolor=land_color, edgecolor="none", zorder=1))
    add_lines(ax, coastline.simplify(tolerance), "#858585", 0.35, 2)
    add_lines(ax, admin.simplify(tolerance), "#777777", admin_width, 3)
    add_lines(ax, uk.simplify(tolerance), "#383838", (admin_width + country_width) / 2, 4)
    add_lines(ax, countries.simplify(tolerance), "#252525", country_width, 5)
    add_lines(ax, disputed.simplify(tolerance), "#252525", country_width, 6, dashed=True)
    if labels:
        # Label the largest visible part, not distant islands/overseas possessions.
        placed = []
        for code, mask in sorted(masks.items(), key=lambda item: item[1].area, reverse=True):
            largest = max(polygons(mask), key=lambda p: p.area, default=None)
            if largest is None:
                continue
            point = largest.representative_point()
            # Small countries are still drawn, but omit crowded labels at this scale.
            if any(point.distance(other) < 105000 for other in placed):
                continue
            placed.append(point)
            ax.text(point.x, point.y, DIVISIONS[code].name, fontsize=7.5, color="#303030",
                    ha="center", va="center", zorder=10,
                    bbox={"facecolor": land_color, "edgecolor": "none", "alpha": 0.8, "pad": 1})
    fig.text(0.02, 0.017,
             "Protomaps · © OpenStreetMap contributors — openstreetmap.org/copyright (ODbL) · Natural Earth · Irish counties: J. McCullagh (GPL-3.0)\n"
             "Supplementary boundaries: geoBoundaries / OSM (ODbL / CC BY-SA 2.0) · Eurostat GISCO / © EuroGeographics · Source details in accompanying JSON",
             fontsize=6, color="#555555", va="bottom")
    output.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".png", ".svg"):
        path = output.with_suffix(suffix)
        fig.savefig(path, dpi=100, facecolor=sea_color)
        click.echo(f"Wrote {path}")
    plt.close(fig)


def prepare_boundaries(data: TileData, masks: dict[str, Geometry], levels: dict[str, Division],
                       ni: Geometry, land: Geometry, land_mask: Geometry | None = None) -> tuple[Geometry, Geometry, Geometry, Geometry, dict[str, Any]]:
    # A small tolerance accommodates differences between NE country masks and OSM.
    # Exact political lines still come from Protomaps, not Natural Earth's masks.
    display_mask = union_all(list(masks.values())).buffer(5000)
    if land_mask is None:
        land_mask = land.buffer(500)
    admin_parts, country_parts, disputed_parts, uk_parts = [], [], [], []
    report = {}
    indexed = {}
    for key, parts in data.boundaries.items():
        indexed[key] = (parts, STRtree(parts))
    for (level, is_disputed, zoom), parts in data.boundaries.items():
        if level == 2:
            target = disputed_parts if is_disputed else country_parts
            target.append(union_all(parts).intersection(display_mask).intersection(land_mask))
        elif level == 4 and zoom == DIVISIONS["GBR"].zoom and "GBR" in masks:
            # Keep constituent-country borders even when the user replaces UK
            # administrative data with a custom overlay.
            uk_parts.append(union_all(parts).intersection(masks["GBR"].buffer(5000)).intersection(land_mask))
    for code, mask in tqdm(masks.items(), desc="Selecting country-specific levels", unit="country"):
        division = levels[code]
        if division.level is None:
            continue
        local_mask = mask.buffer(5000)
        if code == "GBR" and not ni.is_empty:
            local_mask = local_mask.difference(ni.buffer(1000))
        local_land_mask = land_mask.intersection(local_mask)
        local_parts, exact_parts = [], []
        for (level, _is_disputed, zoom), (parts, tree) in indexed.items():
            if zoom != division.zoom or not 2 < level <= division.level:
                continue
            matches = tree.query(local_mask, predicate="intersects")
            if len(matches) == 0:
                continue
            geometry = union_all([parts[int(i)] for i in matches]).intersection(local_land_mask)
            local_parts.append(geometry)
            if level == division.level:
                exact_parts.append(geometry)
        geometry = union_all(local_parts)
        admin_parts.append(geometry)
        report[code] = {"division": division.description, "osm_admin_level": division.level,
                        "line_length_km": round(geometry.length / 1000, 2),
                        "target_level_length_km": round(union_all(exact_parts).length / 1000, 2)}
    return (union_all(admin_parts), union_all(country_parts), union_all(disputed_parts),
            union_all(uk_parts), report)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--pmtiles", "source", default=DEFAULT_PMTILES, show_default=True,
              help="Protomaps v4 .pmtiles file or HTTP Range-capable URL; local cutouts are recommended.")
@click.option("--output", type=click.Path(path_type=Path), default=HERE / "output" / "europe", show_default=True,
              help="Output stem; creates .png, .svg and .sources.json.")
@click.option("--cache", type=click.Path(path_type=Path), default=HERE / ".cache", show_default=True)
@click.option("--bounds", nargs=4, type=float, default=(-32, 27, 68, 72), show_default=True,
              help="West south east north, in longitude/latitude. Russia is restricted to Natural Earth's continental Europe extent.")
@click.option("--countries", "country_codes", help="Optional comma-separated ISO3 subset (Kosovo: KOS). Defaults to all configured countries.")
@click.option("--width", type=click.IntRange(200, 20000), default=3200, show_default=True)
@click.option("--height", type=click.IntRange(200, 20000), default=2600, show_default=True)
@click.option("--workers", type=click.IntRange(1, 16), default=6, show_default=True)
@click.option("--labels/--no-labels", default=True, show_default=True)
@click.option("--land-color", default="#f0eee9", show_default=True)
@click.option("--sea-color", default="white", show_default=True)
@click.option("--admin-width", type=click.FloatRange(min=0.01), default=0.35, show_default=True, help="Stroke width in points.")
@click.option("--country-width", type=click.FloatRange(min=0.01), default=1.0, show_default=True, help="Stroke width in points.")
@click.option("--overlay", multiple=True, help="Replace admin boundaries for a country: ISO3=/path/to/WGS84.geojson. Polygons or lines; retain source licensing separately.")
@click.option("--allow-missing", is_flag=True, help="Permit absent target-level boundaries, with warnings recorded in the source manifest.")
@click.option("--list-divisions", is_flag=True, help="Print the per-country division choices and exit.")
def main(source: str, output: Path, cache: Path, bounds: tuple[float, float, float, float],
         country_codes: str | None, width: int, height: int, workers: int, labels: bool,
         land_color: str, sea_color: str, admin_width: float, country_width: float,
         overlay: tuple[str, ...], allow_missing: bool, list_divisions: bool) -> None:
    """Draw country borders and the agreed administrative divisions, with no physical detail.

    Azores, Madeira and the Canary Islands are included in the default bounds.
    Georgia, Armenia and Azerbaijan (and other non-selected countries) are omitted.
    No terrain, inland water, transport features or city labels are rendered.
    """
    if list_divisions:
        for code, division in sorted(DIVISIONS.items()):
            click.echo(f"{code}  {division.name:25} {division.description}")
        return
    west, south, east, north = bounds
    if not (-180 <= west < east <= 180 and -85 < south < north < 85):
        raise click.BadParameter("Need west < east and south < north, within Web Mercator limits.", param_hint="--bounds")
    codes = set(DIVISIONS) if not country_codes else {c.strip().upper() for c in country_codes.split(",")}
    if unknown := codes - DIVISIONS.keys():
        raise click.BadParameter(f"Unknown codes: {', '.join(sorted(unknown))}", param_hint="--countries")
    custom = {}
    for value in overlay:
        code, separator, filename = value.partition("=")
        code = code.upper()
        if not separator or code not in codes:
            raise click.BadParameter("Use --overlay SELECTED_ISO3=/path/to/file.geojson", param_hint="--overlay")
        custom[code] = Path(filename)
    output = output.resolve()
    if output.suffix in {".png", ".svg", ".json"}:
        output = output.with_suffix("")
    with ExitStack() as stack:
        downloads = Downloads(cache)
        stack.callback(downloads.client.close)
        click.echo("Loading country masks (Natural Earth) …")
        geographic_bounds = box(*bounds)
        geographic_masks = country_masks(downloads, codes, geographic_bounds)
        if not geographic_masks:
            raise click.ClickException("No selected countries intersect the requested bounds.")
        masks = {code: project(mask) for code, mask in geographic_masks.items()}
        ni = GeometryCollection()
        irish = {}
        if (codes & {"IRL", "GBR"}) and geographic_bounds.intersects(box(-11, 51, -5, 56)):
            irish = irish_counties(downloads, workers)
            ni = project(union_all([irish[name] for name in NI_COUNTIES]))
        if source.startswith(("https://", "http://")):
            range_source = RangeSource(source, cache)
            stack.callback(range_source.client.close)
            get_bytes: Callable[[int, int], bytes] = range_source
            archive_identity = range_source.identity
        else:
            path = Path(source).expanduser().resolve()
            file = stack.enter_context(path.open("rb"))
            mapping = stack.enter_context(mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ))
            get_bytes = lambda offset, length: mapping[offset:offset + length]
            archive_identity = {"path": str(path), "size": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}
        reader = Reader(get_bytes)
        header = reader.header()
        if header["tile_type"] != TileType.MVT:
            raise click.ClickException("Expected an MVT vector-tile PMTiles archive, not raster tiles.")
        metadata = reader.metadata()
        layer_names = {layer["id"] for layer in metadata.get("vector_layers", [])}
        if not {"earth", "boundaries"} <= layer_names:
            raise click.ClickException("Archive does not advertise Protomaps earth and boundaries layers.")
        levels = {code: Division(d.name, None if code in custom else d.level, d.description)
                  for code in masks for d in [DIVISIONS[code]]}
        # z8 is essential for départements/counties; z10 for levels 7/8.
        # Do not use the display-scale z4/z5 tiles: they lack these subdivisions.
        jobs_by_zoom: dict[int, set[mercantile.Tile]] = defaultdict(set)
        for code, mask in geographic_masks.items():
            if levels[code].level is not None:
                jobs_by_zoom[levels[code].zoom].update(tiles_for(mask.buffer(0.05), levels[code].zoom))
            elif code == "GBR":
                jobs_by_zoom[8].update(tiles_for(mask.buffer(0.05), 8))
        required_max = max([6, *jobs_by_zoom])
        if header["max_zoom"] < required_max or header["min_zoom"] > 6:
            raise click.ClickException(f"Archive must include z6 through z{required_max}; this archive has z{header['min_zoom']}–z{header['max_zoom']}.")
        all_geographic_land = union_all(list(geographic_masks.values()))
        base_jobs = tiles_for(all_geographic_land.buffer(0.05), 6)
        click.echo(f"Reading {len(base_jobs)} coastline tiles and {sum(map(len, jobs_by_zoom.values()))} administrative tiles.")
        base = collect_tiles(reader, base_jobs, header["tile_compression"], True, workers, "Protomaps coastlines")
        data = TileData([], defaultdict(list))
        # Keep z6 country borders separately; they cover custom-overlay countries too.
        for key, parts in base.boundaries.items():
            if key[0] == 2:
                data.boundaries[key].extend(parts)
        for zoom, jobs in sorted(jobs_by_zoom.items()):
            batch = collect_tiles(reader, jobs, header["tile_compression"], False, workers, f"Protomaps borders z{zoom}")
            for key, parts in batch.boundaries.items():
                # Avoid mixing different simplifications of the same country boundary.
                if key[0] != 2:
                    data.boundaries[key].extend(parts)
        click.echo("Merging coastlines and selecting borders …")
        display_mask = union_all(list(masks.values())).buffer(5000).intersection(project(geographic_bounds.segmentize(0.25)))
        unclipped_land = union_all(base.land)
        land = unclipped_land.intersection(display_mask)
        coastline = union_all(base.coastline).intersection(display_mask)
        if land.is_empty:
            raise click.ClickException("No Protomaps land features found for the selected extent.")
        # Buffer the detailed continent-wide coastline once, not separately for
        # every supplementary country overlay.
        land_mask = land.buffer(500)
        admin, countries, disputed, uk, report = prepare_boundaries(data, masks, levels, ni, land, land_mask)
        supplemental = []
        extra = []
        regional_codes = REGIONAL_OVERLAYS.intersection(masks).difference(custom)
        # Neighbouring countries' buffered masks must not reintroduce municipal
        # or district lines inside countries now rendered with regional overlays.
        if regional_codes:
            admin = admin.difference(union_all([masks[code] for code in sorted(regional_codes)]))
        for code in tqdm(sorted(regional_codes), desc="Regional divisions", unit="country"):
            if code == "MDA":
                geometry, info = moldova_development_regions(downloads)
            else:
                geometry, info = statistical_regions(downloads, code)
            extra.append(project(geometry).intersection(masks[code].buffer(5000)).intersection(land_mask))
            supplemental.append(info)
            report[code] = info
        if "POL" in report:
            report["POL"]["configured_units"] = 16
        if irish:
            chosen = [g for name, g in irish.items()
                      if (name in NI_COUNTIES and "GBR" in masks and "GBR" not in custom)
                      or (name not in NI_COUNTIES and "IRL" in masks and "IRL" not in custom)]
            if chosen:
                extra.append(project(internal_borders(chosen)).intersection(land_mask))
            supplemental.append({"source": IRISH_BASE, "units_loaded": 32, "license": "GPL-3.0",
                                 "note": "Traditional county mapping-task boundaries; not a legal boundary authority."})
            if "IRL" in masks and "IRL" not in custom:
                report["IRL"] = {"division": DIVISIONS["IRL"].description, "source": "traditional county overlay", "units": 26}
            if "GBR" in masks and "GBR" not in custom and masks["GBR"].difference(ni.buffer(5000)).area < 1:
                report["GBR"] = {"division": "traditional Northern Irish counties", "source": "traditional county overlay", "units": 6}
        if "MCO" in masks and "MCO" not in custom:
            geometry, info = geoboundaries(downloads, "MCO", 2, 9)
            extra.append(project(geometry).intersection(masks["MCO"].buffer(5000)).intersection(land_mask))
            supplemental.append(info)
            report["MCO"] = info
        # Some first-level divisions are not present as administrative relations
        # in Protomaps. Use vetted, pinned supplements rather than finer units.
        for code, data_code, level, count in [
            ("ISL", "ISL", 1, 8), ("CYP", "CYP", 1, 6), ("KOS", "XKX", 1, 7),
        ]:
            if code in report and report[code].get("target_level_length_km", 0) == 0:
                geometry, info = geoboundaries(downloads, data_code, level, count)
                extra.append(project(geometry).intersection(masks[code].buffer(5000)).intersection(land_mask))
                supplemental.append(info)
                report[code] = info
        for code, path in custom.items():
            if code not in masks:
                continue
            collection = json.loads(path.read_text())
            if collection.get("type") != "FeatureCollection":
                raise click.ClickException(f"Overlay must be a WGS84 GeoJSON FeatureCollection: {path}")
            geometries = [make_valid(shape(f["geometry"])) for f in collection["features"] if f.get("geometry")]
            geometry = union_all([g.boundary if g.geom_type in {"Polygon", "MultiPolygon"} else g for g in geometries])
            projected = project(geometry).intersection(masks[code].buffer(5000)).intersection(land_mask)
            if projected.is_empty or projected.length == 0:
                raise click.ClickException(f"Overlay {path} contains no boundary lines in the displayed {code} extent.")
            extra.append(projected)
            report[code] = {"source": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                            "note": "User-supplied overlay; source licensing must be retained by the user."}
        warnings = []
        for code, entry in report.items():
            if entry.get("target_level_length_km") == 0:
                warnings.append(f"{code}: no OSM level {levels[code].level} lines detected for {levels[code].description} in this extent.")
        if "VAT" in masks:
            report["VAT"] = {"division": "none"}
        manifest = {
            "pmtiles": {"source": source, "identity": archive_identity, "metadata": metadata},
            "bounds_wgs84": bounds, "projection": "EPSG:3035 (Europe Lambert azimuthal equal-area)",
            "dimensions": [width, height], "administrative_divisions": report,
            "supplemental_sources": supplemental, "downloads": list(downloads.sources.values()),
            "warnings": warnings,
            "limitations": [
                "Coverage checks detect absent levels, not missing individual divisions or outdated boundaries.",
                "Country selection masks use Natural Earth 10m with a 5 km tolerance; boundary lines use Protomaps/OSM.",
                "Russian mainland extent follows Natural Earth's geographic Europe polygon, with a 5 km selection tolerance; Arctic Russian islands are not included.",
                "Default viewport includes Nordic mainland, Iceland and Atlantic islands, but not Svalbard or other high Arctic islands.",
                "Disputed country lines follow Protomaps/OSM and are dashed, not an assertion of sovereignty.",
                "Historic Irish overlays are community mapping boundaries, not legally authoritative.",
                "Monaco supplement is a 2017 snapshot, not a current cadastral map.",
                "Slovenia, Latvia, North Macedonia and Moldova use statistical/planning/development regions, not regional governments equivalent to German Länder.",
            ],
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = output.with_suffix(".sources.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        for warning in warnings:
            click.echo(f"WARNING: {warning}", err=True)
        if warnings and not allow_missing:
            raise click.ClickException(f"Missing target-level data: see {manifest_path}. Supply --overlay ISO3=file.geojson, choose a newer archive, or explicitly use --allow-missing.")
        # Preserve source geometry, including details visible when zooming the SVG.
        # Zero tolerance removes only exactly collinear redundant vertices; no
        # display-scale approximation is applied to make rendering faster.
        tolerance = 0.0
        render(land, coastline, union_all([admin, *extra]), countries, disputed, uk, masks, output,
               width, height, labels, land_color, sea_color, admin_width, country_width, tolerance)
        click.echo(f"Wrote {manifest_path}")


if __name__ == "__main__":
    try:
        main()
    except (httpx.HTTPError, OSError, ValueError) as error:
        click.echo(f"Error: {error}", err=True)
        raise SystemExit(1) from error
