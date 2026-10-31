import json
import logging
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.modules.whatsapp.deps import get_provider, reset_provider_cache
from app.modules.whatsapp.fake import FakeProvider
from app.modules.whatsapp.gateway import GatewayProvider
from app.modules.whatsapp.provider import (
    NotOnWhatsApp,
    ProviderError,
    ProviderRejected,
    ProviderStatus,
    ProviderUnavailable,
    SendResult,
    WhatsAppProvider,
)
from app.modules.whatsapp.signature import sign_body, verify_signature

API_KEY = "super-secret-gateway-key"
PHONE = "+6281234567890"
TEXT = "Halo, tagihan SPP Anda jatuh tempo."


def make_gateway(transport: httpx.MockTransport) -> GatewayProvider:
    client = httpx.AsyncClient(transport=transport)
    return GatewayProvider(base_url="http://gateway.test", api_key=API_KEY, client=client)


def json_transport(status_code: int, payload: object) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status_code, json=payload))


# --- gateway: send -------------------------------------------------------------------------


async def test_gateway_send_posts_bearer_and_returns_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"provider_message_id": "wamid-1"})

    gateway = make_gateway(httpx.MockTransport(handler))
    result = await gateway.send(phone=PHONE, text=TEXT, client_ref="ref-1")

    assert result == SendResult(provider_message_id="wamid-1")
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert request.url.path == "/messages"
    assert str(request.url) == "http://gateway.test/messages"
    assert json.loads(request.content) == {"to": PHONE, "text": TEXT, "client_ref": "ref-1"}
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"


async def test_gateway_base_url_trailing_slash_is_tolerated() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"provider_message_id": "x"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = GatewayProvider(base_url="http://gateway.test/", api_key=API_KEY, client=client)
    await gateway.send(phone=PHONE, text=TEXT, client_ref="r")
    assert str(seen[0].url) == "http://gateway.test/messages"


async def test_gateway_404_maps_to_not_on_whatsapp() -> None:
    gateway = make_gateway(json_transport(404, {"error": "not_on_whatsapp"}))
    with pytest.raises(NotOnWhatsApp):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")


async def test_gateway_404_with_other_error_is_unavailable() -> None:
    gateway = make_gateway(json_transport(404, {"error": "something_else"}))
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")


async def test_gateway_503_and_timeout_map_to_unavailable() -> None:
    gateway = make_gateway(json_transport(503, {"error": "disconnected"}))
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")

    def raise_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    timeout_gateway = make_gateway(httpx.MockTransport(raise_timeout))
    with pytest.raises(ProviderUnavailable):
        await timeout_gateway.send(phone=PHONE, text=TEXT, client_ref="r")

    def raise_connect(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connect_gateway = make_gateway(httpx.MockTransport(raise_connect))
    with pytest.raises(ProviderUnavailable):
        await connect_gateway.send(phone=PHONE, text=TEXT, client_ref="r")


async def test_gateway_5xx_maps_to_unavailable_without_leaking_body() -> None:
    gateway = make_gateway(json_transport(500, {"error": "boom", "trace": "SECRET-TRACE"}))
    with pytest.raises(ProviderUnavailable) as info:
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")
    assert "SECRET-TRACE" not in str(info.value)
    assert API_KEY not in str(info.value)


@pytest.mark.parametrize("status_code", [401, 403])
async def test_gateway_auth_rejection_is_unavailable_with_indonesian_message(
    status_code: int,
) -> None:
    gateway = make_gateway(json_transport(status_code, {"error": "unauthorized"}))
    with pytest.raises(ProviderUnavailable) as info:
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")
    assert str(info.value) == "Kunci API gateway ditolak."
    assert API_KEY not in str(info.value)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"provider_message_id": 123},
        {"provider_message_id": ""},
        ["not", "an", "object"],
        "plain string",
    ],
)
async def test_gateway_malformed_200_body_is_unavailable(payload: object) -> None:
    gateway = make_gateway(json_transport(200, payload))
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")


async def test_gateway_non_json_200_body_is_unavailable() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"<html>oops"))
    gateway = make_gateway(transport)
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")


async def test_gateway_errors_are_provider_errors() -> None:
    assert issubclass(NotOnWhatsApp, ProviderError)
    assert issubclass(ProviderUnavailable, ProviderError)
    assert issubclass(ProviderRejected, ProviderError)
    assert not issubclass(ProviderRejected, ProviderUnavailable)


@pytest.mark.parametrize("status_code", [400, 409, 413, 422])
async def test_gateway_bad_request_is_rejected_not_unavailable(status_code: int) -> None:
    gateway = make_gateway(json_transport(status_code, {"error": "bad_request", "detail": "X"}))
    with pytest.raises(ProviderRejected) as info:
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")
    assert str(info.value) == "Permintaan ditolak gateway."


async def test_gateway_429_is_unavailable() -> None:
    gateway = make_gateway(json_transport(429, {"error": "rate_limited"}))
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")


async def test_gateway_redirect_is_a_failure_everywhere() -> None:
    def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://elsewhere.test/"}, json={})

    gateway = make_gateway(httpx.MockTransport(redirect))
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")
    with pytest.raises(ProviderUnavailable):
        await gateway.status()
    with pytest.raises(ProviderUnavailable):
        await gateway.logout()


@pytest.mark.parametrize(
    "error",
    [
        httpx.DecodingError("garbled"),
        httpx.TooManyRedirects("loop"),
        httpx.UnsupportedProtocol("ftp"),
    ],
    ids=["decoding", "redirects", "protocol"],
)
async def test_gateway_any_request_error_is_unavailable(error: httpx.RequestError) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        error.request = request
        raise error

    gateway = make_gateway(httpx.MockTransport(fail))
    with pytest.raises(ProviderUnavailable):
        await gateway.send(phone=PHONE, text=TEXT, client_ref="r")
    with pytest.raises(ProviderUnavailable):
        await gateway.status()


async def test_gateway_non_ascii_text_round_trips_through_the_json_body() -> None:
    text = "Selamat pagi Bapak Ñandu 😀"
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"provider_message_id": "wamid-2"})

    await make_gateway(httpx.MockTransport(handler)).send(phone=PHONE, text=text, client_ref="r")

    assert json.loads(seen[0].content.decode("utf-8"))["text"] == text


async def test_gateway_logs_without_a_phone_omit_the_phone_fragment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="app.modules.whatsapp.gateway")
    with pytest.raises(ProviderUnavailable):
        await make_gateway(json_transport(503, {})).status()
    with pytest.raises(ProviderRejected):
        await make_gateway(json_transport(400, {})).logout()

    messages = [r.getMessage() for r in caplog.records if r.name == "app.modules.whatsapp.gateway"]
    assert messages
    assert all("phone" not in message and "None" not in message for message in messages)


# --- gateway: status and logout ------------------------------------------------------------


@pytest.mark.parametrize("state", ["connected", "connecting", "disconnected"])
async def test_gateway_status_maps_states(state: str) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"state": state, "qr": None})

    gateway = make_gateway(httpx.MockTransport(handler))
    status = await gateway.status()

    assert status == ProviderStatus(state=state, qr=None)  # type: ignore[arg-type]
    assert seen[0].method == "GET"
    assert seen[0].url.path == "/status"
    assert seen[0].headers["Authorization"] == f"Bearer {API_KEY}"


async def test_gateway_status_with_qr() -> None:
    gateway = make_gateway(json_transport(200, {"state": "qr", "qr": "2@abcdef"}))
    assert await gateway.status() == ProviderStatus(state="qr", qr="2@abcdef")


async def test_gateway_status_malformed_or_down_is_unavailable() -> None:
    with pytest.raises(ProviderUnavailable):
        await make_gateway(json_transport(200, {"state": "weird", "qr": None})).status()
    with pytest.raises(ProviderUnavailable):
        await make_gateway(json_transport(200, {"qr": None})).status()
    with pytest.raises(ProviderUnavailable):
        await make_gateway(json_transport(200, {"state": "qr", "qr": 5})).status()
    with pytest.raises(ProviderUnavailable):
        await make_gateway(json_transport(503, {"error": "disconnected"})).status()
    with pytest.raises(ProviderUnavailable) as info:
        await make_gateway(json_transport(401, {})).status()
    assert str(info.value) == "Kunci API gateway ditolak."


async def test_gateway_logout_posts_and_accepts_204() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    gateway = make_gateway(httpx.MockTransport(handler))
    await gateway.logout()
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/logout"
    assert seen[0].headers["Authorization"] == f"Bearer {API_KEY}"

    await make_gateway(json_transport(200, {})).logout()
    with pytest.raises(ProviderUnavailable):
        await make_gateway(json_transport(503, {})).logout()


async def test_gateway_aclose_closes_the_client() -> None:
    client = httpx.AsyncClient(transport=json_transport(200, {}))
    gateway = GatewayProvider(base_url="http://gateway.test", api_key=API_KEY, client=client)
    await gateway.aclose()
    assert client.is_closed


async def test_phone_is_masked_in_gateway_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="app.modules.whatsapp.gateway")

    ok = make_gateway(json_transport(200, {"provider_message_id": "wamid-1"}))
    await ok.send(phone=PHONE, text=TEXT, client_ref="ref-1")
    missing = make_gateway(json_transport(404, {"error": "not_on_whatsapp"}))
    with pytest.raises(NotOnWhatsApp):
        await missing.send(phone=PHONE, text=TEXT, client_ref="ref-2")
    down = make_gateway(json_transport(503, {"error": "disconnected"}))
    with pytest.raises(ProviderUnavailable):
        await down.send(phone=PHONE, text=TEXT, client_ref="ref-3")

    logged = " | ".join(
        record.getMessage()
        for record in caplog.records
        if record.name == "app.modules.whatsapp.gateway"
    )
    assert "7890" in logged
    assert PHONE not in logged
    assert "81234567890" not in logged
    assert "6281234" not in logged
    assert TEXT not in logged
    assert "tagihan" not in logged
    assert API_KEY not in logged


# --- fake provider -------------------------------------------------------------------------


async def test_fake_provider_records_and_fails_on_demand() -> None:
    fake = FakeProvider()

    first = await fake.send(phone=PHONE, text="satu", client_ref="r1")
    second = await fake.send(phone="+6289999999999", text="dua", client_ref="r2")
    assert first.provider_message_id == "fake-1"
    assert second.provider_message_id == "fake-2"
    assert [(m.phone, m.text, m.client_ref, m.provider_message_id) for m in fake.sent] == [
        (PHONE, "satu", "r1", "fake-1"),
        ("+6289999999999", "dua", "r2", "fake-2"),
    ]

    fake.fail_next(ProviderUnavailable("boom"), times=2)
    for ref in ("r3", "r4"):
        with pytest.raises(ProviderUnavailable):
            await fake.send(phone=PHONE, text="gagal", client_ref=ref)
    assert len(fake.sent) == 2  # failed sends are not recorded

    third = await fake.send(phone=PHONE, text="tiga", client_ref="r5")
    assert third.provider_message_id == "fake-3"
    assert len(fake.sent) == 3


async def test_fake_provider_can_reject_a_send() -> None:
    fake = FakeProvider()
    fake.fail_next(ProviderRejected("Permintaan ditolak gateway."))
    with pytest.raises(ProviderRejected):
        await fake.send(phone=PHONE, text="x", client_ref="r")
    assert fake.sent == []
    await fake.send(phone=PHONE, text="x", client_ref="r")
    assert len(fake.sent) == 1


async def test_fake_provider_fail_next_defaults_to_once() -> None:
    fake = FakeProvider()
    fake.fail_next(RuntimeError("once"))
    with pytest.raises(RuntimeError):
        await fake.send(phone=PHONE, text="x", client_ref="r")
    await fake.send(phone=PHONE, text="x", client_ref="r")


async def test_fake_provider_unregistered_numbers_raise_not_on_whatsapp() -> None:
    fake = FakeProvider()
    fake.unregistered.add(PHONE)
    with pytest.raises(NotOnWhatsApp):
        await fake.send(phone=PHONE, text="x", client_ref="r")
    assert fake.sent == []
    await fake.send(phone="+6281111111111", text="x", client_ref="r")


async def test_fake_provider_not_connected_raises_unavailable_and_logout() -> None:
    fake = FakeProvider()
    assert await fake.status() == ProviderStatus(state="connected", qr=None)

    fake.state = "connecting"
    with pytest.raises(ProviderUnavailable):
        await fake.send(phone=PHONE, text="x", client_ref="r")
    assert (await fake.status()).state == "connecting"

    fake.state = "connected"
    await fake.logout()
    assert fake.state == "disconnected"
    assert (await fake.status()).state == "disconnected"
    with pytest.raises(ProviderUnavailable):
        await fake.send(phone=PHONE, text="x", client_ref="r")


def test_providers_satisfy_the_protocol() -> None:
    client = httpx.AsyncClient(transport=json_transport(200, {}))
    providers: list[WhatsAppProvider] = [
        FakeProvider(),
        GatewayProvider(base_url="http://g", api_key="k", client=client),
    ]
    assert len(providers) == 2


# --- signature -----------------------------------------------------------------------------


def test_signature_roundtrip_and_tamper_detection() -> None:
    secret = "webhook-secret"
    body = b'{"event":"delivered","id":"wamid-1"}'
    header = sign_body(secret, body)

    assert header.startswith("sha256=")
    assert len(header) == len("sha256=") + 64
    assert verify_signature(secret, body, header) is True

    flipped = bytes([body[0] ^ 1]) + body[1:]
    assert verify_signature(secret, flipped, header) is False
    assert verify_signature("other-secret", body, header) is False
    assert verify_signature(secret, body, header.removeprefix("sha256=")) is False
    assert verify_signature(secret, body, "sha1=" + header.removeprefix("sha256=")) is False
    assert verify_signature(secret, body, None) is False
    assert verify_signature(secret, body, "") is False
    assert verify_signature(secret, body, "sha256=") is False
    assert verify_signature(secret, body, header[:-2]) is False  # wrong length
    assert verify_signature(secret, body, header + "00") is False
    assert verify_signature(secret, body, "sha256=" + "zz" * 32) is False  # non-hex
    assert verify_signature(secret, body, "sha256=" + "é" * 64) is False  # non-ascii, never raises


def test_signature_is_strictly_lowercase_hex() -> None:
    secret = "webhook-secret"
    body = b"payload"
    header = sign_body(secret, body)
    assert verify_signature(secret, body, header) is True
    # Strict: the gateway must send lowercase hex, an uppercase digest is rejected.
    upper = "sha256=" + header.removeprefix("sha256=").upper()
    assert upper != header
    assert verify_signature(secret, body, upper) is False


@pytest.mark.parametrize(
    ("body", "header"),
    [
        (b"payload", b"sha256=00"),
        (b"payload", 123),
        (b"payload", ["sha256=00"]),
        ("payload", sign_body("webhook-secret", b"payload")),
        (None, sign_body("webhook-secret", b"payload")),
        (123, sign_body("webhook-secret", b"payload")),
    ],
    ids=["bytes-header", "int-header", "list-header", "str-body", "none-body", "int-body"],
)
def test_signature_wrong_types_are_false_and_never_raise(body: Any, header: Any) -> None:
    assert verify_signature("webhook-secret", body, header) is False


def test_signature_known_vector() -> None:
    # HMAC-SHA256("key", "The quick brown fox jumps over the lazy dog")
    expected = "sha256=f7bc83f430538424b13298e6aa6fb143ef4d59a14946175997479dbc2d1a3cd8"
    assert sign_body("key", b"The quick brown fox jumps over the lazy dog") == expected


# --- get_provider --------------------------------------------------------------------------


@pytest.mark.parametrize("run", [1, 2])
async def test_process_wide_fake_provider_starts_empty_in_every_test(run: int) -> None:
    provider = get_provider()
    assert isinstance(provider, FakeProvider)
    assert provider.sent == [], f"leaked from an earlier test (run {run})"
    await provider.send(phone=PHONE, text="x", client_ref=f"leak-{run}")


def test_get_provider_returns_cached_fake_by_default() -> None:
    assert settings.wa_provider == "fake"
    provider = get_provider()
    assert isinstance(provider, FakeProvider)
    assert get_provider() is provider

    reset_provider_cache()
    assert get_provider() is not provider


def test_get_provider_builds_gateway_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wa_provider", "gateway")
    monkeypatch.setattr(settings, "wa_gateway_url", "http://gw.internal:8095")
    monkeypatch.setattr(settings, "wa_gateway_api_key", SecretStr("abc"))
    provider = get_provider()
    assert isinstance(provider, GatewayProvider)
    assert get_provider() is provider


@pytest.mark.parametrize(
    ("url", "key"),
    [("", SecretStr("abc")), ("http://gw", None), ("http://gw", SecretStr(""))],
)
def test_get_provider_gateway_misconfiguration_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    key: SecretStr | None,
) -> None:
    monkeypatch.setattr(settings, "wa_provider", "gateway")
    monkeypatch.setattr(settings, "wa_gateway_url", url)
    monkeypatch.setattr(settings, "wa_gateway_api_key", key)
    with pytest.raises(RuntimeError, match="WA_GATEWAY"):
        get_provider()
