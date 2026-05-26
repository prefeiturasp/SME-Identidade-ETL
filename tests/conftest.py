import django
import os
import socket
import pytest
import httpx

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "etl_ms.settings_test")

@pytest.fixture(autouse=True)
def no_network(monkeypatch):

    def guard(*args, **kwargs):
        raise RuntimeError(
            "Rede não permitida em testes"
        )

    monkeypatch.setattr(socket, "socket", guard)

    monkeypatch.setattr(
        httpx.Client,
        "request",
        guard,
    )

    monkeypatch.setattr(
        httpx.AsyncClient,
        "request",
        guard,
    )