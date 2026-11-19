import asyncio
from collections import deque
import json

import httpx
import pytest
from pydantic import ValidationError

from app.hotkeys import ACTIONS, HotkeyConfig, HotkeyConflict, RadioHotkeys


class Native:
    def __init__(self):
        self.registered = {}
        self.occupied = set()
        self.events = deque()

    def add(self, ident, combo):
        if combo in self.occupied:
            return False
        self.registered[ident] = combo
        return True

    def remove(self, ident):
        del self.registered[ident]

    def pending(self):
        while self.events:
            yield self.events.popleft()


def test_configuration_rejects_ordinary_keys_and_duplicate_bindings():
    for opts in (
        {'toggle': 'P'}, {'toggle': 'Win+Shift+P'}, {'toggle': 'Ctrl+Alt+F12'},
        {'next': 'Ctrl+Alt+P'}, {'volume_up': '↑'}, {'volume_down': 'Ctrl+Alt+P'},
    ):
        with pytest.raises(ValidationError):
            HotkeyConfig(**opts)


@pytest.mark.parametrize('enabled', [True, False])
def test_legacy_configuration_preserves_custom_bindings_when_volume_defaults_collide(tmp_path, enabled):
    path = tmp_path / 'hotkeys.json'
    original = {
        'enabled': enabled,
        'toggle': 'Ctrl+Shift+J',
        'previous': 'Ctrl+Alt+↑',
        'next': 'Ctrl+Alt+↓',
    }
    path.write_text(json.dumps(original), encoding='utf-8')
    native = Native()
    radio = RadioHotkeys(path, native)
    expected = {**original, 'volume_up': 'Ctrl+Shift+↑', 'volume_down': 'Ctrl+Shift+↓'}
    assert radio.status()['config'] == expected
    assert json.loads(path.read_text(encoding='utf-8')) == original
    radio.save(radio.config)
    assert json.loads(path.read_text(encoding='utf-8')) == expected


def test_conflicting_configuration_keeps_live_keys_and_saved_preferences(tmp_path):
    async def scenario():
        path = tmp_path / 'hotkeys.json'
        native = Native()
        radio = RadioHotkeys(path, native)
        radio.connect('first', False, 1)
        try:
            saved = HotkeyConfig(toggle='Ctrl+Shift+J')
            radio.save(saved)
            previous = native.registered.copy()
            native.occupied.add('Alt+Shift+F10')
            with pytest.raises(HotkeyConflict, match='上一首'):
                radio.save(HotkeyConfig(toggle='Ctrl+Shift+K', previous='Alt+Shift+F10'))
            with pytest.raises(HotkeyConflict, match='降低音量'):
                radio.save(HotkeyConfig(volume_up='Ctrl+Shift+↑', volume_down='Alt+Shift+F10'))
            assert native.registered == previous
            assert RadioHotkeys(path, native).config == saved
            swapped = HotkeyConfig(toggle=saved.previous, previous=saved.toggle)
            assert radio.save(swapped)['active'] == list(ACTIONS)
            assert native.registered == previous
            radio.save(HotkeyConfig(enabled=False))
            assert not native.registered
            radio.disconnect('first', radio.clients['first'][0])
            enabled = radio.save(saved)
            assert not enabled['active']
            radio.connect('first', False, 2)
            assert radio.status()['active'] == list(ACTIONS)
        finally:
            radio.stop()

    asyncio.run(scenario())


def test_events_pick_one_playing_tab_then_release_keys_after_last_disconnect(tmp_path):
    async def scenario():
        native = Native()
        radio = RadioHotkeys(tmp_path / 'hotkeys.json', native)
        first = radio.connect('first', True, 3)
        second = radio.connect('second', False, 5)
        try:
            for action in ('toggle', 'volume_up', 'volume_down'):
                native.events.append(radio.registered[getattr(radio.config, action)])
                assert (await asyncio.wait_for(first.get(), 1))[0] == action
                assert second.empty()
            radio.activate('first', False, 6)
            radio.activate('first', True, 2)
            radio.activate('second', False, 7)
            native.events.append(radio.registered[radio.config.toggle])
            assert (await asyncio.wait_for(second.get(), 1))[0] == 'toggle'
            assert first.empty()
            radio.disconnect('first', first)
            radio.disconnect('second', second)
            await asyncio.sleep(1.1)
            assert not native.registered
        finally:
            radio.stop()

    asyncio.run(scenario())


def test_shortcut_api_keeps_local_access_boundary(tmp_path):
    from app.main import create_app

    async def scenario():
        app = create_app()
        app.state.hotkeys = RadioHotkeys(tmp_path / 'keys.json', Native())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as client:
            assert (await client.get('/api/hotkeys')).status_code == 401
            assert (await client.get('/api/local-access')).status_code == 200
            foreign = {'origin': 'http://127.0.0.1:9999'}
            assert (await client.put('/api/hotkeys', json={}, headers=foreign)).status_code == 403
            assert (await client.get('/api/hotkeys')).json()['config']['toggle'] == 'Ctrl+Alt+P'
            assert (await client.put('/api/hotkeys', json={'next': 'P'})).status_code == 422
            body = {'toggle': 'Ctrl+Shift+F8'}
            assert (await client.put('/api/hotkeys', json=body)).json()['config']['toggle'] == 'Ctrl+Shift+F8'
            assert (await client.post('/api/hotkeys/target', json={'tab': 'invalid', 'playing': True, 'used_at': 0})).status_code == 422
            token = 'd6ef281d-ff87-4615-870a-58caeac67005'
            radio = app.state.hotkeys
            radio.connect('first', False, 1)
            try:
                assert len(radio.registered) == 5
                assert (await client.post('/api/hotkeys/capture', json={'token': token}, headers=foreign)).status_code == 403
                assert (await client.post('/api/hotkeys/capture', json={'token': token})).status_code == 200
                assert not radio.registered
                assert (await client.put('/api/hotkeys', json=body)).status_code == 409
                assert (await client.delete('/api/hotkeys/capture', params={'token': '8d705636-0175-4661-b906-ec7a949b7340'})).status_code == 200
                assert not radio.registered
                assert (await client.delete('/api/hotkeys/capture', params={'token': token})).status_code == 200
                assert len(radio.registered) == 5
            finally:
                radio.stop()

    asyncio.run(scenario())
