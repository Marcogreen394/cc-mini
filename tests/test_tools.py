import pytest
from mini_claude.tools.file_read import FileReadTool
from mini_claude.tools.glob_tool import GlobTool


@pytest.fixture
def tmp_file(tmp_path):
    f = tmp_path / "sample.txt"
    f.write_text("line one\nline two\nline three\n")
    return str(f)


def test_file_read_returns_numbered_content(tmp_file):
    result = FileReadTool().execute(file_path=tmp_file)
    assert not result.is_error
    assert "1\tline one" in result.content
    assert "2\tline two" in result.content


def test_file_read_respects_offset_and_limit(tmp_file):
    result = FileReadTool().execute(file_path=tmp_file, offset=1, limit=1)
    assert not result.is_error
    assert "line two" in result.content
    assert "line one" not in result.content


def test_file_read_missing_file():
    result = FileReadTool().execute(file_path="/nonexistent/path/file.txt")
    assert result.is_error
    assert "not found" in result.content.lower() or "no such" in result.content.lower()


def test_file_read_is_read_only():
    assert FileReadTool().is_read_only() is True


def test_glob_finds_files(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("x")
    (tmp_path / "c.txt").write_text("x")

    result = GlobTool().execute(pattern="*.py", path=str(tmp_path))
    assert not result.is_error
    assert "a.py" in result.content
    assert "b.py" in result.content
    assert "c.txt" not in result.content


def test_glob_no_matches(tmp_path):
    result = GlobTool().execute(pattern="*.xyz", path=str(tmp_path))
    assert not result.is_error
    assert "No files found" in result.content


def test_glob_missing_dir():
    result = GlobTool().execute(pattern="*.py", path="/no/such/dir")
    assert result.is_error


def test_glob_is_read_only():
    assert GlobTool().is_read_only() is True
