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

- `artifact`: ZIP with `index.html` at the root.

Only static Web file types are accepted. Hidden files, path traversal, symlinks, private keys, suspected provider keys, oversized archives, and unsupported extensions fail publication.

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
