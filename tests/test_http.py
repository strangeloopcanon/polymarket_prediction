from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest

from polymarket_watch.http import HttpClient


class _FakeResp(io.BytesIO):
    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        return None

    def read(self, *args, **kwargs) -> bytes:  # noqa: ANN002, ANN003
        return super().read()


def test_httpclient_rejects_non_https() -> None:
    client = HttpClient()
    with pytest.raises(ValueError):
        client.get_json("http://example.com")


def test_httpclient_get_json_parses_body() -> None:
    client = HttpClient()
    body = json.dumps({"ok": True}).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=_FakeResp(body)):
        data = client.get_json("https://example.com/api", params={"a": 1})
    assert data == {"ok": True}


def test_httpclient_post_json_sends_payload() -> None:
    client = HttpClient()

    def _fake_urlopen(req, timeout):  # noqa: ANN001, ANN201
        assert req.get_method() == "POST"
        assert req.full_url.startswith("https://example.com/")
        return _FakeResp(b"")

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        client.post_json("https://example.com/webhook", {"hello": "world"})
