# Replay Studio web workspace

React, TypeScript and Vite. The production build is served by the Python API from `frontend/dist`, so session cookies, CSRF protection, media and exports share one origin. Fonts are bundled locally; the interface does not require a third-party font or image service.

## Run

```powershell
pnpm install --frozen-lockfile
pnpm build
```

For frontend development, start the API on `127.0.0.1:8000`, allow `http://127.0.0.1:5173` in `REPLAY_ALLOWED_ORIGINS`, and run `pnpm dev`. Vite proxies `/api` to the API. Open the printed Vite URL. A production build uses the API's same origin without a development proxy.

`pnpm typecheck` checks all production TypeScript. `pnpm format` formats the frontend. The lockfile fixes the installed dependency graph; `pnpm-workspace.yaml` permits only the required esbuild installation script.

## Editing behavior

- Uploads use bounded binary chunks and server-confirmed offsets. Pause/resume works within the page; a resumed browser session asks for the same local file. Resume metadata includes name, size, modification time and hashes of three sampled file regions. File bytes and session tokens are not stored in browser storage. This identity check is not a full-file integrity proof; the server computes the committed source hash.
- Discarding an unfinished upload explicitly confirms deletion, calls the authenticated cancellation API to release its chunks and reserved quota, and clears local resume information only after success. An unsuccessful cancellation preserves the resume handle. Selecting a different file while an upload is unfinished requires resolving the original upload first.
- Search distinguishes speech, screen text and visual evidence. It displays backend warnings and partial-evidence notices without invented confidence percentages. Drafts display the actual planning strategy and must be explicitly applied.
- Each saved timeline references one source and one analysis run. The timeline supports boundaries, clip titles, one caption per clip, clip order, undo, and contiguous playback of the chosen source segments. Captions are delivered separately as SRT, not silently burned into the video.
- Polling never replaces a dirty timeline. A compare-and-swap conflict retains local edits, offers a JSON backup, and requires an explicit choice to load the latest shared version or base a new save on it. A delayed poll cannot roll a just-saved timeline back. Edits made while a save is in flight are retained.
- MP4, SRT and source-map downloads use authenticated URLs. Export creation uses an immutable saved version; unsaved edits cannot accidentally be rendered.
- Owners add existing account holders as editors or viewers. Adding a member grants access; no invitation email is sent. Comments with a timestamp retain their source asset ID. Ambiguous legacy timestamps are not played against an arbitrary recording.
- Removing an asset and abandoning unsaved edits require an explicit in-app confirmation. Reanalysis does not rewrite an existing saved cut.

Keyboard: buttons and dialogs are accessible by Tab, Escape closes a dialog, Ctrl/Command + S saves, Ctrl/Command + Z undoes a timeline action outside text fields, and Alt + Left/Right reorders a focused timeline block. Text inputs retain their normal native undo behavior. The workspace also adapts to narrow screens.

## Real-browser integration checks

The tests use the **live API and a real processed recording**, with no mocked model responses. Start a disposable server and run the repository's actual video fixture workflow first. Set:

```powershell
$env:REPLAY_E2E_URL = 'http://127.0.0.1:8080'
$env:REPLAY_E2E_EMAIL = 'YOUR_TEST_ACCOUNT_EMAIL'
$env:REPLAY_E2E_PASSWORD = 'YOUR_TEST_ACCOUNT_PASSWORD'
$env:REPLAY_E2E_VIDEO = 'ABSOLUTE_PATH_TO_FIXTURE.mp4'
pnpm exec playwright install chromium
pnpm test:e2e
```

An installed Chromium-family browser can be selected with `PLAYWRIGHT_CHROMIUM_EXECUTABLE`. The first test needs a ready recording with an existing saved version, searches for the fixture's spoken “error”, and verifies a real version conflict. It restores the original timeline as a new version in `finally`. The upload test creates a disposable project, uploads the real file, verifies queued stages, then deletes the asset. The discard test creates a real partial upload and checks confirmed cancellation, retention of the resume handle after a real server error, and protection against replacing an unfinished upload with a different file. Empty test projects and immutable version history remain, so use a test workspace.

The checks cover UI login, actual evidence retrieval, editing and saving, preservation during polling, a 409 conflict, explicit recovery, available authenticated export links, mobile overflow, project creation, chunked upload, and discarding unfinished uploads. They do not claim that all optional models, collaborative roles, every upload-interruption condition, or all export codecs have been browser-tested.

Screenshots, failure traces and the HTML report are written below `test-results` and `playwright-report`. These can include private media and account names; do not publish artifacts from a real user's workspace.
