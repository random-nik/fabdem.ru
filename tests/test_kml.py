from __future__ import annotations

import io
import json
import zipfile

import pytest

from app.services.kml import ParseError, parse_upload

KML_POLYGON = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>Test area</name>
      <Polygon>
        <outerBoundaryIs>
          <LinearRing>
            <coordinates>
              37.0,55.0,0 38.0,55.0,0 38.0,56.0,0 37.0,56.0,0 37.0,55.0,0
            </coordinates>
          </LinearRing>
        </outerBoundaryIs>
      </Polygon>
    </Placemark>
  </Document>
</kml>
"""

KML_TWO_POLYGONS = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <Placemark>
      <name>One</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        37,55,0 38,55,0 38,56,0 37,56,0 37,55,0
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
    <Placemark>
      <name>Two</name>
      <Polygon><outerBoundaryIs><LinearRing><coordinates>
        39,55,0 40,55,0 40,56,0 39,56,0 39,55,0
      </coordinates></LinearRing></outerBoundaryIs></Polygon>
    </Placemark>
  </Document>
</kml>
"""


def test_parse_kml_polygon():
    parsed = parse_upload("area.kml", KML_POLYGON.encode("utf-8"))
    assert parsed.feature_count == 1
    assert parsed.geometry["type"] == "Polygon"
    assert parsed.geometry["coordinates"][0][0] == [37.0, 55.0]


def test_parse_kml_with_multiple_polygons():
    parsed = parse_upload("area.kml", KML_TWO_POLYGONS.encode("utf-8"))
    assert parsed.feature_count == 2
    assert parsed.geometry["type"] == "MultiPolygon"


def test_parse_kmz():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc.kml", KML_POLYGON)
    parsed = parse_upload("area.kmz", buffer.getvalue())
    assert parsed.feature_count == 1
    assert parsed.geometry["type"] == "Polygon"


def test_parse_geojson():
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[37, 55], [38, 55], [38, 56], [37, 56], [37, 55]]
                    ],
                },
            }
        ],
    }
    parsed = parse_upload("area.geojson", json.dumps(payload).encode("utf-8"))
    assert parsed.feature_count == 1
    assert parsed.geometry["type"] == "Polygon"


def test_rejects_non_polygon_and_bad_files():
    with pytest.raises(ParseError):
        parse_upload(
            "bad.geojson",
            json.dumps(
                {
                    "type": "Point",
                    "coordinates": [37, 55],
                }
            ).encode("utf-8"),
        )

    with pytest.raises(ParseError):
        parse_upload("bad.kml", b"<not-kml>")
