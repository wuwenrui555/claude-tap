def test_top_level_imports():
    import claude_tap

    # Public surface
    assert hasattr(claude_tap, "EventStream")
    assert hasattr(claude_tap, "DecisionListener")
    assert hasattr(claude_tap, "DecisionRequest")
    assert hasattr(claude_tap, "Event")
    assert hasattr(claude_tap, "ClaudeInfo")
    assert hasattr(claude_tap, "TmuxInfo")
    assert hasattr(claude_tap, "SCHEMA_VERSION")
    assert hasattr(claude_tap, "__version__")


def test_sample_consumer_compiles():
    """The reference example must at least syntactically parse."""
    import py_compile
    from pathlib import Path

    sample = Path(__file__).parent.parent / "examples" / "sample_consumer.py"
    py_compile.compile(str(sample), doraise=True)
