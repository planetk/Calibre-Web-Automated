# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import subprocess
from pathlib import Path
from unittest import mock

import pytest


pytestmark = pytest.mark.unit


@pytest.fixture
def ingest_processor(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.setenv("CWA_INGEST_BATCH_DIRTY_FILE", str(tmp_path / "batch_dirty"))
    monkeypatch.delenv("CWA_POST_IMPORT_HOOK", raising=False)
    monkeypatch.delenv("CWA_POST_IMPORT_HOOK_TIMEOUT", raising=False)

    import ingest_processor

    return ingest_processor


def build_processor(ingest_processor, tmp_path):
    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.calibre_env = {"HOME": "/config"}
    processor.library_dir = str(tmp_path / "library")
    processor.metadata_db = str(tmp_path / "library" / "metadata.db")
    return processor


def write_hook(tmp_path, body="#!/bin/sh\nexit 0\n"):
    hook = tmp_path / "hook.sh"
    hook.write_text(body)
    hook.chmod(0o755)
    return hook


def test_hook_not_configured_does_nothing(ingest_processor, tmp_path):
    processor = build_processor(ingest_processor, tmp_path)

    with mock.patch.object(ingest_processor.subprocess, "run") as run_mock:
        processor.run_post_import_hook_if_enabled([7])

    run_mock.assert_not_called()


def test_hook_receives_ids_and_environment(ingest_processor, monkeypatch, tmp_path):
    hook = write_hook(tmp_path)
    monkeypatch.setenv("CWA_POST_IMPORT_HOOK", str(hook))
    processor = build_processor(ingest_processor, tmp_path)

    result = subprocess.CompletedProcess(args=[str(hook)], returncode=0, stdout="ok\n", stderr="")
    with mock.patch.object(ingest_processor.subprocess, "run", return_value=result) as run_mock:
        processor.run_post_import_hook_if_enabled([7, 8])

    assert run_mock.call_args.args[0] == [str(hook), "7", "8"]
    env = run_mock.call_args.kwargs["env"]
    assert env["CWA_BOOK_IDS"] == "7,8"
    assert env["CWA_LIBRARY_DIR"] == processor.library_dir
    assert env["CWA_METADATA_DB"] == processor.metadata_db
    assert env["HOME"] == "/config"
    assert run_mock.call_args.kwargs["timeout"] == 300


def test_hook_skipped_without_book_ids(ingest_processor, monkeypatch, tmp_path):
    monkeypatch.setenv("CWA_POST_IMPORT_HOOK", str(write_hook(tmp_path)))
    processor = build_processor(ingest_processor, tmp_path)

    with mock.patch.object(ingest_processor.subprocess, "run") as run_mock:
        processor.run_post_import_hook_if_enabled([])

    run_mock.assert_not_called()


def test_hook_skipped_when_not_executable(ingest_processor, monkeypatch, tmp_path):
    hook = write_hook(tmp_path)
    hook.chmod(0o644)
    monkeypatch.setenv("CWA_POST_IMPORT_HOOK", str(hook))
    processor = build_processor(ingest_processor, tmp_path)

    with mock.patch.object(ingest_processor.subprocess, "run") as run_mock:
        processor.run_post_import_hook_if_enabled([7])

    run_mock.assert_not_called()


@pytest.mark.parametrize("side_effect", [
    subprocess.TimeoutExpired(cmd="hook", timeout=5),
    OSError("exec format error"),
])
def test_hook_errors_are_not_fatal(ingest_processor, monkeypatch, tmp_path, side_effect):
    monkeypatch.setenv("CWA_POST_IMPORT_HOOK", str(write_hook(tmp_path)))
    monkeypatch.setenv("CWA_POST_IMPORT_HOOK_TIMEOUT", "5")
    processor = build_processor(ingest_processor, tmp_path)

    with mock.patch.object(ingest_processor.subprocess, "run", side_effect=side_effect) as run_mock:
        processor.run_post_import_hook_if_enabled([7])

    assert run_mock.call_args.kwargs["timeout"] == 5


def test_hook_runs_for_real(ingest_processor, monkeypatch, tmp_path, capsys):
    marker = tmp_path / "marker"
    hook = write_hook(tmp_path, f'#!/bin/sh\necho "$CWA_BOOK_IDS $*" > "{marker}"\nexit 3\n')
    monkeypatch.setenv("CWA_POST_IMPORT_HOOK", str(hook))
    processor = build_processor(ingest_processor, tmp_path)

    processor.run_post_import_hook_if_enabled([7])

    assert marker.read_text().strip() == "7 7"
    assert "exited with code 3" in capsys.readouterr().out


def test_import_runs_hook_before_auto_send(ingest_processor, tmp_path):
    processor = build_processor(ingest_processor, tmp_path)
    processor.target_format = "epub"
    processor.is_kindle_epub_fixer = False
    processor.cwa_settings = {"auto_ingest_automerge": "ignore", "auto_backup_imports": False}
    processor.staging_dir = str(tmp_path / "staging")
    processor.last_added_book_id = None
    processor.last_added_book_ids = []
    processor.db = mock.Mock()
    processor.filepath = str(tmp_path / "source.epub")
    processor.tmp_conversion_dir = str(tmp_path / "conversion")
    for directory in (processor.staging_dir, processor.library_dir, processor.tmp_conversion_dir):
        Path(directory).mkdir()
    source = tmp_path / "source.epub"
    source.write_bytes(b"book")

    calls = []
    result = subprocess.CompletedProcess(args=["calibredb"], returncode=0, stdout="Added book id: 7\n", stderr="")
    with mock.patch.object(ingest_processor.subprocess, "run", return_value=result), \
        mock.patch.object(ingest_processor, "gdrive_sync_if_enabled"), \
        mock.patch.object(processor, "fetch_metadata_if_enabled"), \
        mock.patch.object(processor, "generate_book_checksums"), \
        mock.patch.object(processor, "run_post_import_hook_if_enabled",
                          side_effect=lambda ids: calls.append(("hook", ids))), \
        mock.patch.object(processor, "trigger_auto_send_if_enabled",
                          side_effect=lambda **kwargs: calls.append(("auto_send", kwargs["book_id"]))):
        processor.add_book_to_library(str(source))

    assert calls == [("hook", [7]), ("auto_send", 7)]
