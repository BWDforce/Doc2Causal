# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from d2c13_2.lang7_pipeline import enhance_lang7_run

ROOT = Path(__file__).resolve().parents[2]
LANG7_RUN = ROOT / "graph_exp" / "exp29" / "results" / "win95pts" / "lang7" / "run_01"
DOCS = ROOT / "data" / "Test-text" / "input" / "goldgen_gen5" / "win95pts"


@pytest.mark.skipif(not LANG7_RUN.exists() or not DOCS.exists(), reason="external experiment data not included in the repository")
def test_enhance_existing_lang7_run(tmp_path):
    summary = enhance_lang7_run(
        LANG7_RUN, tmp_path, docs_dir=DOCS, corpus="win95pts",
        config_path=ROOT / "D2C_13_2" / "config.json", verify=False,
    )
    assert summary["pipeline"] == "lang7->D2C_13_2"
    assert summary["validation_valid"] is True
    assert summary["shacl_conforms"] is True
    assert summary["roundtrip_json"] is True
    assert summary["roundtrip_rdf"] is True
    assert summary["roundtrip_queries"] is True
    assert (tmp_path / "artifact.json").exists()
    assert (tmp_path / "artifact.pre_verification.json").exists()
    assert (tmp_path / "verification_requests.input.json").exists()
    assert (tmp_path / "verification_results.json").exists()
    assert (tmp_path / "visualization.html").exists()

