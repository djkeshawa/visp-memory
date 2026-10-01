"""REST and file imports share the public validation contract."""
import pytest

from visp_memory.core import memory_import_export as portability


def test_public_import_validation_helpers_return_checked_values():
    data = {"version": "3.0", "memories": {"episodic": [{"content": "Observation"}]}}
    assert portability.require_dict(data, "root") is data
    rows = data["memories"]["episodic"]
    assert portability.require_list(rows, "memories.episodic") is rows
    assert portability.validate_import_data(data) is data


@pytest.mark.parametrize("method,value,field", [
    ("require_dict", [], "root"),
    ("require_list", {}, "memories.episodic"),
])
def test_public_shape_helpers_reject_invalid_shapes(method, value, field):
    with pytest.raises(ValueError, match=field):
        getattr(portability, method)(value, field)


def test_public_import_validator_rejects_invalid_content():
    with pytest.raises(ValueError, match="content"):
        portability.validate_import_data({"memories": {"episodic": [{"content": 123}]}})
