"""Named administrative areas and collision-free cartographic text placement.

These polygons locate labels only. Rendered boundaries remain the ones selected
by main.py; names of towns/boroughs at an unselected level are not substituted.
"""

from __future__ import annotations

import math
import re
import textwrap
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlencode

import click
import numpy as np
from matplotlib import patheffects
from matplotlib.transforms import Bbox
from shapely import Geometry, make_valid, union_all
from shapely.geometry import box, shape
from tqdm import tqdm


class Documents(Protocol):
    def json(self, url: str) -> Any: ...


@dataclass(frozen=True)
class NamedRegion:
    country: str
    key: str
    name: str
    geometry: Geometry  # WGS84; used to locate text, not redraw borders
    tier: str = "region"
    designation: str = "region"


NE_COUNTS = {
    "ALB": 12, "AND": 7, "AUT": 9, "BEL": 11, "BGR": 28, "BLR": 7,
    "CHE": 26, "CZE": 14, "DEU": 16, "DNK": 5, "ESP": 52, "EST": 15,
    "GRC": 14, "HRV": 21, "LIE": 11, "LTU": 10, "NLD": 12, "POL": 16,
    "ROU": 42, "RUS": 85, "SMR": 9, "SRB": 25, "SVK": 8, "SWE": 21, "TUR": 81, "UKR": 25,
}
GB_LABEL_LEVELS = {"BIH": (1, 3), "ISL": (1, 8), "KOS": (1, 7),
                   "LUX": (1, 12), "MCO": (2, 9), "CYP": (1, 6)}
INSET_COUNTRIES = ("LUX", "AND", "LIE", "MCO", "SMR", "MLT")
ONS_SERVICE = ("https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
               "Counties_and_Unitary_Authorities_December_2025_Boundaries_UK_BGC/FeatureServer/0")
ONS_URL = ONS_SERVICE + "/query?" + urlencode({
    "where": "1=1", "outFields": "CTYUA25CD,CTYUA25NM", "returnGeometry": "true",
    "outSR": 4326, "f": "geojson", "resultRecordCount": 1000,
})
NORWAY_URL = "https://api.kartverket.no/kommuneinfo/v1/fylker"
OSM_AREAS = {
    4850450: ("FRA", "Métropole de Lyon", 6),
    10141812: ("MNE", "Tuzi", 6), 10147976: ("MNE", "Zeta", 6),
    2319360: ("MNE", "Podgorica", 6),
    19732551: ("MLT", "Eastern Region", 7), 10563355: ("MLT", "Northern Region", 7),
    19732550: ("MLT", "Port Region", 7), 1711219: ("MLT", "Southern Region", 7),
    19732552: ("MLT", "Western Region", 7), 2101121: ("MLT", "Gozo Region", 7),
}
METROPOLITAN_COUNTIES = {
    "Greater Manchester": "Bolton|Bury|Manchester|Oldham|Rochdale|Salford|Stockport|Tameside|Trafford|Wigan",
    "Merseyside": "Knowsley|Liverpool|St. Helens|Sefton|Wirral",
    "South Yorkshire": "Barnsley|Doncaster|Rotherham|Sheffield",
    "Tyne and Wear": "Gateshead|Newcastle upon Tyne|North Tyneside|South Tyneside|Sunderland",
    "West Midlands": "Birmingham|Coventry|Dudley|Sandwell|Solihull|Walsall|Wolverhampton",
    "West Yorkshire": "Bradford|Calderdale|Kirklees|Leeds|Wakefield",
}
MK_NAMES = {"MK001": "Vardar", "MK002": "Eastern", "MK003": "Southwestern",
            "MK004": "Southeastern", "MK005": "Pelagonia", "MK006": "Polog",
            "MK007": "Northeastern", "MK008": "Skopje"}


def normal_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


def parts(geometry: Geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from parts(part)


class RegionCatalogue:
    def __init__(self, downloads: Documents, ne_url: str,
                 gb_features: Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
                 gisco_url: str, nuts: dict[str, tuple[str, int]],
                 moldova_members: dict[str, set[str]]):
        self.downloads = downloads
        self.ne_url = ne_url
        self.gb_features = gb_features
        self.gisco_url = gisco_url
        self.nuts = nuts
        self.moldova_members = moldova_members
        self.sources: list[dict[str, Any]] = []
        self.ne: list[dict[str, Any]] = []
        self.osm: dict[int, Geometry] = {}

    def load_osm(self, countries: set[str]) -> None:
        wanted = {rid: spec for rid, spec in OSM_AREAS.items() if spec[0] in countries}
        if not wanted:
            return
        url = "https://nominatim.openstreetmap.org/lookup?" + urlencode({
            "osm_ids": ",".join(f"R{rid}" for rid in sorted(wanted)), "format": "json",
            "polygon_geojson": 1, "extratags": 1, "addressdetails": 1,
        })
        for record in self.downloads.json(url):
            rid = int(record["osm_id"])
            if rid not in wanted or record.get("osm_type") != "relation":
                continue
            _, _, level = wanted[rid]
            if record.get("class") != "boundary" or record.get("type") != "administrative":
                raise click.ClickException(f"Label source R{rid} is not an administrative boundary.")
            if str(record.get("extratags", {}).get("admin_level")) != str(level):
                raise click.ClickException(f"Label source R{rid} has an unexpected administrative level.")
            geometry = make_valid(shape(record["geojson"]))
            if geometry.is_empty or geometry.area == 0:
                raise click.ClickException(f"Label source R{rid} has no polygon.")
            self.osm[rid] = geometry
        if set(self.osm) != set(wanted):
            raise click.ClickException("Missing named administrative polygons in Nominatim lookup.")
        self.sources.append({"source": "OpenStreetMap administrative relations via Nominatim",
                             "url": url, "license": "ODbL 1.0", "relations": sorted(wanted)})

    def gb(self, code: str, level: int, count: int) -> list[NamedRegion]:
        data_code = "XKX" if code == "KOS" else code
        features, info = self.gb_features(self.downloads, data_code, level, count)
        self.sources.append(info)
        result = []
        for index, f in enumerate(features):
            name = f["properties"].get("shapeName", "").strip()
            name = re.sub(r"^District of\s+", "", name)
            if code == "HUN" and normal_name(name) in {"csongrad", "csongradcounty"}:
                name = "Csongrád-Csanád"
            if not name:
                raise click.ClickException(f"Unnamed label polygon in {data_code} ADM{level}.")
            result.append(NamedRegion(code, f"{code}:gb:{index}", name, make_valid(shape(f["geometry"]))))
        return result

    def natural_earth(self, code: str) -> list[NamedRegion]:
        features = [f for f in self.ne if f["properties"].get("adm0_a3") == code]
        if code == "NLD":
            features = [f for f in features if f["properties"].get("type_en") == "Province"]
        result = []
        for f in features:
            p = f["properties"]
            name = p.get("name_en") or p.get("name")
            # Natural Earth includes an unnamed Russian minor-island placeholder,
            # not a denoted federal subject in this mainland map.
            if not name:
                if code == "RUS" and p.get("note") == "RUS-99 (Russia minor island)":
                    continue
                raise click.ClickException(f"Unnamed Natural Earth region for {code}.")
            result.append(NamedRegion(code, p["adm1_code"], name, make_valid(shape(f["geometry"])),
                                      designation=p.get("type_en") or "region"))
        if code in NE_COUNTS and len(result) != NE_COUNTS[code]:
            raise click.ClickException(f"Expected {NE_COUNTS[code]} named areas for {code}, got {len(result)}.")
        return result

    def italy(self) -> list[NamedRegion]:
        grouped: dict[str, list[Geometry]] = defaultdict(list)
        for f in self.ne:
            p = f["properties"]
            if p.get("adm0_a3") == "ITA":
                if not p.get("region"):
                    raise click.ClickException("Italian province lacks its parent region name.")
                grouped[p["region"]].append(make_valid(shape(f["geometry"])))
        if len(grouped) != 20:
            raise click.ClickException("Expected 20 Italian regions, not province labels.")
        return [NamedRegion("ITA", f"ITA:region:{name}", name, union_all(poly))
                for name, poly in sorted(grouped.items())]

    def uk(self, irish: dict[str, Geometry], ni_counties: set[str]) -> list[NamedRegion]:
        collection = self.downloads.json(ONS_URL)
        if collection.get("exceededTransferLimit") or not collection.get("features"):
            raise click.ClickException("Incomplete ONS county label dataset.")
        grouped: dict[str, list[Geometry]] = defaultdict(list)
        metro = {normal_name(member): county for county, members in METROPOLITAN_COUNTIES.items()
                 for member in members.split("|")}
        seen_metro = set()
        for f in collection["features"]:
            p = f["properties"]
            code, name = p["CTYUA25CD"], p["CTYUA25NM"]
            if code.startswith("N090"):
                continue  # NI uses the six historic counties, not 11 councils.
            if code.startswith("E080"):
                key = normal_name(name)
                if key not in metro:
                    raise click.ClickException(f"Unknown metropolitan district: {name}")
                seen_metro.add(key)
                name = metro[key]
            elif code.startswith("E090") and code != "E09000001":
                name = "Greater London"
            grouped[name].append(make_valid(shape(f["geometry"])))
        if seen_metro != set(metro):
            raise click.ClickException("Incomplete metropolitan county membership in ONS source.")
        result = [NamedRegion("GBR", f"GBR:county:{name}", name, union_all(poly))
                  for name, poly in sorted(grouped.items())]
        if not ni_counties <= irish.keys():
            raise click.ClickException("Traditional NI county polygons are required for UK labels.")
        result.extend(NamedRegion("GBR", f"GBR:historic:{name}", name, irish[name])
                      for name in sorted(ni_counties))
        constituent = self.gb("GBR", 1, 4)
        result.extend(replace(r, tier="constituent") for r in constituent)
        self.sources.append({"source": "ONS counties/unitary authorities, December 2025 BGC",
                             "url": ONS_URL, "license": "Open Government Licence v3.0",
                             "attribution": "Contains OS data © Crown copyright and database right 2025",
                             "note": "Metropolitan districts dissolved into six counties; London boroughs into Greater London, with City of London distinct; NI councils replaced by traditional counties."})
        return result

    def norway(self) -> list[NamedRegion]:
        counties = self.downloads.json(NORWAY_URL)
        if len(counties) != 15:
            raise click.ClickException("Expected Norway's 15 modern counties.")
        result = []
        for county in tqdm(counties, desc="Norwegian county names", unit="county", leave=False):
            number = county["fylkesnummer"]
            record = self.downloads.json(f"{NORWAY_URL}/{number}/omrade")
            result.append(NamedRegion("NOR", f"NOR:{number}", county["fylkesnavn"],
                                      make_valid(shape(record["omrade"]))))
        self.sources.append({"source": "Kartverket county administrative areas", "url": NORWAY_URL,
                             "attribution": "Kartverket", "note": "Modern 15-county geography; label anchors only."})
        return result

    def nuts_regions(self, code: str) -> list[NamedRegion]:
        country, expected = self.nuts[code]
        fs = [f for f in self.downloads.json(self.gisco_url)["features"]
              if f["properties"].get("CNTR_CODE") == country and f["properties"].get("LEVL_CODE") == 3]
        if len(fs) != expected:
            raise click.ClickException(f"Expected {expected} statistical/planning region labels for {code}.")
        result = []
        for f in fs:
            p = f["properties"]
            name = MK_NAMES[p["NUTS_ID"]] if code == "MKD" else p["NUTS_NAME"]
            result.append(NamedRegion(code, p["NUTS_ID"], name, make_valid(shape(f["geometry"]))))
        return result

    def moldova(self) -> list[NamedRegion]:
        fs, info = self.gb_features(self.downloads, "MDA", 1, 37)
        by_iso = {f["properties"]["shapeISO"]: make_valid(shape(f["geometry"])) for f in fs}
        members = [iso for values in self.moldova_members.values() for iso in values]
        if len(members) != len(set(members)) or set(members) != set(by_iso):
            raise click.ClickException("Invalid Moldova development-region label grouping.")
        self.sources.append(info)
        return [NamedRegion("MDA", f"MDA:{name}", name, union_all([by_iso[i] for i in sorted(values)]))
                for name, values in self.moldova_members.items()]

    def build(self, countries: set[str], irish: dict[str, Geometry], ni_counties: set[str],
              custom: dict[str, dict[str, Any]] | None = None) -> list[NamedRegion]:
        custom = custom or {}
        needed = countries | ({"ALD"} if "FIN" in countries else set())
        self.ne = [f for f in self.downloads.json(self.ne_url)["features"]
                   if f["properties"].get("adm0_a3") in needed]
        self.sources.append({"source": "Natural Earth named administrative areas", "url": self.ne_url,
                             "license": "Public domain", "note": "Label polygons only; mixed levels explicitly grouped/overridden."})
        self.load_osm(countries - custom.keys())
        result = []
        for code in tqdm(sorted(countries), desc="Named displayed divisions", unit="country"):
            if code in custom:
                regions = []
                for index, f in enumerate(custom[code]["features"]):
                    p = f.get("properties", {})
                    name = p.get("name") or p.get("shapeName") or p.get("NUTS_NAME")
                    if not name or not f.get("geometry"):
                        raise click.ClickException(f"Custom {code} label features require a name and geometry. Use --no-region-labels to export an unnamed overlay.")
                    regions.append(NamedRegion(code, f"{code}:custom:{index}", name, make_valid(shape(f["geometry"]))))
            elif code == "VAT":
                regions = []
            elif code == "IRL":
                regions = [NamedRegion(code, f"IRL:{name}", name, g) for name, g in sorted(irish.items())
                           if name not in ni_counties]
            elif code in self.nuts:
                regions = self.nuts_regions(code)
            elif code == "MDA":
                regions = self.moldova()
            elif code == "GBR":
                regions = self.uk(irish, ni_counties)
            elif code == "NOR":
                regions = self.norway()
            elif code == "HUN":
                # The pinned gbOpen geometry contains 19 counties although its
                # metadata says 20; Budapest is a distinct denoted capital unit.
                regions = self.gb(code, 1, 19)
                capitals = [f for f in self.ne if f["properties"].get("adm0_a3") == code
                            and f["properties"].get("type_en") == "Capital City"]
                if len(capitals) != 1:
                    raise click.ClickException("Expected Budapest as Hungary's capital county-equivalent.")
                capital = make_valid(shape(capitals[0]["geometry"]))
                regions = [replace(r, geometry=r.geometry.difference(capital))
                           if normal_name(r.name) == "pest" else r for r in regions]
                regions.append(NamedRegion(code, "HUN:budapest", "Budapest", capital))
            elif code == "FRA":
                regions = self.gb(code, 2, 96)
                lyon = self.osm[4850450]
                regions = [replace(r, geometry=r.geometry.difference(lyon))
                           if normal_name(r.name) == "rhone" else r for r in regions]
                regions.append(NamedRegion(code, "FRA:metropole-lyon", "Métropole de Lyon", lyon))
            elif code == "ITA":
                regions = self.italy()
            elif code == "FIN":
                regions = self.natural_earth(code)
                aland = [make_valid(shape(f["geometry"])) for f in self.ne
                         if f["properties"].get("adm0_a3") == "ALD"]
                regions.append(NamedRegion(code, "FIN:aland", "Åland", union_all(aland)))
                if len(regions) != 19:
                    raise click.ClickException("Expected 19 Finnish regions including Åland.")
            elif code == "MNE":
                regions = self.gb(code, 1, 23)
                previous = [r for r in regions if "podgorica" in normal_name(r.name)]
                if len(previous) != 1:
                    raise click.ClickException("Could not replace Montenegro's older Podgorica label polygon.")
                regions = [r for r in regions if r not in previous]
                regions.extend(NamedRegion(code, f"MNE:osm:{rid}", spec[1], self.osm[rid])
                               for rid, spec in OSM_AREAS.items() if spec[0] == code)
                if len(regions) != 25:
                    raise click.ClickException("Expected Montenegro's 25 modern municipalities.")
            elif code == "MLT":
                regions = [NamedRegion(code, f"MLT:osm:{rid}", spec[1], self.osm[rid])
                           for rid, spec in OSM_AREAS.items() if spec[0] == code]
            elif code in GB_LABEL_LEVELS:
                level, count = GB_LABEL_LEVELS[code]
                regions = self.gb(code, level, count)
            else:
                regions = self.natural_earth(code)
            # Distinguish, for example, Kyiv/Minsk/Moscow city units from their
            # surrounding same-named regions instead of printing ambiguous twins.
            duplicates = Counter(r.name for r in regions)
            regions = [replace(r, name=f"{r.name} ({r.designation.lower()})")
                       if duplicates[r.name] > 1 else r for r in regions]
            if code != "VAT" and not regions:
                raise click.ClickException(f"No named division polygons for {code}.")
            result.extend(regions)
        return result


@dataclass(frozen=True)
class PlotLabel:
    key: str
    country: str
    name: str
    geometry: Geometry  # projected, visible portion
    tier: str = "region"


class RectangleIndex:
    """Exact padded label rectangles indexed into a pixel-space grid."""

    def __init__(self, cell: int = 64):
        self.cell = cell
        self.cells: dict[tuple[int, int], list[Bbox]] = defaultdict(list)
        self.rectangles: list[Bbox] = []

    def keys(self, rectangle: Bbox):
        for x in range(math.floor(rectangle.x0 / self.cell), math.floor(rectangle.x1 / self.cell) + 1):
            for y in range(math.floor(rectangle.y0 / self.cell), math.floor(rectangle.y1 / self.cell) + 1):
                yield x, y

    def overlaps(self, rectangle: Bbox) -> bool:
        return any(rectangle.overlaps(other) for key in self.keys(rectangle) for other in self.cells.get(key, []))

    def add(self, rectangle: Bbox) -> None:
        self.rectangles.append(rectangle)
        for key in self.keys(rectangle):
            self.cells[key].append(rectangle)


def wrapped_name(name: str, width: int = 20) -> str:
    return "\n".join(line for paragraph in name.splitlines()
                     for line in textwrap.wrap(paragraph, width=width, break_long_words=False, break_on_hyphens=True))


def placement_offsets(max_radius: int = 720):
    yield 0.0, 0.0
    for radius in (12, 22, 36, 54, 78, 110, 150, 200, 270, 360, 470, 590, max_radius):
        steps = max(16, math.ceil(2 * math.pi * radius / 24))
        for step in range(steps):
            angle = 2 * math.pi * step / steps
            yield radius * math.cos(angle), radius * math.sin(angle)


def place_labels(ax: Any, labels: list[PlotLabel], region_fontsize: float = 9.0,
                 country_fontsize: float = 12.0, description: str = "Placing labels") -> dict[str, Any]:
    """Place every label, or fail explicitly rather than silently hiding names."""
    ax.apply_aspect()
    renderer = ax.figure.canvas.get_renderer()
    frame = ax.get_window_extent(renderer).padded(-8)
    occupied = RectangleIndex()
    inverse = ax.transData.inverted()
    placed = []
    failures = []
    priority = {"country": 0, "constituent": 1, "region": 2}
    ordered = sorted(labels, key=lambda label: (priority[label.tier],
                     -label.geometry.area if label.tier != "region" else label.geometry.area, label.key))
    for label in tqdm(ordered, desc=description, unit="label"):
        polygon = max(parts(label.geometry), key=lambda p: p.area, default=None)
        if polygon is None:
            anchor = label.geometry.representative_point()
            visible = None
        else:
            anchor = polygon.representative_point()
            visible = polygon
        anchor_pixel = ax.transData.transform((anchor.x, anchor.y))
        fontsize = country_fontsize if label.tier == "country" else region_fontsize + (1 if label.tier == "constituent" else 0)
        text = wrapped_name(label.name.upper() if label.tier == "country" else label.name)
        artist = ax.text(anchor.x, anchor.y, text, fontsize=fontsize,
                         weight="bold" if label.tier != "region" else "normal",
                         color="#292929" if label.tier != "region" else "#414141",
                         ha="center", va="center", multialignment="center", linespacing=1.05,
                         zorder=12, clip_on=False,
                         path_effects=[patheffects.withStroke(linewidth=2.2, foreground="white")])
        original = artist.get_window_extent(renderer)
        half_w, half_h = original.width / 2 + 3, original.height / 2 + 3
        candidates = []
        for dx, dy in placement_offsets():
            center = anchor_pixel + (dx, dy)
            rectangle = Bbox.from_extents(center[0] - half_w, center[1] - half_h,
                                         center[0] + half_w, center[1] + half_h)
            if not frame.contains(rectangle.x0, rectangle.y0) or not frame.contains(rectangle.x1, rectangle.y1):
                continue
            if occupied.overlaps(rectangle):
                continue
            lower = inverse.transform((rectangle.x0, rectangle.y0))
            upper = inverse.transform((rectangle.x1, rectangle.y1))
            inside = visible is not None and visible.covers(box(lower[0], lower[1], upper[0], upper[1]))
            if inside:
                candidates = [(center, rectangle, False)]
                break
            # A displaced callout needs an actual leader, not an anchor hidden
            # under the text; this avoids naming an adjacent small region.
            if not rectangle.padded(6).contains(*anchor_pixel):
                candidates.append((center, rectangle, True))
            if candidates and math.hypot(dx, dy) >= 110:
                break
        if not candidates:
            artist.remove()
            failures.append(label.name)
            continue
        center, rectangle, leader = min(candidates, key=lambda c: float(np.linalg.norm(c[0] - anchor_pixel)))
        position = inverse.transform(center)
        artist.set_position(position)
        occupied.add(rectangle)
        if leader:
            endpoint = np.array([np.clip(anchor_pixel[0], rectangle.x0, rectangle.x1),
                                 np.clip(anchor_pixel[1], rectangle.y0, rectangle.y1)])
            end_data = inverse.transform(endpoint)
            ax.plot([anchor.x, end_data[0]], [anchor.y, end_data[1]], color="#8a8a8a",
                    linewidth=0.35, zorder=10, solid_capstyle="round")
            ax.plot(anchor.x, anchor.y, marker="o", markersize=1.4, color="#6b6b6b", zorder=11)
        placed.append({"key": label.key, "country": label.country, "name": label.name,
                       "tier": label.tier, "leader": leader,
                       "rectangle_pixels": [float(value) for value in rectangle.extents]})
    if failures:
        raise click.ClickException(f"Could not place {len(failures)} labels legibly ({', '.join(failures[:8])}). Increase --width/--height or use --no-region-labels; no overlapping/incomplete export was written.")
    return {"placed": placed, "overlapping_pairs": 0, "font_size_points": region_fontsize,
            "note": "Padded text rectangles checked in output-pixel coordinates. Leader lines locate small/displaced regions, not cities."}
