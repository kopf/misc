#!/usr/bin/env uv run
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click>=8.1,<9", "httpx>=0.28,<1", "json5>=0.12,<1", "mapbox-vector-tile>=2.2,<3",
#     "matplotlib>=3.10,<4", "mercantile>=1.2,<2", "numpy>=2,<3",
#     "pmtiles>=3.4,<4", "pyproj>=3.7,<4", "shapely>=2.1,<3", "tqdm>=4.67,<5",
# ]
# ///
"""Offline tests, including rendering a synthetic Protomaps-compatible PMTiles file."""

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import httpx
import main as app
import mapbox_vector_tile
import mercantile
import numpy as np
from click.testing import CliRunner
from geolabels import (
    METROPOLITAN_COUNTIES,
    ONS_URL,
    PlotLabel,
    RegionCatalogue,
    place_labels,
)
from matplotlib.transforms import Bbox
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from shapely import GeometryCollection
from shapely.geometry import LineString, Polygon, box, mapping


def vector_tile() -> bytes:
    return mapbox_vector_tile.encode([
        {"name": "earth", "features": [
            {"geometry": box(0, 0, 4096, 4096), "properties": {"kind": "earth"}},
            # A buffered polygon just outside the tile clips to a line. It must
            # be discarded rather than corrupting the merged land boundary.
            {"geometry": box(-100, 0, 0, 100), "properties": {"kind": "earth"}},
        ]},
        {"name": "boundaries", "features": [
            {"geometry": LineString([(0, 2048), (4096, 2048)]),
             "properties": {"kind": "region", "kind_detail": 4}},
            {"geometry": LineString([(0, 1024), (4096, 1024)]),
             "properties": {"kind": "country", "kind_detail": 2}},
        ]},
    ])


class MappingTests(unittest.TestCase):
    def test_agreed_levels_and_excluded_caucasus(self):
        expected = {"FRA": 6, "DEU": 4, "GBR": 6, "ESP": 6, "ITA": 4,
                    "BEL": 6, "PRT": 6, "POL": 4, "CHE": 4, "AUT": 4,
                    "GRC": 5, "LTU": 4, "BIH": 4, "SRB": 6, "KOS": 4, "ISL": 5}
        for code, level in expected.items():
            self.assertEqual(app.DIVISIONS[code].level, level)
        self.assertFalse({"GEO", "ARM", "AZE"} & app.DIVISIONS.keys())
        self.assertEqual(len(set(app.COUNTIES)), 32)
        self.assertEqual(len(app.NI_COUNTIES), 6)
        self.assertEqual(app.DIVISIONS["AND"].zoom, 10)
        self.assertEqual(app.DIVISIONS["FRA"].zoom, 8)
        self.assertEqual(app.REGIONAL_NUTS, {"SVN": ("SI", 12), "LVA": ("LV", 5), "MKD": ("MK", 8)})
        for code in ("SVN", "MKD", "LVA", "MDA"):
            self.assertIsNone(app.DIVISIONS[code].level)
        members = [iso for units in app.MOLDOVA_DEVELOPMENT_UNITS.values() for iso in units]
        self.assertEqual(len(app.MOLDOVA_DEVELOPMENT_UNITS), 6)
        self.assertEqual(len(members), 37)
        self.assertEqual(len(set(members)), 37)
        self.assertIn("MD-BD", app.MOLDOVA_DEVELOPMENT_UNITS["Transnistria"])

    def test_statistical_regions_filter_country_and_level(self):
        features = []
        for country, count in app.REGIONAL_NUTS.values():
            for index in range(count):
                features.append({"properties": {"CNTR_CODE": country, "LEVL_CODE": 3,
                                                 "NUTS_ID": f"{country}{index:03}", "NUTS_NAME": f"Region {index}"},
                                 "geometry": mapping(box(index, 0, index + 1, 1))})
        features.append({"properties": {"CNTR_CODE": "SI", "LEVL_CODE": 2, "NUTS_ID": "SI0"},
                         "geometry": mapping(box(0, 0, 12, 1))})

        class FakeDownloads:
            def json(self, _url):
                return {"features": features}

        for code, (_, count) in app.REGIONAL_NUTS.items():
            borders, info = app.statistical_regions(FakeDownloads(), code)
            self.assertEqual(info["units"], count)
            self.assertEqual(len(info["identifiers"]), count)
            self.assertAlmostEqual(borders.length, count - 1)
        features.pop(0)
        with self.assertRaisesRegex(app.click.ClickException, "12 distinct"):
            app.statistical_regions(FakeDownloads(), "SVN")

    def test_moldova_dissolves_districts_into_six_regions(self):
        features = []
        index = 0
        for members in app.MOLDOVA_DEVELOPMENT_UNITS.values():
            for iso in sorted(members):
                features.append({"properties": {"shapeISO": iso, "shapeName": iso},
                                 "geometry": mapping(box(index, 0, index + 1, 1))})
                index += 1

        class FakeDownloads:
            def json(self, url):
                if url.endswith("-metaData.json"):
                    return {"boundaryLicense": "ODbL", "boundaryYear": "2017", "admUnitCount": "37"}
                return {"features": features}

        borders, info = app.moldova_development_regions(FakeDownloads())
        self.assertEqual(info["units"], 6)
        self.assertEqual(info["source_units"], 37)
        self.assertAlmostEqual(borders.length, 5)
        all_districts = [shape_feature["geometry"] for shape_feature in features]
        self.assertEqual(len(all_districts), 37)
        features[0]["properties"]["shapeISO"] = "MD-UNKNOWN"
        with self.assertRaisesRegex(app.click.ClickException, "all 37"):
            app.moldova_development_regions(FakeDownloads())

    def test_tile_coordinates_point_up(self):
        class FakeReader:
            def get(self, *_args):
                return vector_tile()

        tile = mercantile.tile(10, 50, 8)
        decoded = app.read_tile(FakeReader(), tile, Compression.NONE, True)
        bounds = mercantile.xy_bounds(tile)
        midpoint = (bounds.left + bounds.right) / 2
        expected_x, expected_y = app.FROM_MERCATOR(midpoint, bounds.bottom + (bounds.top - bounds.bottom) / 4)
        country_line = decoded.boundaries[(2, False, 8)][0]
        self.assertLess(country_line.distance(app.project(
            LineString([(mercantile.bounds(tile).west, mercantile.bounds(tile).south),
                        (mercantile.bounds(tile).east, mercantile.bounds(tile).south)]))), 200000)
        from shapely.geometry import Point
        self.assertLess(country_line.distance(Point(expected_x, expected_y)), 1)
        self.assertTrue(decoded.land)
        self.assertTrue(all(part.geom_type == "Polygon" for part in decoded.land))
        self.assertFalse(decoded.coastline, "Tile clipping edges must not become coastlines")

    def test_coastlines_keep_interior_shore_but_not_tile_edges(self):
        data = mapbox_vector_tile.encode({"name": "earth", "features": [
            {"geometry": box(-100, -100, 2048, 4200), "properties": {"kind": "earth"}}]})

        class FakeReader:
            def get(self, *_args):
                return data

        tile = mercantile.tile(10, 50, 8)
        decoded = app.read_tile(FakeReader(), tile, Compression.NONE, True)
        self.assertEqual(len(decoded.coastline), 1)
        # One shore roughly one tile high, rather than a four-sided tile frame.
        self.assertLess(decoded.coastline[0].length, np.sqrt(decoded.land[0].area) * 2)
        self.assertGreater(decoded.coastline[0].length, np.sqrt(decoded.land[0].area))

    def test_holes_have_opposite_winding(self):
        polygon = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)],
                          holes=[[(2, 2), (8, 2), (8, 8), (2, 8)]])
        path = app.polygon_path(polygon)
        starts = np.flatnonzero(path.codes == app.PlotPath.MOVETO)
        areas = []
        for index, start in enumerate(starts):
            stop = starts[index + 1] if index + 1 < len(starts) else len(path.vertices)
            ring = path.vertices[start:stop]
            areas.append(np.sum(ring[:-1, 0] * ring[1:, 1] - ring[1:, 0] * ring[:-1, 1]))
        self.assertLess(areas[0] * areas[1], 0)

    def test_admin_selection_does_not_mix_zooms_or_deeper_levels(self):
        mask = box(0, 0, 100000, 100000)
        valid = LineString([(10000, 50000), (90000, 50000)])
        deeper = LineString([(10000, 60000), (90000, 60000)])
        other_zoom = LineString([(10000, 70000), (90000, 70000)])
        data = app.TileData([], {(4, False, 8): [valid], (6, False, 8): [deeper],
                                 (4, False, 10): [other_zoom]})
        admin, _, _, _, report = app.prepare_boundaries(
            data, {"DEU": mask}, {"DEU": app.DIVISIONS["DEU"]}, GeometryCollection(), mask)
        self.assertAlmostEqual(admin.length, valid.length)
        self.assertEqual(report["DEU"]["target_level_length_km"], 80)

    def test_uk_country_borders_survive_custom_admin_overlay(self):
        mask = box(0, 0, 100000, 100000)
        border = LineString([(0, 50000), (100000, 50000)])
        data = app.TileData([], {(4, False, 8): [border]})
        admin, _, _, uk, _ = app.prepare_boundaries(
            data, {"GBR": mask}, {"GBR": app.Division("UK", None, "custom")},
            GeometryCollection(), mask)
        self.assertTrue(admin.is_empty)
        self.assertAlmostEqual(uk.length, border.length)

    def test_russia_uses_europe_mask_not_a_meridian(self):
        country = {"type": "FeatureCollection", "features": [{
            "properties": {"ADM0_A3": "RUS"}, "geometry": mapping(box(40, 45, 80, 70))}]}
        europe_shape = Polygon([(0, 30), (60, 30), (66, 70), (0, 80)])
        continent = {"features": [{"properties": {"NAME": "EUROPE"},
                                   "geometry": mapping(europe_shape)}]}

        class FakeDownloads:
            def json(self, url):
                return continent if url == app.EUROPE_URL else country

        masks = app.country_masks(FakeDownloads(), {"RUS"}, box(-32, 27, 68, 72))
        self.assertTrue(europe_shape.covers(masks["RUS"]))
        self.assertGreater(masks["RUS"].bounds[2], 60)
        self.assertLess(masks["RUS"].bounds[2], 68)

    def test_supplements_do_not_draw_old_coastlines_as_admin_borders(self):
        left, right = box(0, 0, 1, 1), box(1, 0, 2, 1)
        borders = app.internal_borders([left, right])
        self.assertTrue(borders.equals(LineString([(1, 0), (1, 1)])))

    def test_range_server_must_not_return_full_archive(self):
        source = app.RangeSource.__new__(app.RangeSource)
        source.url = "https://example.test/planet.pmtiles"
        source.client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"not a ranged response")))
        try:
            with self.assertRaisesRegex(app.click.ClickException, "ignored the Range"):
                source.request(0, 127)
        finally:
            source.client.close()

    def test_range_identity_cannot_change(self):
        source = app.RangeSource.__new__(app.RangeSource)
        source.url = "https://example.test/planet.pmtiles"
        source.identity = '"original"'
        source.client = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(206, content=b"x", headers={
                "content-range": "bytes 0-0/100", "etag": '"changed"'})))
        try:
            with self.assertRaisesRegex(app.click.ClickException, "changed during"):
                source.request(0, 1)
        finally:
            source.client.close()

    def test_full_cli_produces_png_and_vector_svg_offline(self):
        geometry = box(9.5, 49.5, 10.5, 50.5)
        natural_earth = {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": {"ADM0_A3": "DEU"}, "geometry": mapping(geometry)}]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "test.pmtiles"
            jobs = app.tiles_for(geometry.buffer(0.05), 6) | app.tiles_for(geometry.buffer(0.05), 8)
            with archive.open("wb") as file:
                writer = Writer(file)
                for tile in sorted(jobs, key=lambda t: zxy_to_tileid(t.z, t.x, t.y)):
                    writer.write_tile(zxy_to_tileid(tile.z, tile.x, tile.y), gzip.compress(vector_tile()))
                writer.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP,
                                 "min_lon_e7": 90000000, "min_lat_e7": 490000000,
                                 "max_lon_e7": 110000000, "max_lat_e7": 510000000,
                                 "center_zoom": 6, "center_lon_e7": 100000000, "center_lat_e7": 500000000},
                                {"vector_layers": [{"id": "earth"}, {"id": "boundaries"}]})
            with patch.object(app.Downloads, "json", return_value=natural_earth):
                result = CliRunner().invoke(app.main, [
                    "--pmtiles", str(archive), "--countries", "DEU", "--bounds", "9", "49", "11", "51",
                    "--output", str(root / "map"), "--cache", str(root / "cache"),
                    "--width", "800", "--height", "600", "--no-labels", "--workers", "2"])
            self.assertEqual(result.exit_code, 0, f"{result.output}\n{result.exception}")
            with Image.open(root / "map.png") as image:
                self.assertEqual(image.size, (800, 600))
            svg = ElementTree.parse(root / "map.svg")
            self.assertFalse(svg.findall(".//{http://www.w3.org/2000/svg}image"))
            self.assertTrue(svg.findall(".//{http://www.w3.org/2000/svg}path"))
            manifest = json.loads((root / "map.sources.json").read_text())
            self.assertEqual(manifest["warnings"], [])
            self.assertGreater(manifest["administrative_divisions"]["DEU"]["target_level_length_km"], 0)

    def test_uk_labels_are_counties_not_metropolitan_or_london_boroughs(self):
        features = []
        index = 0
        for members in METROPOLITAN_COUNTIES.values():
            for name in members.split("|"):
                features.append({"properties": {"CTYUA25CD": f"E080{index:05}", "CTYUA25NM": name},
                                 "geometry": mapping(box(index, 0, index + 1, 1))})
                index += 1
        for i in range(1, 34):
            features.append({"properties": {"CTYUA25CD": f"E090{i:05}",
                                             "CTYUA25NM": "City of London" if i == 1 else f"Borough {i}"},
                             "geometry": mapping(box(i, 3, i + 1, 4))})
        features.append({"properties": {"CTYUA25CD": "E10000017", "CTYUA25NM": "Lancashire"},
                         "geometry": mapping(box(0, 5, 10, 6))})

        class FakeDownloads:
            def json(self, url):
                self_url = ONS_URL
                if url != self_url:
                    raise AssertionError(url)
                return {"features": features}

        def fake_gb(_downloads, code, level, count):
            self.assertEqual((code, level, count), ("GBR", 1, 4))
            return ([{"properties": {"shapeName": name}, "geometry": mapping(box(i, 10, i + 1, 11))}
                     for i, name in enumerate(["England", "Scotland", "Wales", "Northern Ireland"])], {})

        catalogue = RegionCatalogue(FakeDownloads(), "unused", fake_gb, "unused", {}, {})
        irish = {name: box(i, 12, i + 1, 13) for i, name in enumerate(sorted(app.NI_COUNTIES))}
        regions = catalogue.uk(irish, app.NI_COUNTIES)
        names = {region.name for region in regions}
        self.assertNotIn("Manchester", names)
        self.assertNotIn("Liverpool", names)
        self.assertNotIn("Borough 2", names)
        self.assertIn("Greater Manchester", names)
        self.assertIn("Merseyside", names)
        self.assertIn("Greater London", names)
        self.assertIn("City of London", names)
        self.assertEqual(len(regions), 19)
        self.assertEqual(sum(region.tier == "constituent" for region in regions), 4)

    def test_italian_labels_use_twenty_regions_not_provinces(self):
        catalogue = RegionCatalogue(None, "unused", None, "unused", {}, {})
        catalogue.ne = [{"properties": {"adm0_a3": "ITA", "region": f"Region {i}", "name": f"Province {j}"},
                         "geometry": mapping(box(2 * i + j, 0, 2 * i + j + 1, 1))}
                        for i in range(20) for j in range(2)]
        regions = catalogue.italy()
        self.assertEqual(len(regions), 20)
        self.assertTrue(all(region.name.startswith("Region") for region in regions))
        self.assertTrue(all(region.geometry.area == 2 for region in regions))

    def test_crowded_labels_have_no_overlap_and_none_are_dropped(self):
        fig, ax = app.plt.subplots(figsize=(12, 8), dpi=100)
        try:
            ax.set_xlim(0, 100)
            ax.set_ylim(0, 100)
            ax.set_aspect("equal")
            labels = [PlotLabel(f"region:{i}", "TEST", f"Region number {i}",
                                box(50 + i / 1000, 50, 50.0005 + i / 1000, 50.0005))
                      for i in range(24)]
            info = place_labels(ax, labels, description="Test label layout")
            self.assertEqual(len(info["placed"]), len(labels))
            rectangles = [Bbox.from_extents(*entry["rectangle_pixels"]) for entry in info["placed"]]
            for i, rectangle in enumerate(rectangles):
                for other in rectangles[i + 1:]:
                    self.assertFalse(rectangle.overlaps(other))
            self.assertTrue(all(entry["leader"] for entry in info["placed"]))
        finally:
            app.plt.close(fig)

    def test_impossible_label_layout_fails_instead_of_hiding_names(self):
        fig, ax = app.plt.subplots(figsize=(4, 3), dpi=100)
        try:
            ax.set_xlim(0, 10)
            ax.set_ylim(0, 10)
            with self.assertRaisesRegex(app.click.ClickException, "Could not place 1 labels legibly"):
                place_labels(ax, [PlotLabel("too-wide", "TEST", "X" * 400, box(0, 0, 10, 10))])
        finally:
            app.plt.close(fig)

    def test_invalid_country_and_bounds(self):
        for arguments in (["--countries", "GEO"], ["--bounds", "10", "50", "5", "55"]):
            result = CliRunner().invoke(app.main, arguments)
            self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
