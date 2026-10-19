
import pytest

from execution.executor import Executor
from execution.filesystem import (
    FilesystemToolError,
    FilesystemTools,
    register_filesystem_tools,
)
from execution.tool_registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "hello.txt").write_text(
        "Hello PHANES!", encoding="utf-8"
    )
    (tmp_path / "subfolder").mkdir()
    (tmp_path / "subfolder" / "nested.txt").write_text(
        "Nested file", encoding="utf-8"
    )

    return tmp_path


@pytest.fixture
def filesystem(workspace):
    return FilesystemTools(workspace)


def test_list_directory(filesystem):
    entries = filesystem.list_directory(".")

    names = {entry["name"] for entry in entries}

    assert "hello.txt" in names
    assert "subfolder" in names


def test_list_directory_identifies_entry_types(filesystem):
    entries = filesystem.list_directory(".")

    by_name = {entry["name"]: entry["type"] for entry in entries}

    assert by_name["hello.txt"] == "file"
    assert by_name["subfolder"] == "directory"


def test_read_file(filesystem):
    assert filesystem.read_file("hello.txt") == "Hello PHANES!"


def test_read_nested_file(filesystem):
    assert filesystem.read_file("subfolder/nested.txt") == "Nested file"


def test_file_info(filesystem):
    info = filesystem.file_info("hello.txt")

    assert info["name"] == "hello.txt"
    assert info["type"] == "file"
    assert info["size_bytes"] == len(b"Hello PHANES!")


def test_reject_absolute_path(filesystem, workspace):
    with pytest.raises(FilesystemToolError):
        filesystem.read_file(str(workspace / "hello.txt"))


def test_reject_path_traversal(filesystem, workspace):
    outside_file = workspace.parent / "outside.txt"
    outside_file.write_text("Do not read", encoding="utf-8")

    with pytest.raises(FilesystemToolError):
        filesystem.read_file("../outside.txt")


def test_reject_missing_file(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.read_file("missing.txt")


def test_reject_directory_as_file(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.read_file("subfolder")


def test_reject_oversized_file(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.read_file("hello.txt", max_bytes=4)


def test_reject_invalid_size_limit(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.read_file("hello.txt", max_bytes=0)


def test_register_and_execute_filesystem_tool(workspace):
    registry = ToolRegistry()
    register_filesystem_tools(registry, workspace)
    executor = Executor(registry)

    result = executor.execute(
        "filesystem.read_file",
        {"path": "hello.txt"},
    )

    assert result.success is True
    assert result.output == "Hello PHANES!"


def test_filesystem_error_is_handled_by_executor(workspace):
    registry = ToolRegistry()
    register_filesystem_tools(registry, workspace)
    executor = Executor(registry)

    result = executor.execute(
        "filesystem.read_file",
        {"path": "../outside.txt"},
    )

    assert result.success is False
    assert result.error_code == "INTERNAL_ERROR"


def test_empty_path_is_rejected(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.list_directory(" ")


def test_create_file(filesystem, workspace):
    result = filesystem.create_file("new.txt", "Hello PHANES!")

    assert result["created"] is True
    assert result["bytes_written"] == len(b"Hello PHANES!")
    assert (workspace / "new.txt").read_text(encoding="utf-8") == "Hello PHANES!"


def test_create_file_does_not_overwrite_existing_file(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.create_file("hello.txt", "Overwrite attempt")

    assert filesystem.read_file("hello.txt") == "Hello PHANES!"


def test_write_file_creates_new_file(filesystem, workspace):
    result = filesystem.write_file("written.txt", "New content")

    assert result["written"] is True
    assert result["bytes_written"] == len(b"New content")
    assert (workspace / "written.txt").read_text(encoding="utf-8") == "New content"


def test_write_file_overwrites_existing_file(filesystem):
    result = filesystem.write_file("hello.txt", "Updated content")

    assert result["written"] is True
    assert filesystem.read_file("hello.txt") == "Updated content"


def test_delete_file(filesystem, workspace):
    result = filesystem.delete_file("hello.txt")

    assert result["deleted"] is True
    assert not (workspace / "hello.txt").exists()


def test_delete_file_rejects_missing_file(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.delete_file("missing.txt")


def test_delete_file_rejects_directory(filesystem):
    with pytest.raises(FilesystemToolError):
        filesystem.delete_file("subfolder")


def test_mutating_filesystem_tools_require_approval(workspace):
    registry = ToolRegistry()
    register_filesystem_tools(registry, workspace)
    executor = Executor(registry)

    operations = [
        ("filesystem.create_file", {"path": "created.txt", "content": "test"}),
        ("filesystem.write_file", {"path": "hello.txt", "content": "changed"}),
        ("filesystem.delete_file", {"path": "hello.txt"}),
    ]

    for tool_name, arguments in operations:
        result = executor.execute(tool_name, arguments)
        assert result.success is False, f"{tool_name} unexpectedly succeeded"
        assert result.error_code == "APPROVAL_REQUIRED"