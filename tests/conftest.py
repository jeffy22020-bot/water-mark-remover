import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="session", autouse=True)
def isolated_data_dir(tmp_path_factory):
    os.environ["WMR_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    import make_samples

    out = tmp_path_factory.mktemp("samples")
    make_samples.main(out)
    return out


def needs_models():
    return pytest.mark.skipif(
        not (ROOT / "models" / "lama.onnx").exists(), reason="AI models not downloaded (python scripts/fetch_models.py)"
    )
