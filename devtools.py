#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import random
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp import WSMsgType, web
from aiohttp.web import middleware

REMOTE_DEVTOOLS_PORT = 9222
LOCAL_PROXY_PORT = 9223
DEFAULT_FRONTEND_BASE_URL = "https://chrome-devtools-frontend.appspot.com"
DEFAULT_FRONTEND_ENTRYPOINT = "inspector.html"
DEFAULT_FRONTEND_FALLBACK_REVISION = "f84901f7f0a12725375071f589e8d9fc61af1de3"
DEFAULT_FRONTEND_URL_TEMPLATE = (
    "{base_url}/serve_rev/@{revision}/{entrypoint}?ws={ws}"
)
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


@dataclass(frozen=True)
class ParsedArgs:
    adb_port: Optional[int]
    frontend_base_url: str
    frontend_entrypoint: str
    frontend_revision: Optional[str]
    frontend_url_template: str


@dataclass(frozen=True)
class RuntimeConfig:
    frontend_base_url: str
    frontend_entrypoint: str
    frontend_revision: str
    frontend_url_template: str


def parse_args() -> ParsedArgs:
    parser = argparse.ArgumentParser(
        description="Launch Android Chrome DevTools from Termux with a local proxy."
    )
    parser.add_argument(
        "adb_port",
        nargs="?",
        type=int,
        help="Existing wireless ADB port. Omit to configure a random port using root.",
    )
    parser.add_argument(
        "--frontend-base-url",
        default=os.environ.get(
            "ANDROID_CHROME_DEVTOOLS_FRONTEND_BASE_URL",
            DEFAULT_FRONTEND_BASE_URL,
        ),
        help="Base URL used by the default frontend template.",
    )
    parser.add_argument(
        "--frontend-entrypoint",
        default=os.environ.get(
            "ANDROID_CHROME_DEVTOOLS_FRONTEND_ENTRYPOINT",
            DEFAULT_FRONTEND_ENTRYPOINT,
        ),
        help="Frontend entrypoint used by the default frontend template.",
    )
    parser.add_argument(
        "--frontend-revision",
        default=os.environ.get("ANDROID_CHROME_DEVTOOLS_FRONTEND_REVISION"),
        help="Override the detected DevTools frontend revision hash.",
    )
    parser.add_argument(
        "--frontend-url-template",
        default=os.environ.get(
            "ANDROID_CHROME_DEVTOOLS_FRONTEND_URL_TEMPLATE",
            DEFAULT_FRONTEND_URL_TEMPLATE,
        ),
        help=(
            "Template for opening DevTools. Supported placeholders: "
            "{base_url}, {revision}, {entrypoint}, {ws}."
        ),
    )
    args = parser.parse_args()
    return ParsedArgs(
        adb_port=args.adb_port,
        frontend_base_url=args.frontend_base_url,
        frontend_entrypoint=args.frontend_entrypoint,
        frontend_revision=args.frontend_revision,
        frontend_url_template=args.frontend_url_template,
    )


def run_command(command: list[str], error_message: str) -> None:
    if subprocess.run(command, check=False).returncode != 0:
        print(error_message, flush=True)
        sys.exit(1)


def setup_wireless_adb(adb_port: Optional[int]) -> None:
    if adb_port is None:
        print("No port specified, setting up wireless ADB on a random port using root")
        adb_port = random.randint(10000, 60000)
        command = (
            f"setprop service.adb.tcp.port {adb_port} && "
            "stop adbd && "
            "start adbd"
        )
        if subprocess.run(["su", "-c", command], check=False).returncode != 0:
            print("[!] Failed to set up wireless ADB, exiting", flush=True)
            sys.exit(1)
        print(f"Wireless ADB set up successfully on port {adb_port}", flush=True)
    else:
        print(f"Connecting to an existing wireless ADB on port {adb_port}", flush=True)

    subprocess.run(["adb", "disconnect"], check=False)
    run_command(
        ["adb", "connect", f"localhost:{adb_port}"],
        "[!] Failed to connect to wireless ADB",
    )
    run_command(
        [
            "adb",
            "forward",
            f"tcp:{REMOTE_DEVTOOLS_PORT}",
            "localabstract:chrome_devtools_remote",
        ],
        "[!] Failed to forward CDP port",
    )


async def fetch_remote_json(path: str) -> Any:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://localhost:{REMOTE_DEVTOOLS_PORT}{path}") as resp:
            resp.raise_for_status()
            return await resp.json()


def normalize_frontend_revision(revision: Optional[str]) -> Optional[str]:
    if not revision:
        return None

    normalized_revision = revision.strip().lower()
    if REVISION_PATTERN.fullmatch(normalized_revision):
        return normalized_revision
    return None


def detect_frontend_revision(
    version_payload: dict[str, Any], requested_revision: Optional[str]
) -> str:
    normalized_requested_revision = normalize_frontend_revision(requested_revision)
    if normalized_requested_revision:
        print(
            f"Using requested DevTools frontend revision {normalized_requested_revision}",
            flush=True,
        )
        return normalized_requested_revision
    if requested_revision:
        print(
            f"[!] Ignoring invalid frontend revision {requested_revision!r}",
            flush=True,
        )

    webkit_version = str(version_payload.get("WebKit-Version", ""))
    match = re.search(r"@([0-9a-fA-F]{40})", webkit_version)
    if match:
        revision = normalize_frontend_revision(match.group(1))
        if revision is None:
            print(
                "[!] Ignoring invalid detected DevTools frontend revision",
                flush=True,
            )
        else:
            print(f"Detected DevTools frontend revision {revision}", flush=True)
            return revision

    print(
        "[!] Failed to detect a matching DevTools frontend revision, "
        f"falling back to {DEFAULT_FRONTEND_FALLBACK_REVISION}",
        flush=True,
    )
    return DEFAULT_FRONTEND_FALLBACK_REVISION


async def resolve_runtime_config(args: ParsedArgs) -> RuntimeConfig:
    try:
        version_payload = await fetch_remote_json("/json/version")
    except Exception as error:
        print(
            f"[!] Failed to fetch browser version metadata: {error}. "
            f"Falling back to {DEFAULT_FRONTEND_FALLBACK_REVISION}",
            flush=True,
        )
        version_payload = {}

    return RuntimeConfig(
        frontend_base_url=args.frontend_base_url.rstrip("/"),
        frontend_entrypoint=args.frontend_entrypoint.lstrip("/"),
        frontend_revision=detect_frontend_revision(
            version_payload, args.frontend_revision
        ),
        frontend_url_template=args.frontend_url_template,
    )


def extract_websocket_path(websocket_url: Optional[str]) -> Optional[str]:
    if not websocket_url:
        return None

    parsed = urlparse(websocket_url)
    if not parsed.path.startswith("/"):
        return None
    return parsed.path


def local_websocket_url(websocket_path: str) -> str:
    return f"ws://localhost:{LOCAL_PROXY_PORT}{websocket_path}"


def local_websocket_query_value(websocket_path: str) -> str:
    return f"localhost:{LOCAL_PROXY_PORT}{websocket_path}"


def extract_target_id(websocket_path: str, default_target_id: str) -> str:
    path_parts = [part for part in websocket_path.split("/") if part]
    if len(path_parts) >= 3:
        return path_parts[-1]
    return default_target_id


def build_frontend_url(config: RuntimeConfig, websocket_path: str) -> str:
    return config.frontend_url_template.format(
        base_url=config.frontend_base_url,
        revision=config.frontend_revision,
        entrypoint=config.frontend_entrypoint,
        ws=local_websocket_query_value(websocket_path),
    )


def rewrite_target_payload(
    target_payload: dict[str, Any], config: RuntimeConfig
) -> dict[str, Any]:
    rewritten = dict(target_payload)
    websocket_path = extract_websocket_path(target_payload.get("webSocketDebuggerUrl"))
    if not websocket_path:
        return rewritten

    frontend_url = build_frontend_url(config, websocket_path)
    rewritten["webSocketDebuggerUrl"] = local_websocket_url(websocket_path)
    rewritten["devtoolsFrontendUrl"] = frontend_url
    rewritten["devtoolsFrontendUrlCompat"] = frontend_url
    return rewritten


def rewrite_discovery_payload(
    payload: Any, request_path: str, config: RuntimeConfig
) -> Any:
    if isinstance(payload, list):
        return [
            rewrite_target_payload(item, config) if isinstance(item, dict) else item
            for item in payload
        ]

    if not isinstance(payload, dict):
        return payload

    rewritten = dict(payload)
    websocket_path = extract_websocket_path(payload.get("webSocketDebuggerUrl"))
    if websocket_path:
        frontend_url = build_frontend_url(config, websocket_path)
        rewritten["webSocketDebuggerUrl"] = local_websocket_url(websocket_path)
        rewritten["devtoolsFrontendUrl"] = frontend_url
        rewritten["devtoolsFrontendUrlCompat"] = frontend_url

    if request_path.startswith("/json") and "id" in payload:
        return rewrite_target_payload(payload, config)

    return rewritten


async def fetch_debug_targets(config: RuntimeConfig) -> list[dict[str, Any]]:
    version_payload, list_payload = await asyncio.gather(
        fetch_remote_json("/json/version"),
        fetch_remote_json("/json/list"),
    )
    targets = [
        rewrite_target_payload(target, config)
        for target in list_payload
        if isinstance(target, dict)
        and extract_websocket_path(target.get("webSocketDebuggerUrl"))
    ]

    browser_websocket_path = extract_websocket_path(
        version_payload.get("webSocketDebuggerUrl")
    )
    if browser_websocket_path:
        targets.append(
            {
                "id": extract_target_id(browser_websocket_path, "browser"),
                "title": version_payload.get("Browser", "Chrome browser target"),
                "type": "browser",
                "webSocketDebuggerUrl": local_websocket_url(browser_websocket_path),
                "devtoolsFrontendUrl": build_frontend_url(
                    config, browser_websocket_path
                ),
                "description": "Browser-level debugging target",
            }
        )

    return targets


async def open_tab(config: RuntimeConfig) -> None:
    async def ainput() -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, sys.stdin.readline)

    while True:
        try:
            targets = await fetch_debug_targets(config)
        except Exception as error:
            print(f"[!] Failed to fetch targets: {error}", flush=True)
            await asyncio.sleep(2)
            continue

        if not targets:
            print("[!] No debuggable targets found. Is Chrome running?", flush=True)
            await asyncio.sleep(2)
            continue

        print("Available targets:", flush=True)
        for index, target in enumerate(targets, start=1):
            title = target.get("title") or target.get("description") or target["id"]
            print(
                f"[{index}] ({target.get('type', 'page')}) {title} [{target['id']}]",
                flush=True,
            )

        print("Enter target number or target ID (Ctrl-C to exit): ", end="", flush=True)
        user_input = (await ainput()).strip()
        if not user_input:
            print("Invalid input, try again", flush=True)
            continue

        selected_target = None
        if user_input.isdigit():
            selected_index = int(user_input)
            if 1 <= selected_index <= len(targets):
                selected_target = targets[selected_index - 1]

        if selected_target is None:
            for target in targets:
                if target["id"] == user_input:
                    selected_target = target
                    break

        if selected_target is None:
            print("Invalid input, try again", flush=True)
            continue

        frontend_url = selected_target.get("devtoolsFrontendUrl")
        if not frontend_url:
            print("[!] Selected target is missing a frontend URL", flush=True)
            continue

        print(f"Opening DevTools for {selected_target['id']}", flush=True)
        subprocess.run(["termux-open-url", frontend_url], check=False)


@middleware
async def cors_middleware(request: web.Request, handler: Any) -> web.StreamResponse:
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return response


async def proxy_http_handler(request: web.Request) -> web.Response:
    request_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() != "host" and key.lower() not in HOP_BY_HOP_HEADERS
    }
    request_body = await request.read()

    async with aiohttp.ClientSession() as session:
        async with session.request(
            request.method,
            f"http://localhost:{REMOTE_DEVTOOLS_PORT}{request.path_qs}",
            data=request_body or None,
            headers=request_headers,
            allow_redirects=False,
        ) as resp:
            response_headers = {
                key: value
                for key, value in resp.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            }
            response_body = await resp.read()
            response_status = resp.status
            response_charset = resp.charset or "utf-8"

    if request.path.startswith("/json"):
        try:
            payload = json.loads(response_body.decode(response_charset))
            payload = rewrite_discovery_payload(
                payload, request.path, request.app["config"]
            )
            response_body = json.dumps(payload).encode("utf-8")
            response_headers["Content-Type"] = "application/json; charset=utf-8"
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass

    return web.Response(
        body=response_body,
        status=response_status,
        headers=response_headers,
    )


async def forward_websocket_messages(source: Any, destination: Any) -> None:
    async for msg in source:
        if msg.type == WSMsgType.TEXT:
            await destination.send_str(msg.data)
        elif msg.type == WSMsgType.BINARY:
            await destination.send_bytes(msg.data)
        elif msg.type in {WSMsgType.CLOSE, WSMsgType.CLOSED}:
            await destination.close()
            break
        elif msg.type == WSMsgType.ERROR:
            break


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    ws_server = web.WebSocketResponse(max_msg_size=0)
    await ws_server.prepare(request)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                f"ws://localhost:{REMOTE_DEVTOOLS_PORT}{request.path_qs}",
                max_msg_size=0,
            ) as ws_client:
                await asyncio.gather(
                    forward_websocket_messages(ws_server, ws_client),
                    forward_websocket_messages(ws_client, ws_server),
                )
    except aiohttp.ClientError as error:
        print(f"[!] WebSocket proxy failure for {request.path}: {error}", flush=True)
    finally:
        await ws_server.close()

    return ws_server


async def reverse_proxy(config: RuntimeConfig) -> None:
    app = web.Application(middlewares=[cors_middleware])
    app["config"] = config
    app.router.add_route("*", "/json", proxy_http_handler)
    app.router.add_route("*", "/json/{tail:.*}", proxy_http_handler)
    app.router.add_route("*", "/devtools/{kind}/{id}", websocket_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", LOCAL_PROXY_PORT)
    await site.start()
    print(
        f"Reverse proxy listening on http://localhost:{LOCAL_PROXY_PORT}",
        flush=True,
    )
    await asyncio.Event().wait()


def main() -> None:
    args = parse_args()
    setup_wireless_adb(args.adb_port)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    config = loop.run_until_complete(resolve_runtime_config(args))
    loop.create_task(open_tab(config))
    loop.create_task(reverse_proxy(config))

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        loop.stop()
        loop.close()


if __name__ == "__main__":
    main()
