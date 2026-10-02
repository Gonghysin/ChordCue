from chordcue.settings import Settings


def test_settings_are_isolated_and_persistent(tmp_path):
    path = tmp_path / "preferences.ini"
    settings = Settings(path)
    settings.set_value("test", "中文")
    settings.sync()
    assert Settings(path).value("test") == "中文"
    assert Settings(tmp_path / "other.ini").value("test") is None
