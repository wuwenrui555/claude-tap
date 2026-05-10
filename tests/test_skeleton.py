def test_import_package():
    import claude_tap

    assert claude_tap.__version__ == "0.1.3"
