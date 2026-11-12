import pytest

from fxengine.cli import main


def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])
    assert excinfo.value.code == 0
    assert main(["run-all", "--dry-run"]) == 0
