"""File-drop source: stack order, resume after a jam, and removing uploaded files."""

from pathlib import Path

import cv2
import numpy as np

from scan_agent.capture.filedrop import FolderSource


def write_pages(folder, names):
    for i, name in enumerate(names):
        cv2.imwrite(str(folder / name), np.full((40, 30), 255 - i, np.uint8))


def test_pages_come_in_name_order_and_resume_from_a_sequence(tmp_path):
    write_pages(tmp_path, ["page-002.png", "page-001.png", "page-003.png"])
    source = FolderSource(tmp_path)
    assert [Path(p.source).name for p in source.pages()] == [
        "page-001.png", "page-002.png", "page-003.png"]
    assert [p.sequence for p in FolderSource(tmp_path).pages(start_sequence=2)] == [2, 3]


def test_delete_consumed_removes_only_files_that_were_read(tmp_path):
    write_pages(tmp_path, ["page-001.png", "page-002.png"])
    (tmp_path / "notes.txt").write_text("not a scan", encoding="utf-8")
    source = FolderSource(tmp_path)
    assert len(list(source.pages())) == 2
    assert source.delete_consumed() == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["notes.txt"]


def test_nothing_is_deleted_before_the_batch_is_read(tmp_path):
    write_pages(tmp_path, ["page-001.png"])
    source = FolderSource(tmp_path)
    assert source.delete_consumed() == 0
    assert (tmp_path / "page-001.png").exists()
