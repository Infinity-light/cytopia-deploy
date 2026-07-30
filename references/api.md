# Deployment API

Base URL: `https://summercamp.godpenai.com`

## Device authorization

1. `POST /api/deploy/device/start`
2. Open the returned `verification_uri`.
3. Poll `POST /api/deploy/device/token` with `{"device_code":"..."}`.
4. Use the same raw device code as `Authorization: Bearer ...` for one artifact upload.

The device code expires in ten minutes, is bound to the logged-in camp enrollment, and becomes consumed after upload. It cannot access infrastructure APIs.

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

## Status

`GET /api/deploy/jobs/{deployment_id}` accepts the consumed device code until it expires. Terminal states are `published` and `failed`.

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
