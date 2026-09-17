import pytest

from local_dictation.processors import ProcessorExistsError, ProcessorRegistry


def test_plain_text_is_always_available(tmp_path):
    assert [p.key for p in ProcessorRegistry(tmp_path).list_processors()] == [
        "plain_text"
    ]


def test_discovery_ignores_private_and_non_python_files(tmp_path):
    (tmp_path / "sentence_case.py").write_text("def process(text): return text")
    (tmp_path / "_helper.py").write_text("")
    (tmp_path / "notes.txt").write_text("")

    processors = ProcessorRegistry(tmp_path).list_processors()

    assert [p.key for p in processors] == ["plain_text", "sentence_case"]
    assert processors[1].display_name == "Sentence Case"


def test_create_writes_importable_template(tmp_path):
    info = ProcessorRegistry(tmp_path).create("Sentence Case")

    assert info.key == "sentence_case"
    assert info.path is not None
    assert "def process(text: str) -> str:" in info.path.read_text()


def test_create_rejects_invalid_or_existing_name(tmp_path):
    registry = ProcessorRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.create("!!!")
    registry.create("Clean Up")
    with pytest.raises(ProcessorExistsError):
        registry.create("clean-up")


def test_processor_transforms_text(tmp_path):
    (tmp_path / "upper.py").write_text(
        "def process(text: str) -> str:\n    return text.upper()\n"
    )

    result = ProcessorRegistry(tmp_path).apply("upper", "hello")

    assert result.text == "HELLO"
    assert result.error is None


@pytest.mark.parametrize(
    "source",
    [
        "def process(text):\n    raise RuntimeError('bad')\n",
        "value = 1\n",
        "def process(text):\n    return 42\n",
    ],
)
def test_invalid_processor_falls_back_to_raw_text(tmp_path, source):
    (tmp_path / "broken.py").write_text(source)

    result = ProcessorRegistry(tmp_path).apply("broken", "raw")

    assert result.text == "raw"
    assert result.error is not None


def test_missing_processor_falls_back_to_raw_text(tmp_path):
    result = ProcessorRegistry(tmp_path).apply("missing", "raw")

    assert result.text == "raw"
    assert result.error is not None
