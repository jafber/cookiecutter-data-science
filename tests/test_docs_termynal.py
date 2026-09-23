"""Cross-platform regression tests for the documentation's CLI animation."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "docs/scripts/generate-termynal.py"


@pytest.fixture
def termynal():
    spec = importlib.util.spec_from_file_location("generate_termynal", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_dialogue(termynal, tmp_path):
    directory = tmp_path / "directory with spaces"
    directory.mkdir()
    program = directory / "prompt script.py"
    program.write_text(
        "import sys\n"
        "assert input('name (default): ') == 'Zoë & <friends>'\n"
        "assert input('module (default): ') == ''\n"
        "print('Select storage\\n1 - local\\n2 - cloud')\n"
        "assert input('Choose from [1/2] (1): ') == '2'\n"
        "assert input('bucket (default): ') == 's3://my-bucket'\n"
        "print('Finished', file=sys.stderr)\n",
        encoding="utf-8",
    )
    output = termynal.execute_command_and_get_output(
        [sys.executable, str(program)],
        [
            ("name", "Zoë & <friends>"),
            ("module", ""),
            ("Choose from", "2"),
            ("bucket", "s3://my-bucket"),
        ],
        directory,
    )
    assert output[1:] == [
        "name (default): Zoë & <friends>",
        "module (default):",
        "Select storage",
        "1 - local",
        "2 - cloud",
        "Choose from [1/2] (1): 2",
        "bucket (default): s3://my-bucket",
        "Finished",
    ]


def test_capture_failure_includes_output(termynal, tmp_path):
    with pytest.raises(
        RuntimeError,
        match="status 7:.*",
    ) as error:
        termynal.execute_command_and_get_output(
            [sys.executable, "-c", "print('generation failed'); raise SystemExit(7)"],
            [],
            tmp_path,
        )
    assert "generation failed" in str(error.value)


def test_capture_missing_prompt_fails(termynal, tmp_path):
    with pytest.raises(RuntimeError, match="Expected CCDS prompt 'name'"):
        termynal.execute_command_and_get_output(
            [sys.executable, "-c", "print('changed prompt: ')"],
            [("name", "answer")],
            tmp_path,
        )


def test_capture_timeout(termynal, tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        termynal.execute_command_and_get_output(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            [],
            tmp_path,
            timeout=0.2,
        )


def test_run_scripts_cleans_up_on_failure(termynal, monkeypatch):
    directories = []

    def fail(command, input_script, cwd):
        directories.append(Path(cwd))
        assert Path(cwd).is_dir()
        raise RuntimeError("capture failed")

    monkeypatch.setattr(termynal, "execute_command_and_get_output", fail)
    with pytest.raises(RuntimeError, match="capture failed"):
        termynal.run_scripts()
    assert not directories[0].exists()


def test_render_real_cli_preserves_existing_project(termynal, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = tmp_path / "my_analysis"
    project.mkdir()
    sentinel = project / "keep.txt"
    sentinel.write_text("Do not delete")

    output = termynal.render_termynal()

    assert sentinel.read_text() == "Do not delete"
    assert sorted(tmp_path.iterdir()) == [project]
    assert output.count('data-ty="input"') == len(termynal.ccds_script) + 1
    for option in json.loads((termynal.CCDS_ROOT / "ccds.json").read_text()):
        assert option in output
    assert "s3://my-aws-bucket" in output
    assert str(termynal.CCDS_ROOT) not in output
    assert "https://github.com/drivendataorg/cookiecutter-data-science" in output


def test_renderer_escapes_html_and_preserves_colons(termynal, monkeypatch, tmp_path):
    (tmp_path / "ccds.json").write_text('{"bucket": ""}')
    monkeypatch.setattr(termynal, "CCDS_ROOT", tmp_path)
    monkeypatch.setattr(termynal, "ccds_script", [("bucket", "s3://new/<data>")])
    monkeypatch.setattr(
        termynal,
        "run_scripts",
        lambda: ["$ ccds example", "bucket (s3://old): s3://new/<data>", "Done"],
    )
    output = termynal.render_termynal()
    assert "bucket (s3://old):" in output
    assert "s3://new/&lt;data&gt;" in output
    assert "<span data-ty>Done</span>" in output


def test_renderer_rejects_missing_answer(termynal, monkeypatch):
    monkeypatch.setattr(termynal, "run_scripts", lambda: ["$ ccds example"])
    with pytest.raises(RuntimeError, match="was not rendered"):
        termynal.render_termynal()
