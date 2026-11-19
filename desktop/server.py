"""Electron 管理的本机服务。标准输入关闭时执行常规 ASGI 关闭。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def bind(port):
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        server_socket.bind(("127.0.0.1", port))
        server_socket.listen(128)
        return server_socket
    except OSError:
        server_socket.close()
        raise


def wait_for_parent_shutdown():
    if os.name != "nt":
        sys.stdin.buffer.read(1)
        return

    import _winapi
    import msvcrt

    # Windows 上阻塞读取标准输入管道会卡住 NumPy、SciPy 等原生依赖的加载。
    # 只检查管道是否收到关闭信号或已断开，避免持有正在等待数据的读取操作。
    handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    try:
        while not _winapi.PeekNamedPipe(handle, 0)[0]:
            time.sleep(0.1)
    except BrokenPipeError:
        return


async def run(application, port):
    sys.path.insert(0, str(application))
    from app.main import app
    import uvicorn

    with bind(port) as server_socket:
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", access_log=False, log_level="warning", timeout_graceful_shutdown=4
        ))

        def watch_parent():
            try:
                wait_for_parent_shutdown()
            finally:
                server.should_exit = True

        threading.Thread(target=watch_parent, daemon=True).start()
        task = asyncio.create_task(server.serve(sockets=[server_socket]))
        while not server.started and not task.done():
            await asyncio.sleep(0.04)
        if server.started:
            print("PERSONAL_RADIO_READY " + json.dumps({"port": server_socket.getsockname()[1]}), flush=True)
        await task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--application", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if "PERSONAL_RADIO_DATA_DIR" not in os.environ or not 0 <= args.port <= 65535:
        parser.error("desktop data directory and a valid port are required")
    asyncio.run(run(args.application.resolve(), args.port))
