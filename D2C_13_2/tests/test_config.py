# -*- coding: utf-8 -*-
from pathlib import Path

from d2c13_2.config import DEFAULT_CONFIG_PATH, load_config, role_config


def test_default_config_has_all_roles():
    config = load_config()
    assert Path(DEFAULT_CONFIG_PATH).exists()
    for role in ("extractor", "verifier"):
        assert config[role]["model"]
        assert config[role]["base_url"]
        assert config[role]["api_key_env"]
        assert int(config[role]["max_tokens"]) > 4096
    assert config["promotion"]["require_complete_evidence_contract_for_acceptance"] is False
    assert config["canonical_arbitration"]["vote_mode"] == "canonical_document"
    assert config["canonical_arbitration"]["candidate_conflict_policy"] == "needs_review"


def test_role_config_uses_single_config_and_allows_override():
    base = role_config("extractor")
    assert base["model"]
    assert base["base_url"]
    override = role_config("extractor", model="override-model")
    assert override["model"] == "override-model"
    assert override["base_url"] == base["base_url"]

