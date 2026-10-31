import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from harness.config import cell_configs, load_config  # noqa: E402

CONFIG_PATH = ROOT / "configs" / "social.yaml"


@pytest.fixture
def base_cfg():
    return load_config(CONFIG_PATH)


@pytest.fixture
def eff(base_cfg):
    """Effective config with bonus 30, no threshold."""
    cfg = copy.deepcopy(base_cfg)
    cfg.pop("cells", None)
    return cell_configs(cfg, {"bonus_per_word": 30, "close_threshold": None})[0][1]


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_DIR", str(tmp_path / "data"))


FOLLOWERS_PATH = ROOT / "configs" / "followers.yaml"


@pytest.fixture
def fcfg():
    return load_config(FOLLOWERS_PATH)


@pytest.fixture
def feff(fcfg):
    """Effective followers config of the first cell: reciprocal, no follow limit."""
    cfg = copy.deepcopy(fcfg)
    cfg.pop("cells", None)
    return cell_configs(cfg, {"reciprocal": True, "follow_limit": None})[0][1]
