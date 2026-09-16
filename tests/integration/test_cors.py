"""Integration tests for CORS.

Browser clients cannot read a response the browser refuses to hand them, so the
demo page depends on these headers being present and correct.
"""

from fastapi.testclient import TestClient

ORIGIN = "https://example.invalid"


def test_preflight_is_answered(client: TestClient) -> None:
    # Without this, a browser never sends the POST at all.
    response = client.options(
        "/readings",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_simple_request_carries_the_allow_origin_header(client: TestClient) -> None:
    response = client.get("/healthz", headers={"Origin": ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_request_id_is_exposed_to_browser_clients(client: TestClient) -> None:
    # A header the browser can see only because it is in expose_headers.
    response = client.get("/healthz", headers={"Origin": ORIGIN})

    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_credentials_are_not_allowed(client: TestClient) -> None:
    # A wildcard origin is only legitimate while credentials are off.
    response = client.get("/healthz", headers={"Origin": ORIGIN})

    assert "access-control-allow-credentials" not in response.headers
