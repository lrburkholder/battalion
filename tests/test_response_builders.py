"""Isolation and negative-input guarantees for shared completion builders."""
import pytest

from battalion.nodes.driver import MalformedDriverOutput, extract_files
from support.responses import files_response, json_response


def test_responses_do_not_share_mutable_input_or_envelopes():
    files = {"widget.py": "VALUE = 1"}
    first = files_response(files)
    second = files_response(files, fenced=True)
    files["widget.py"] = "changed after construction"
    first["choices"][0]["message"]["content"] = "corrupted envelope"

    assert extract_files(second) == {"widget.py": "VALUE = 1"}


def test_json_builder_preserves_invalid_payloads_for_production_validation():
    response = json_response({"files": ["not a mapping"]})
    with pytest.raises(MalformedDriverOutput, match="JSON object"):
        extract_files(response)
