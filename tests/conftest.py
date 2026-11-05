import textwrap
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_judge(monkeypatch):
    monkeypatch.setenv("RH_JUDGE", "0")  # the packaged default enables the judge; unit tests must not call Ollama


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "billing.py").write_text(textwrap.dedent('''\
        import os


        def helper():
            return 1


        class InvoiceTotaler:
            def compute_total(self, items):
                total = 0
                for i in items:
                    total += i.price
                return total
        '''))
    (tmp_path / "tests" / "test_billing.py").write_text(
        "from pkg.billing import InvoiceTotaler\n\n\ndef test_total():\n    assert InvoiceTotaler().compute_total([]) == 0\n")
    (tmp_path / "README.md").write_text("# Billing\n\nInvoiceTotaler sums prices.\n")
    return tmp_path
