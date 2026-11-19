# -*- coding: utf-8 -*-
import importlib.util
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "replay_saved_verdicts.py"
_SPEC = importlib.util.spec_from_file_location("d2c13_2_replay_saved_verdicts", _MODULE_PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)
remap_verdicts = _MODULE.remap_verdicts


def _artifact():
    return {
        "entities": [
            {"id": "e:problem", "label": "Fifth Problem Indicator"},
            {"id": "e:lower", "label": "truetype_font_rendering_normal"},
            {"id": "e:title", "label": "TrueType Font Rendering Normal"},
        ]
    }


def test_case_preserving_surface_signature_disambiguates_alias_collapse():
    old_requests = [
        {
            "edge_id": "old:lower", "source": "Fifth Problem Indicator",
            "target": "truetype_font_rendering_normal", "kind": "candidate",
        },
        {
            "edge_id": "old:title", "source": "Fifth Problem Indicator",
            "target": "TrueType Font Rendering Normal", "kind": "candidate",
        },
    ]
    old_verdicts = [
        {"edge_id": "old:lower", "support": "unsupported", "direction": "ambiguous"},
        {"edge_id": "old:title", "support": "unsupported", "direction": "ambiguous"},
    ]
    new_requests = [
        {
            "edge_id": "new:title", "source": "Fifth Problem Indicator",
            "target": "TrueType Font Rendering Normal", "kind": "candidate",
        },
        {
            "edge_id": "new:lower", "source": "Fifth Problem Indicator",
            "target": "truetype_font_rendering_normal", "kind": "candidate",
        },
    ]
    mapped, report = remap_verdicts(
        old_requests, old_verdicts, new_requests, _artifact(), _artifact(),
    )
    by_new = {row["edge_id"]: row for row in mapped}
    assert by_new["new:title"]["replay_source_edge_id"] == "old:title"
    assert by_new["new:lower"]["replay_source_edge_id"] == "old:lower"
    assert report["mapped_verdicts"] == 2
    assert report["unmapped_new_requests"] == 0
    assert report["mapping_methods"]["exact_surface_signature"] == 2
