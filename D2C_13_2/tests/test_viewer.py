# -*- coding: utf-8 -*-
import json
from pathlib import Path

import pytest

from d2c13_2.viewer import HTML_TEMPLATE, build_payload, write_viewer_from_file

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "nova_exp" / "exp1" / "win95pts" / "run_01" / "artifact.json"
VALIDATION = ARTIFACT.parent / "validation_report.json"


@pytest.mark.skipif(not ARTIFACT.exists(), reason="external experiment artifact not included in the repository")
def test_viewer_payload_has_typed_views():
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    payload = build_payload(artifact)
    assert payload["counts"]["direct_core"] > 0
    assert "grounded" in payload["views"]
    assert "direct_core" in payload["layouts"]
    assert payload["nodes"]
    assert payload["edges"]


@pytest.mark.skipif(not ARTIFACT.exists() or not VALIDATION.exists(), reason="external experiment artifact not included in the repository")
def test_viewer_writes_self_contained_html(tmp_path):
    out = tmp_path / "viewer.html"
    write_viewer_from_file(ARTIFACT, out, validation_path=VALIDATION)
    text = out.read_text(encoding="utf-8")
    assert "D2C_13_2 Causal Audit Viewer" in text
    assert "direct_core" in text
    assert "const DATA =" in text
    assert '"run":"run_01"' in text
    assert "https://" not in text  # no CDN dependency



def test_viewer_template_contains_no_encoding_corruption():
    assert HTML_TEMPLATE.isascii()
    assert "\ufffd" not in HTML_TEMPLATE
    assert '<button id="zout" title="Zoom out">-</button>' in HTML_TEMPLATE
    assert "s.corpus||'unknown'" in HTML_TEMPLATE
    assert "e.condition||'none'" in HTML_TEMPLATE
    assert "Soft core (balanced)" in HTML_TEMPLATE
    assert "arrow-candidate" in HTML_TEMPLATE
    assert "let labelsOn = true;" in HTML_TEMPLATE
    assert "pointerdown" in HTML_TEMPLATE
    assert "pointermove" in HTML_TEMPLATE
    assert "'wheel'" in HTML_TEMPLATE
    assert "zoomAt" in HTML_TEMPLATE
    assert "setPointerCapture" in HTML_TEMPLATE
