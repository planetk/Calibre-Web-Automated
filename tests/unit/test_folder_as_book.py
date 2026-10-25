# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2018-2026 Calibre-Web contributors
# Copyright (C) 2024-2026 Calibre-Web Automated contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

import json
import os
import time
from pathlib import Path
from unittest import mock

import pytest


pytestmark = pytest.mark.unit


@pytest.fixture
def ingest_processor(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    monkeypatch.setenv("CWA_INGEST_FOLDER_AS_BOOK", "true")
    monkeypatch.setenv("CWA_INGEST_FOLDER_BOOKS_FILE", str(tmp_path / "folder_books.json"))
    monkeypatch.setenv("CWA_INGEST_FOLDER_SETTLE_SECONDS", "0")

    import ingest_processor

    monkeypatch.setattr(ingest_processor, "_get_cwa_usernames", lambda: {"stefan"})
    return ingest_processor


@pytest.fixture
def ingest_dir(tmp_path):
    folder = tmp_path / "ingest"
    folder.mkdir()
    return folder


def test_book_dir_only_for_files_in_subfolders(ingest_processor, ingest_dir):
    book = ingest_dir / "Vargas"
    book.mkdir()

    assert ingest_processor.get_folder_book_dir(str(ingest_dir / "a.epub"), str(ingest_dir)) is None
    assert ingest_processor.get_folder_book_dir(str(book / "a.epub"), str(ingest_dir)) == str(book)


def test_book_dir_disabled_by_default(ingest_processor, ingest_dir, monkeypatch):
    monkeypatch.delenv("CWA_INGEST_FOLDER_AS_BOOK")
    path = ingest_dir / "Vargas" / "a.epub"

    assert ingest_processor.get_folder_book_dir(str(path), str(ingest_dir)) is None


def test_user_routing_folder_is_not_a_book_folder(ingest_processor, ingest_dir):
    user_file = ingest_dir / "Stefan" / "a.epub"
    nested_book = ingest_dir / "stefan" / "Vargas" / "a.epub"

    assert ingest_processor.get_folder_book_dir(str(user_file), str(ingest_dir)) is None
    assert ingest_processor.get_folder_book_dir(str(nested_book), str(ingest_dir)) == str(nested_book.parent)


def test_mapping_roundtrip_and_pruning(ingest_processor, tmp_path):
    kept = tmp_path / "kept"
    gone = tmp_path / "gone"
    kept.mkdir()
    gone.mkdir()

    ingest_processor.remember_folder_book(str(kept), 7)
    ingest_processor.remember_folder_book(str(gone), 8)
    gone.rmdir()

    assert ingest_processor.get_folder_book_id(str(kept)) == 7
    assert ingest_processor.get_folder_book_id(str(gone)) is None


def test_mapping_expires(ingest_processor, tmp_path, monkeypatch):
    folder = tmp_path / "book"
    folder.mkdir()
    entries = {str(folder): {"book_id": 7, "updated": int(time.time()) - 3 * 3600}}
    Path(os.environ["CWA_INGEST_FOLDER_BOOKS_FILE"]).write_text(json.dumps(entries))
    monkeypatch.setenv("CWA_INGEST_FOLDER_BOOK_TTL_HOURS", "2")

    assert ingest_processor.get_folder_book_id(str(folder)) is None


@pytest.mark.parametrize("files, target, expected", [
    (["b.mobi", "a.pdf", "c.epub"], "epub", "c.epub"),
    (["b.mobi", "a.pdf"], "epub", "b.mobi"),
    (["b.epub", "a.azw3"], "azw3", "a.azw3"),
    (["x.cbz", "y.txt"], "epub", "x.cbz"),
    ([], "epub", None),
])
def test_pick_main_file(ingest_processor, files, target, expected):
    assert ingest_processor.pick_folder_main_file(files, target) == expected


def build_processor(ingest_processor, filepath, metadata_db):
    processor = object.__new__(ingest_processor.NewBookProcessor)
    processor.filepath = str(filepath)
    processor.filename = os.path.basename(filepath)
    processor.input_format = Path(filepath).suffix[1:].lower()
    processor.metadata_db = str(metadata_db)
    return processor


def make_metadata_db(path, formats):
    import sqlite3
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT)")
        con.executemany("INSERT INTO data(book, format, name) VALUES (7, ?, 'x')", [(f,) for f in formats])


def test_attach_adds_missing_format(ingest_processor, tmp_path):
    make_metadata_db(tmp_path / "metadata.db", ["EPUB"])
    processor = build_processor(ingest_processor, tmp_path / "book.mobi", tmp_path / "metadata.db")

    with mock.patch.object(processor, "add_format_to_book") as add_mock:
        processor.attach_folder_format(7)

    add_mock.assert_called_once_with(7, str(tmp_path / "book.mobi"))


def test_attach_skips_existing_format(ingest_processor, tmp_path):
    make_metadata_db(tmp_path / "metadata.db", ["EPUB"])
    processor = build_processor(ingest_processor, tmp_path / "book.epub", tmp_path / "metadata.db")

    with mock.patch.object(processor, "add_format_to_book") as add_mock:
        processor.attach_folder_format(7)

    add_mock.assert_not_called()


def folder_nbp(ingest_processor, folder):
    nbp = mock.Mock()
    nbp.folder_book_dir = str(folder)
    nbp.target_format = "epub"
    nbp.supported_book_formats = {"epub", "mobi", "pdf"}
    nbp.supported_audiobook_formats = {"m4b"}
    nbp.ingest_ignored_formats = ["part"]
    return nbp


def test_import_folder_imports_best_file_first_then_attaches(ingest_processor, ingest_dir, monkeypatch):
    folder = ingest_dir / "Vargas"
    folder.mkdir()
    for name in ("book.mobi", "book.epub", "book.pdf", "notes.part", "cover.jpg"):
        (folder / name).write_bytes(b"x")

    calls = []

    def fake_main(path, _folder_main=False):
        calls.append((os.path.basename(path), _folder_main))
        if _folder_main:
            ingest_processor.remember_folder_book(str(folder), 7)
        return 0

    monkeypatch.setattr(ingest_processor, "main", fake_main)

    assert ingest_processor.import_folder_as_book(folder_nbp(ingest_processor, folder)) == 0
    assert calls == [("book.epub", True), ("book.mobi", False), ("book.pdf", False)]


def test_import_folder_falls_back_to_single_books(ingest_processor, ingest_dir, monkeypatch):
    folder = ingest_dir / "Broken"
    folder.mkdir()
    for name in ("book.epub", "book.mobi"):
        (folder / name).write_bytes(b"x")

    calls = []
    monkeypatch.setattr(ingest_processor, "main",
                        lambda path, _folder_main=False: calls.append((os.path.basename(path), _folder_main)) or 0)

    ingest_processor.import_folder_as_book(folder_nbp(ingest_processor, folder))

    assert calls == [("book.epub", True), ("book.mobi", True)]
