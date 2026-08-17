from importlib import import_module
from typing import cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

app: FastAPI = import_module("app.main").app
client = TestClient(app)


def test_docs_are_accessible() -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert "Swagger UI" in response.text


def test_health_check_is_hidden_from_openapi_schema() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = cast(dict[str, object], response.json())
    paths = cast(dict[str, object], payload.get("paths", {}))
    assert "/health" not in paths
    assert "/api/v1/examples" in paths


def test_example_module_works() -> None:
    response = client.get("/api/v1/examples")

    assert response.status_code == 200
    body = cast(dict[str, object], response.json())
    assert body["message"] == "Example module is ready."


def test_health_check_works() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
