from pathlib import Path

import app

ROOT = Path(__file__).resolve().parents[1]


def test_app_package_imports():
    assert app is not None


def test_dbt_project_exists():
    assert (ROOT / "warehouse" / "dbt_project.yml").is_file()