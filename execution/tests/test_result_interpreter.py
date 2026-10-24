
from shared.result_interpreter import DeterministicResultInterpreter


def test_readme_summary_uses_actual_content():
    interpreter = DeterministicResultInterpreter()

    result = interpreter.interpret(
        "Summarize README",
        "filesystem.read_file",
        "# Phanes-AI-OS\nAn AI-powered operating layer for Windows.",
    )

    assert "Phanes-AI-OS" in result
    assert "AI-powered operating layer for Windows" in result


def test_empty_output_is_reported_honestly():
    interpreter = DeterministicResultInterpreter()

    result = interpreter.interpret(
        "Read README",
        "filesystem.read_file",
        "  ",
    )

    assert "no content" in result.lower()


def test_directory_listing_counts_files_and_directories():
    interpreter = DeterministicResultInterpreter()

    result = interpreter.interpret(
        "List files",
        "filesystem.list_directory",
        "[{'name': 'README.md', 'type': 'file'}, "
        "{'name': 'shared', 'type': 'directory'}]",
    )

    assert "1 file(s)" in result
    assert "1 directorie(s)" in result
