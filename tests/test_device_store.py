"""Tests for tools/devices/device_store.py -- device registration, token
verification (never storing the raw token), and revocation.
"""
from __future__ import annotations

import pytest

from tools.devices.device_store import DeviceStore


def _store(tmp_path) -> DeviceStore:
    return DeviceStore(tmp_path / "devices.json")


@pytest.mark.asyncio
async def test_create_device_returns_a_verifiable_token(tmp_path):
    store = _store(tmp_path)

    device, raw_token = await store.create_device("test-phone")

    assert device.name == "test-phone"
    assert device.last_seen_at is None
    verified = await store.verify_token(raw_token)
    assert verified is not None
    assert verified.id == device.id


@pytest.mark.asyncio
async def test_raw_token_is_never_persisted(tmp_path):
    store = _store(tmp_path)
    _, raw_token = await store.create_device("test-phone")

    raw_file_contents = (tmp_path / "devices.json").read_text(encoding="utf-8")

    assert raw_token not in raw_file_contents


@pytest.mark.asyncio
async def test_verify_token_rejects_unknown_token(tmp_path):
    store = _store(tmp_path)
    await store.create_device("test-phone")

    assert await store.verify_token("not-a-real-token") is None


@pytest.mark.asyncio
async def test_verify_token_updates_last_seen_at(tmp_path):
    store = _store(tmp_path)
    _, raw_token = await store.create_device("test-phone")

    verified = await store.verify_token(raw_token)

    assert verified.last_seen_at is not None


@pytest.mark.asyncio
async def test_revoked_device_token_no_longer_verifies(tmp_path):
    store = _store(tmp_path)
    device, raw_token = await store.create_device("test-phone")

    await store.revoke_device(device.id)

    assert await store.verify_token(raw_token) is None
    assert await store.list_devices() == []


@pytest.mark.asyncio
async def test_revoke_unknown_device_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="No such device"):
        await store.revoke_device("nonexistent")


@pytest.mark.asyncio
async def test_list_devices_returns_every_registered_device(tmp_path):
    store = _store(tmp_path)
    await store.create_device("phone-a")
    await store.create_device("phone-b")

    devices = await store.list_devices()

    assert sorted(d.name for d in devices) == ["phone-a", "phone-b"]
