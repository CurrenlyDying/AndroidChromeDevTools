# CLAUDE.md

Guidance for AI assistants working in this repository.

## What this project is

**AndroidChromeDevTools** is a single-file Python tool that lets you use the
full desktop Chrome DevTools frontend to inspect and debug Chrome running on an
Android phone. It runs from [Termux](https://termux.dev/en/) on the phone
itself — no PC required at debug time (a PC is only needed once for non-rooted
users to enable wireless ADB).

The whole program is `devtools.py`. There is no build system, package manifest,
test suite, or dependency lockfile. The only third-party runtime dependency is
`aiohttp`; everything else is the Python 3 standard library.

## How it works (end-to-end flow)

1. **Wireless ADB setup** (`setup_wireless_adb`): connects `adb` to the phone
   over TCP and forwards local TCP port `9222` to the abstract socket
   `chrome_devtools_remote`, which is Chrome's remote-debugging endpoint on
   Android. Rooted users get a random port configured via `su`; non-rooted
   users pass an existing ADB port as the sole positional argument.
2. **Config resolution** (`resolve_runtime_config`): fetches
   `http://localhost:9222/json/version` and picks a DevTools *frontend
   revision* — from `--frontend-revision`, else parsed out of the
   `WebKit-Version` field, else the pinned fallback
   `DEFAULT_FRONTEND_FALLBACK_REVISION`.
3. **Reverse proxy** (`reverse_proxy`): starts an `aiohttp` server on
   `localhost:9223` that proxies both HTTP discovery endpoints (`/json*`) and
   WebSocket debugging connections (`/devtools/{kind}/{id}`) to Chrome on
   `9222`. Discovery JSON is rewritten so `webSocketDebuggerUrl` and
   `devtoolsFrontendUrl` point back at the local proxy and the chosen hosted
   frontend.
4. **Interactive tab picker** (`open_tab`): lists debuggable targets, lets the
   user pick by number or raw target ID, and launches the DevTools frontend URL
   with `termux-open-url`.

`main()` sets up ADB synchronously, then runs `open_tab` and `reverse_proxy` as
concurrent asyncio tasks on a single event loop.

## Key ports and constants

Defined at the top of `devtools.py`:

- `REMOTE_DEVTOOLS_PORT = 9222` — the `adb forward` target; Chrome's CDP endpoint.
- `LOCAL_PROXY_PORT = 9223` — where this tool's reverse proxy listens.
- `DEFAULT_FRONTEND_BASE_URL` / `DEFAULT_FRONTEND_ENTRYPOINT` /
  `DEFAULT_FRONTEND_URL_TEMPLATE` — how the hosted DevTools URL is built.
- `DEFAULT_FRONTEND_FALLBACK_REVISION` — pinned frontend revision used only when
  detection fails.
- `REVISION_PATTERN` — a 40-char lowercase hex validator; all revisions must
  match it.
- `HOP_BY_HOP_HEADERS` — headers stripped when proxying in both directions.

## Configuration surface

Every knob has both a CLI flag and an environment variable (flag wins):

| Flag | Env var | Purpose |
| --- | --- | --- |
| `--frontend-base-url` | `ANDROID_CHROME_DEVTOOLS_FRONTEND_BASE_URL` | Host serving the frontend |
| `--frontend-entrypoint` | `ANDROID_CHROME_DEVTOOLS_FRONTEND_ENTRYPOINT` | e.g. `inspector.html` |
| `--frontend-revision` | `ANDROID_CHROME_DEVTOOLS_FRONTEND_REVISION` | Force a specific 40-hex revision |
| `--frontend-url-template` | `ANDROID_CHROME_DEVTOOLS_FRONTEND_URL_TEMPLATE` | Full URL template |

The URL template supports the placeholders `{base_url}`, `{revision}`,
`{entrypoint}`, and `{ws}`. Use `--frontend-url-template` to point at a
self-hosted `devtools-frontend` bundle.

The single positional argument is the wireless ADB port (optional; omit it on a
rooted device to auto-configure a random port).

## Running it

This tool is designed to run on an Android device under Termux, so it cannot be
fully exercised in this repo's environment (no `adb`, no phone, no
`termux-open-url`). Normal invocation:

```bash
./devtools.py            # rooted: auto-configure wireless ADB
./devtools.py 1234       # non-rooted: connect to an already-open ADB port
```

For syntax/type sanity you can still do:

```bash
python3 -m py_compile devtools.py
```

## Conventions to follow when editing `devtools.py`

- **Keep it a single file** with no new runtime dependencies beyond `aiohttp`
  and the standard library. Do not introduce a package structure, framework, or
  build tooling unless explicitly asked.
- **Type hints everywhere.** All functions are annotated; frozen dataclasses
  (`ParsedArgs`, `RuntimeConfig`) carry structured config. Match this style.
- **Async I/O via `aiohttp`/`asyncio`.** Networking is non-blocking; only ADB
  setup uses blocking `subprocess`. Keep new network code async.
- **Fail loudly to stdout with `flush=True`.** User-facing messages use a
  `[!]` prefix for warnings/errors and `sys.exit(1)` on fatal setup failures.
  Preserve this pattern — Termux users read stdout directly.
- **Validate revisions** through `normalize_frontend_revision` /
  `REVISION_PATTERN`; never interpolate an unvalidated revision into a URL.
- **Preserve the security posture.** The proxy binds to `localhost` only. CORS
  is intentionally permissive (`Access-Control-Allow-Origin: *`) so hosted
  frontends can connect back; this is safe *only* because the listener is
  loopback-bound. Do not bind to `0.0.0.0`.
- **Strip hop-by-hop headers** (`HOP_BY_HOP_HEADERS`) and the `Host` header when
  proxying, and only rewrite bodies for `/json*` responses that parse as JSON —
  leave everything else untouched.
- **WebSocket proxying uses `max_msg_size=0`** deliberately (traces/source maps
  are large). Keep that.

## Repository layout

```
devtools.py     # the entire application
README.md       # end-user setup/usage docs (Termux-focused)
LICENSE         # MIT
docs/           # img1.jpg / img2.jpg / img3.jpg used by the README
```

## Docs to keep in sync

`README.md` documents the user-facing setup, flags, env vars, proxy behavior,
and troubleshooting. When you change flags, ports, the frontend-selection
logic, or the proxy's discovery/WebSocket surface, update the corresponding
README section in the same change.

## Git workflow

- Default branch is `main`. Develop on the branch you were assigned; do not push
  to `main` without explicit permission.
- Use clear, imperative commit messages consistent with the existing history
  (e.g. "Harden proxy header and revision handling").
- After pushing, open a PR as ready-for-review if none is open for the branch.
