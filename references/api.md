# Deployment API

Base URL: `https://summercamp.godpenai.com`

## Device authorization

1. `POST /api/deploy/device/start` with a stable, random `client_id`.
2. Open the returned `verification_uri`.
3. Poll `POST /api/deploy/device/token` with `{"device_code":"..."}`.
4. Save the returned `access_token` in operating-system-protected user state.
5. Validate a cached session with `GET /api/deploy/session`.
6. Use the access token as `Authorization: Bearer ...` for uploads and job polling.

The device code expires in ten minutes and is consumed after authorization. The deployment session is bound to the logged-in camp enrollment, the current team, and the first uploaded project; it lasts at most eight hours. It cannot access infrastructure APIs.

The client reuses a valid session for fixes, retries, and new versions. It must not silently generate a second device code when the current code expires; stop with a clear error so one command execution never causes repeated confirmations.

## Artifact upload

`POST /api/deploy/artifacts` uses `multipart/form-data`:

- `manifest`: JSON matching:

  ```json
  {
    "project_name": "校园助手",
    "entry": "index.html",
    "spa": true,
    "needs_ai_gateway": true,
    "source": "cytopia-deploy-skill"
  }
  ```

  Full-stack example:

  ```json
  {
    "version": 2,
    "project_name": "校园助手",
    "kind": "fullstack",
    "preset": "flask",
    "database": "mysql",
    "entrypoint": "app:app",
    "healthcheck": "/health",
    "needs_ai_gateway": true,
    "source": "cytopia-deploy-skill"
  }
  ```

- `artifact`: static ZIP with `index.html` at the root, or sanitized source ZIP for a supported full-stack preset.

Full-stack presets are `fastapi`, `flask`, and `node`; databases are `postgresql`, `mysql`, `sqlite`, and `none`. Dockerfiles and Compose files are not uploaded. Hidden files, path traversal, symlinks, keys, oversized archives, and unsupported extensions fail publication.

Uploads are idempotent by artifact checksum plus normalized manifest. Repeating an identical upload returns the original `deployment_id` with `deduplicated: true`.

## Status

`GET /api/deploy/jobs/{deployment_id}` accepts the scoped deployment session. Terminal states are `published` and `failed`.

## AI gateway

`POST /__camp/ai/chat` is available only on a published project hostname. It accepts OpenAI-style `messages` but not a model, Base URL, or key. The response is:

```json
{
  "ok": true,
  "data": {
    "message": "模型回答",
    "model": "server-selected-model",
    "remaining": 99
  }
}
```
