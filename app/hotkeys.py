"""Windows 全局播放键，只注册声明的组合，不读取普通按键。"""

import asyncio
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import sys
import time

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


ACTIONS = {
    'toggle': '播放／暂停',
    'previous': '上一首',
    'next': '下一首',
    'volume_up': '提高音量',
    'volume_down': '降低音量',
}
MODIFIERS = {'Ctrl+Alt': 3, 'Ctrl+Shift': 6, 'Alt+Shift': 5, 'Ctrl+Alt+Shift': 7}
KEYS = {c: ord(c) for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'}
KEYS.update({f'F{n}': 0x6F + n for n in range(1, 25) if n != 12})
KEYS.update({'←': 0x25, '↑': 0x26, '→': 0x27, '↓': 0x28, 'Space': 0x20})


class HotkeyConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    enabled: bool = True
    toggle: str = 'Ctrl+Alt+P'
    previous: str = 'Ctrl+Alt+←'
    next: str = 'Ctrl+Alt+→'
    volume_up: str = 'Ctrl+Alt+↑'
    volume_down: str = 'Ctrl+Alt+↓'

    @model_validator(mode='before')
    @classmethod
    def fill_volume_bindings(cls, values):
        if not isinstance(values, dict):
            return values
        values = values.copy()
        used = {values[action] for action in ACTIONS if isinstance(values.get(action), str)}
        # 旧配置可能把上下方向键用于切歌。只为缺失的新动作分配未使用的键。
        for action, key, fallback in (('volume_up', '↑', 'F9'), ('volume_down', '↓', 'F10')):
            if action not in values:
                candidates = [f'{modifiers}+{key}' for modifiers in MODIFIERS] + [f'Ctrl+Alt+{fallback}']
                values[action] = next(combo for combo in candidates if combo not in used)
                used.add(values[action])
        return values

    @field_validator(*ACTIONS)
    @classmethod
    def valid_combo(cls, value):
        modifiers, _, key = value.rpartition('+')
        if modifiers not in MODIFIERS or key not in KEYS:
            raise ValueError('不支持的按键组合')
        return value

    @model_validator(mode='after')
    def distinct(self):
        if len({getattr(self, action) for action in ACTIONS}) != len(ACTIONS):
            raise ValueError('各项快捷键必须不同')
        return self


class WindowsHotkeys:
    def __init__(self):
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.register = user32.RegisterHotKey
        self.register.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
        self.register.restype = wintypes.BOOL
        self.unregister = user32.UnregisterHotKey
        self.unregister.argtypes = (wintypes.HWND, ctypes.c_int)
        self.unregister.restype = wintypes.BOOL
        self.peek = user32.PeekMessageW
        self.peek.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
        self.peek.restype = wintypes.BOOL

    def add(self, ident, combo):
        modifiers, key = combo.rsplit('+', 1)
        return bool(self.register(None, ident, MODIFIERS[modifiers] | 0x4000, KEYS[key]))

    def remove(self, ident):
        self.unregister(None, ident)

    def pending(self):
        message = wintypes.MSG()
        while self.peek(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            yield message.wParam


class HotkeyConflict(ValueError):
    pass


class RadioHotkeys:
    def __init__(self, path: Path, native=None):
        self.path = path
        self.native = native if native is not None else WindowsHotkeys() if sys.platform == 'win32' else None
        try:
            self.config = HotkeyConfig.model_validate_json(path.read_bytes())
        except (OSError, ValueError):
            self.config = HotkeyConfig()
        self.registered = {}  # 组合键 -> 注册编号。换键时保留仍使用的注册，也支持互换。
        self.clients = {}  # 页面 -> (事件队列、是否播放、最近使用时间)
        self.pump = None
        self.release_timer = None
        self.capture_timer = None
        self.capture_token = None
        self.next_id = 1

    def status(self):
        return {
            'supported': self.native is not None,
            'config': self.config.model_dump(),
            'active': [action for action in ACTIONS if getattr(self.config, action) in self.registered],
        }

    def _add(self, combo):
        ident = self.next_id
        self.next_id += 1
        if self.next_id >= 0xBFFF:
            self.next_id = 1
        # 回绕时不复用仍在用的编号，否则系统可能把两个组合发给同一个动作。
        if ident in self.registered.values():
            return self._add(combo)
        if self.native.add(ident, combo):
            self.registered[combo] = ident
            return True
        return False

    def _remove(self, combo):
        self.native.remove(self.registered.pop(combo))

    def _register_configured(self):
        if self.native and self.config.enabled and not self.capture_token:
            for action in ACTIONS:
                combo = getattr(self.config, action)
                if combo not in self.registered:
                    self._add(combo)

    def begin_capture(self, token):
        if self.capture_token:
            raise HotkeyConflict('另一页正在录入快捷键，请稍后再试。')
        self.capture_token = token
        for combo in list(self.registered):
            self._remove(combo)
        # 页面关闭或失联时也会自动结束录入、恢复快捷键。
        self.capture_timer = asyncio.get_running_loop().call_later(15, self.end_capture, token)

    def end_capture(self, token):
        if token != self.capture_token:
            return
        if self.capture_timer:
            self.capture_timer.cancel()
            self.capture_timer = None
        self.capture_token = None
        if self.clients:
            self._register_configured()

    def save(self, config):
        if self.capture_token:
            raise HotkeyConflict('请先结束快捷键录入，再保存设置。')
        combos = {getattr(config, action): action for action in ACTIONS}
        desired = combos if config.enabled else {}
        added = []
        try:
            if self.native:
                for combo, action in desired.items():
                    if combo in self.registered:
                        continue
                    if not self._add(combo):
                        raise HotkeyConflict(f'{ACTIONS[action]}的 {combo} 无法注册，可能已被系统或其他应用占用。')
                    added.append(combo)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(config.model_dump_json(), encoding='utf-8')
            temporary.replace(self.path)
        except (OSError, HotkeyConflict):
            for combo in added:
                self._remove(combo)
            raise
        live = desired if self.clients else {}
        for combo in self.registered.keys() - live.keys():
            self._remove(combo)
        self.config = config
        return self.status()

    def connect(self, tab, playing, used_at):
        # EventSource 定期重连，短暂保留注册以免重连时丢掉快捷键。
        first = not self.clients
        if self.release_timer is not None:
            self.release_timer.cancel()
            self.release_timer = None
        queue = asyncio.Queue(maxsize=8)
        self.clients[tab] = (queue, playing, used_at)
        if self.native and first:
            self._register_configured()
            if self.pump is None:
                self.pump = asyncio.create_task(self._dispatch())
        return queue

    def activate(self, tab, playing, used_at):
        if tab in self.clients:
            queue, _, current = self.clients[tab]
            if used_at >= current:
                self.clients[tab] = (queue, playing, used_at)

    def disconnect(self, tab, queue):
        if tab in self.clients and self.clients[tab][0] is queue:
            del self.clients[tab]
            if not self.clients and self.pump:
                self.release_timer = asyncio.get_running_loop().call_later(1, self._release)

    def _release(self):
        if self.release_timer:
            self.release_timer.cancel()
            self.release_timer = None
        if self.pump:
            self.pump.cancel()
            self.pump = None
        self.clients.clear()
        for combo in list(self.registered):
            self._remove(combo)

    def stop(self):
        self._release()
        self.end_capture(self.capture_token)

    async def _dispatch(self):
        # RegisterHotKey 与消息读取同属 Uvicorn 的事件循环线程，只取 WM_HOTKEY。
        while True:
            await asyncio.sleep(.04)
            actions = {self.registered.get(getattr(self.config, name)): name for name in ACTIONS}
            for ident in self.native.pending():
                if ident in actions and self.clients:
                    queue, _, _ = max(self.clients.values(), key=lambda client: (client[1], client[2]))
                    if not queue.full():
                        queue.put_nowait((actions[ident], time.monotonic()))
