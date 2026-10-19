"""Filesystem symlink-boundary tests for PHANES."""

import pytest

from execution.filesystem import FilesystemToolError, FilesystemTools


def make_symlink_or_skip(link, target, *, target_is_directory=False):
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlink creation is unavailable on this system: {exc}")


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "safe.txt").write_text("safe content", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside content", encoding="utf-8")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    return root, outside_file, outside_dir


@pytest.fixture
def filesystem(workspace):
    root, _, _ = workspace
    return FilesystemTools(root)


def test_read_file_rejects_symlink_to_outside_file(workspace, filesystem):
    root, outside_file, _ = workspace
    make_symlink_or_skip(root / "escape.txt", outside_file)

    with pytest.raises(FilesystemToolError):
        filesystem.read_file("escape.txt")


def test_write_file_rejects_symlink_to_outside_file(workspace, filesystem):
    root, outside_file, _ = workspace
    make_symlink_or_skip(root / "escape.txt", outside_file)

    with pytest.raises(FilesystemToolError):
        filesystem.write_file("escape.txt", "overwritten")

    assert outside_file.read_text(encoding="utf-8") == "outside content"


def test_delete_file_rejects_symlink_to_outside_file(workspace, filesystem):
    root, outside_file, _ = workspace
    make_symlink_or_skip(root / "escape.txt", outside_file)

    with pytest.raises(FilesystemToolError):
        filesystem.delete_file("escape.txt")

    assert outside_file.exists()
    assert outside_file.read_text(encoding="utf-8") == "outside content"


def test_create_file_rejects_path_through_symlinked_outside_directory(
    workspace, filesystem
):
    root, _, outside_dir = workspace
    make_symlink_or_skip(
        root / "outside_link",
        outside_dir,
        target_is_directory=True,
    )

    with pytest.raises(FilesystemToolError):
        filesystem.create_file("outside_link/new.txt", "must not escape")

    assert not (outside_dir / "new.txt").exists()


def test_write_file_rejects_path_through_symlinked_outside_directory(
    workspace, filesystem
):
    root, _, outside_dir = workspace
    make_symlink_or_skip(
        root / "outside_link",
        outside_dir,
        target_is_directory=True,
    )

    with pytest.raises(FilesystemToolError):
        filesystem.write_file("outside_link/new.txt", "must not escape")

    assert not (outside_dir / "new.txt").exists()


def test_symlink_to_file_inside_workspace_stays_inside(workspace, filesystem):
    root, _, _ = workspace
    target = root / "safe.txt"
    make_symlink_or_skip(root / "safe_link.txt", target)

    # A symlink resolving to a target within the workspace is not an escape.
    assert filesystem.read_file("safe_link.txt") == "safe content"
