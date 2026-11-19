from dates.cli import main


def test_parse(capsys):
    assert main(["parse", "28 Jan 2026"]) == 0
    assert capsys.readouterr().out == "2026-01-28\n"


def test_parse_fails_with_the_reason(capsys):
    assert main(["parse", "soon"]) == 1
    assert "cannot read 'soon'" in capsys.readouterr().err


def test_find(tmp_path, capsys):
    note = tmp_path / "note.txt"
    note.write_text("Due 2026-03-01.")
    assert main(["find", str(note)]) == 0
    assert capsys.readouterr().out == "4\t2026-03-01\t2026-03-01\n"


def test_normalise(tmp_path, capsys):
    note = tmp_path / "note.txt"
    note.write_text("On 03/04/2026.")
    assert main(["--day-first", "normalise", str(note)]) == 0
    assert capsys.readouterr().out == "On 2026-04-03."
