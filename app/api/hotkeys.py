"""本机快捷键配置和单页播放事件。"""

import asyncio
import time
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.hotkeys import HotkeyConfig, HotkeyConflict

router = APIRouter(prefix='/api/hotkeys')


@router.get('')
async def status(request: Request):
    return request.app.state.hotkeys.status()


@router.put('')
async def save(request: Request, config: HotkeyConfig):
    hotkeys = request.app.state.hotkeys
    if not hotkeys.native:
        raise HTTPException(409, '全局快捷键仅在 Windows 电台可用。')
    try:
        return hotkeys.save(config)
    except HotkeyConflict as error:
        raise HTTPException(409, str(error)) from error
    except OSError as error:
        raise HTTPException(503, '快捷键设置暂时无法保存。') from error


class Target(BaseModel):
    tab: UUID
    playing: bool
    used_at: int = Field(ge=0)


class Capture(BaseModel):
    token: UUID


@router.post('/capture')
async def begin_capture(request: Request, capture: Capture):
    hotkeys = request.app.state.hotkeys
    if not hotkeys.native:
        raise HTTPException(409, '全局快捷键仅在 Windows 电台可用。')
    try:
        hotkeys.begin_capture(capture.token)
    except HotkeyConflict as error:
        raise HTTPException(409, str(error)) from error
    return {'ok': True}


@router.delete('/capture')
async def end_capture(request: Request, token: UUID):
    request.app.state.hotkeys.end_capture(token)
    return {'ok': True}


@router.post('/target')
async def activate(request: Request, target: Target):
    request.app.state.hotkeys.activate(target.tab, target.playing, min(target.used_at, time.time_ns() // 1_000_000))
    return {'ok': True}


@router.get('/events')
async def events(request: Request, tab: UUID, playing: bool = False, used_at: int = Query(0, ge=0)):
    hotkeys = request.app.state.hotkeys
    if not hotkeys.native:
        raise HTTPException(404, '全局快捷键仅在 Windows 电台可用。')

    async def stream():
        queue = hotkeys.connect(tab, playing, min(used_at, time.time_ns() // 1_000_000))
        cutoff = time.monotonic() + 20
        try:
            yield 'retry: 250\nevent: ready\ndata: ready\n\n'
            while (remaining := cutoff - time.monotonic()) > 0:
                try:
                    action, received = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    return
                if time.monotonic() - received < 2:
                    yield f'data: {action}\n\n'
        finally:
            hotkeys.disconnect(tab, queue)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'Cache-Control': 'no-store'})
