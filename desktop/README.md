# desktop/: SoShop as a Windows app

`build-app.ps1` turns the repo into **one file, `SoShop-Setup-<version>.exe`**, that installs SoShop on a Windows 10/11 machine with nothing else on it: no Docker, no Python, no Node, no database, no admin rights. It is the native counterpart of the Docker bundle (`docker-compose.yml`, `tools/build-release.ps1`) and runs the same pieces with the same wiring.

End-user instructions (Vietnamese) are in [`HUONG-DAN.md`](../HUONG-DAN.md).

## What gets installed

```
%LOCALAPPDATA%\Programs\SoShop\        replaced on every update
  SoShop.exe                           the launcher (launcher/)
  app\python\                          CPython 3.11 (uv's relocatable build) + server packages, no venv
  app\server\{src,migrations,seed,scripts}   server code as .pyc only (compileall -b)
  app\web\                             Next.js standalone output (server.js)
  app\node\node.exe
  app\pgsql\{bin,lib,share}            Postgres 16 (EDB Windows binaries)
  app\livekit\livekit-server.exe
  app\ffmpeg\ffmpeg.exe
  app\flow_extension\                  SoShop Flow Bridge (copied to the data folder on start)

%LOCALAPPDATA%\SoShop\                 user data, kept across updates and (optionally) uninstall
  soshop.env                           settings (same keys as soshop.env.example)
  pgdata\  data\  logs\  tmp\  hf\  flow_extension\
```

A profile path with non-ASCII characters (`C:\Users\Nguyễn`) falls back to `C:\SoShop` and `C:\SoShop-data`, because Postgres on Windows cannot use paths outside the ANSI code page.

## How the launcher runs the stack

`launcher/Stack.cs` starts, in order: Postgres (`initdb` on first run, then `pg_ctl`), the migrations (`python -m soshop_server.db.migrate --seed-if-empty`), then LiveKit, the API and the web server, then the agent worker once LiveKit listens. A watchdog restarts anything that dies (at most 5 times in 10 minutes) and restarts the worker whenever LiveKit restarts. Every child process sits in a kill-on-close job object, so ending SoShop.exe in Task Manager also ends the stack.

| Service | Port (127.0.0.1 only) | Notes |
|---|---|---|
| Web (Next standalone) | 3000 | `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000` is baked in at build time |
| API (uvicorn) | 8000 | also the Flow Bridge websocket; the extension hard-codes this port |
| LiveKit | 7880, 7881/tcp, 7882/udp | `--dev --bind 127.0.0.1 --node-ip 127.0.0.1` |
| Agent worker health | 18081 | `AGENT_HTTP_HOST` / `AGENT_HTTP_PORT` settings |
| Postgres | 54329 | trust auth, user and database `soshop` |

Change a port in `Stack.cs` and check the other places that name it: `build-app.ps1` (API base URL), `server/flow_extension/background.js` (API port), and the CORS origins in `Stack.PyEnv`.

The environment the Python services get (`Stack.PyEnv`) mirrors `docker-compose.yml`: the user's `soshop.env` first, then the machine wiring on top (`DATABASE_URL`, `LIVEKIT_*`, `DATA_DIR`, `MEDIA_STORAGE=local`, `ENVIRONMENT=development`, a random `SITE_PASSWORD` per run shared with the web server).

## Backups

"Sao lưu dữ liệu" writes one zip: `manifest.json`, `db.dump` (`pg_dump -Fc`) and `data/...` (`DATA_DIR` without `models`, `work`, `cache`, `tmp`). "Khôi phục dữ liệu" drops and recreates the database, `pg_restore`s the dump, replaces the media, then runs the migrations (with `--baseline` from the manifest when the source database predates `schema_migrations`). `tools/export-data.ps1` writes the same format from the developer setup (`dev.ps1`) or the Docker bundle.

## Building

```powershell
powershell -ExecutionPolicy Bypass -File desktop\build-app.ps1 -Version 1.0.0
#   -NoSetup      stop after staging dist\desktop\SoShop (run SoShop.exe from there to test)
#   -SkipPython   reuse the staged Python, packages and server code
#   -SkipWeb      reuse the staged web build
```

Needs uv, Node with npm, the .NET SDK (the two exes target .NET Framework 4.7.2, which ships with Windows, through the `Microsoft.NETFramework.ReferenceAssemblies` package), ffmpeg on PATH (a static build such as gyan.dev's) and internet on the first run (Postgres and LiveKit downloads are cached in `desktop\.cache`). The web app is built in `dist\desktop\web-build` from a clean `npm ci --os=win32 --cpu=x64`, never from `web\node_modules`, and without local `.env` files.

`SoShop-Setup.exe` is `setup/` with the staged folder appended as a zip: `[setup exe][zip][zip offset, 8 bytes][SOSHOPZ1]` (`setup/Setup.cs`, `Payload`).

Testing without touching the real install:

```powershell
dist\desktop\SoShop\SoShop.exe --data D:\tmp\soshop-test --no-browser
dist\desktop\SoShop-Setup-1.0.0.exe --silent --dir D:\tmp\soshop-app --no-shortcuts --no-registry
```

The launcher refuses to start when ports 3000, 8000, 7880, 7881 or 18081 are taken, so stop `dev.ps1` or the Docker bundle first.

## Known limits

- **Unsigned.** SmartScreen shows "Windows protected your PC" until the exe is signed with a code-signing certificate (sign `SoShop.exe` before packing and the setup after appending, with `signtool`).
- **The Chrome extension is loaded by hand.** Chrome only installs extensions without a prompt from the Chrome Web Store or through enterprise policy, so the launcher walks the user through "Load unpacked". Publishing SoShop Flow Bridge as an unlisted Web Store item would make it one click.
- **One instance per machine**, since the ports are fixed.
- The lip-sync avatar weights (about 900 MB) still download from Hugging Face on first use; the pre-rendered Flow livestream does not need them.

`make-icon.ps1` draws `assets/soshop.ico` from the brand colors; the `.ico` is committed.
