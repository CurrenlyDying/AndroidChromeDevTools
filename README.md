# AndroidChromeDevTools

### Overview

This is a tool that allows you to use the full-featured Chrome DevTools on your Android device just like you would on a PC. This is particularly useful for web developers who want to debug and inspect web pages directly on their mobile devices.

With AndroidChromeDevTools, you can inspect elements, debug JavaScript, analyze network and performance, and much more, all directly from your Android device.

<p float="left">
  <img src="docs/img1.jpg" width="200" height="400" />
  <img src="docs/img2.jpg" width="200" height="400"/> 
  <img src="docs/img3.jpg" width="200" height="400"/>
</p>

### Prerequisites

- An Android phone with Chrome installed (of course)
- [Termux](https://termux.dev/en/) installed on your phone (Install this useful tool if you haven't yet)

### Setup

Open termux and run following commands to retrieve the script and install the necessary dependencies.

```bash
# Update existing packages (to avoid weird issues)
pkg up

# We need python3 and adb
apt install python android-tools -y

pip3 install aiohttp

curl -o devtools.py https://raw.githubusercontent.com/lyc8503/AndroidChromeDevTools/main/devtools.py
chmod +x devtools.py
```

### Usage

#### For non-rooted users

Our script uses ADB to connect to Chrome DevTools, without ROOT you will need to manually enable Wireless ADB debugging with a PC. **This only needs to be done once until you reboot your phone.**

You need to enable ADB debugging for your phone first and then connect it to any computer and run `adb tcpip 1234` on the computer, where 1234 can be any port number you like.

Go back to the phone and start Termux, using `./devtools.py 1234` to run this script, where 1234 is the port number you just used. Until you reboot your phone, you can just run the script again without the need for a computer.

#### For rooted users

Using this script on a phone with ROOT access is even easier, just open Termux and run `./devtools.py` (no additional arguments required).

### Frontend selection

The script no longer uses a single hard-coded DevTools snapshot by default.

- It first reads `http://localhost:9222/json/version`
- It tries to extract a matching DevTools frontend revision from `WebKit-Version`
- It falls back to the previous pinned revision only if detection fails

This makes the hosted frontend much more likely to match the Chrome build running on your phone.

Useful flags:

```bash
# Force a specific frontend revision
./devtools.py 1234 --frontend-revision 0123456789abcdef0123456789abcdef01234567

# Point the default template at a different frontend host
./devtools.py 1234 --frontend-base-url https://chrome-devtools-frontend.appspot.com

# Use a self-hosted frontend bundle instead of the hosted revision service
./devtools.py 1234 --frontend-url-template 'http://127.0.0.1:8080/inspector.html?ws={ws}'
```

The same options can also be provided through environment variables:

- `ANDROID_CHROME_DEVTOOLS_FRONTEND_REVISION`
- `ANDROID_CHROME_DEVTOOLS_FRONTEND_BASE_URL`
- `ANDROID_CHROME_DEVTOOLS_FRONTEND_ENTRYPOINT`
- `ANDROID_CHROME_DEVTOOLS_FRONTEND_URL_TEMPLATE`

The URL template supports these placeholders:

- `{base_url}`
- `{revision}`
- `{entrypoint}`
- `{ws}`

### Proxy behavior

The local proxy listens on `localhost:9223` and now exposes more of the standard DevTools discovery surface:

- `/json`
- `/json/list`
- `/json/version`
- `/json/protocol`
- other `/json/*` endpoints such as `new`, `activate`, and `close`
- WebSocket proxying for `/devtools/page/*` and `/devtools/browser/*`

Discovery responses are rewritten to point back to the local proxy and to the selected DevTools frontend, which improves compatibility with newer DevTools features and external tooling that expects the normal discovery endpoints.

The proxy is bound to `localhost` and uses permissive CORS headers so that hosted or self-hosted DevTools frontends can connect back to it.

When prompted, you can now enter either the numbered target from the list or the raw target ID.

### AI DevTools and MCP limitations

This project can update the DevTools frontend version and expose a more complete remote-debugging proxy, but it does **not** implement Chrome's AI panels or MCP support by itself.

Whether AI DevTools or MCP features actually work depends on the upstream DevTools frontend revision, the Chrome version on the Android device, and any upstream feature gates such as experiments, sign-in requirements, or external services.

If you want maximum control, use `--frontend-url-template` with a self-hosted `devtools-frontend` build that you have pinned and tested against your target Chrome version.

### Known issues

- Devtools is not adapted for mobile, interaction may be somewhat difficult (requiring multiple zooms and drags), but it has full functionality.

### Troubleshooting

Chrome often closes itself in the background to save memory. If a connection failure occurs, switch to Chrome, confirm that Chrome and the tab you need to debug are still running, and then try again.

If the problem still persists, please [raise an issue](https://github.com/lyc8503/AndroidChromeDevTools/issues) in this repo and attach the FULL log.

### Alternative

[ChromeXt](https://github.com/JingMatrix/ChromeXt) might be a good choice too!
