"""Two-version OpenAPI schema generation (issue #42)."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
import yaml
from drf_spectacular.generators import SchemaGenerator

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_V1 = REPO_ROOT / "docs" / "openapi.yaml"
OPENAPI_V2 = REPO_ROOT / "docs" / "openapi-v2.yaml"


def generate(version):
    return SchemaGenerator(api_version=version).get_schema(request=None, public=True)


def parameter_names(operation):
    return {parameter["name"] for parameter in operation.get("parameters", [])}


def operations(schema):
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if isinstance(operation, dict) and "responses" in operation:
                yield path, method, operation


def api_operations(schema):
    # Versioned API operations only — excludes unversioned routes (health) that
    # appear in every schema and do not implement the namespace contract.
    for path, method, operation in operations(schema):
        if path.startswith("/api/"):
            yield path, method, operation


@pytest.fixture(scope="module")
def v1_schema():
    return generate("v1")


@pytest.fixture(scope="module")
def v2_schema():
    return generate("v2")


def test_versions_use_their_own_path_prefix(v1_schema, v2_schema):
    # Unversioned health endpoints appear in both; the versioned API surface must
    # not leak the other version's prefix.
    assert "/api/v1/tags" in v1_schema["paths"]
    assert not any(path.startswith("/api/v2/") for path in v1_schema["paths"])
    assert "/api/v2/tags" in v2_schema["paths"]
    assert not any(path.startswith("/api/v1/") for path in v2_schema["paths"])


def test_namespace_headers_are_v2_only(v1_schema, v2_schema):
    assert not any(
        "X-Namespace-Type" in parameter_names(op) for *_, op in api_operations(v1_schema)
    )
    assert all("X-Namespace-Type" in parameter_names(op) for *_, op in api_operations(v2_schema))
    assert all("X-Namespace-ID" in parameter_names(op) for *_, op in api_operations(v2_schema))


def test_namespace_params_only_on_versioned_paths(v2_schema):
    # Unversioned routes (health) must not advertise the namespace contract.
    for path, _method, operation in operations(v2_schema):
        has_namespace = "X-Namespace-Type" in parameter_names(operation)
        assert has_namespace == path.startswith("/api/v2/"), path


def test_namespace_operations_document_application_id(v2_schema):
    # A namespaced request is rejected without an application_id, so every
    # namespace-capable v2 operation must document the query parameter — otherwise
    # generated clients cannot construct a valid namespaced call.
    for path, method, operation in api_operations(v2_schema):
        if "X-Namespace-Type" not in parameter_names(operation):
            continue
        query = {p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"}
        assert "application_id" in query, (path, method)


def test_include_global_is_v2_read_only(v1_schema, v2_schema):
    assert not any("include_global" in parameter_names(op) for *_, op in api_operations(v1_schema))
    reads = [op for _path, method, op in api_operations(v2_schema) if method == "get"]
    writes = [op for _path, method, op in api_operations(v2_schema) if method != "get"]
    assert reads and all("include_global" in parameter_names(op) for op in reads)
    assert not any("include_global" in parameter_names(op) for op in writes)


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_operation_ids_are_unique(version):
    schema = generate(version)
    ids = [op["operationId"] for *_, op in operations(schema) if "operationId" in op]
    duplicates = [name for name, count in Counter(ids).items() if count > 1]
    assert duplicates == []


def test_both_contract_files_are_committed_and_versioned():
    v1 = yaml.safe_load(OPENAPI_V1.read_text())
    v2 = yaml.safe_load(OPENAPI_V2.read_text())
    # Schema version mirrors the package version for both; the URL contract major
    # (v1 vs v2) is carried by the path prefix, not info.version.
    assert v1["info"]["version"] == v2["info"]["version"]
    assert any(path.startswith("/api/v2/") for path in v2["paths"])
