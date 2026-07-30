---
name: cytopia-deploy
description: Build, preflight, authorize, and publish a static or full-stack student project to the Cytopia Summer Camp without exposing infrastructure or model keys. Supports HTML, React, Vue, FastAPI, Flask, and Node.js with managed PostgreSQL, MySQL, or SQLite. Never deploy student Dockerfiles or Compose files.
---

# Cytopia Deploy

Publish a student project through the training-camp deployment control plane. Keep every server, DNS, and model credential on the server.

## Safety boundary

- Never request, read, copy, print, or store an SSH key, DNS AccessKey, model API key, platform admin password, or reusable deployment token.
- Never add a secret to the project, this Skill, a prompt, a screenshot, or a Git commit.
- Upload static output for frontend-only projects or sanitized source for supported full-stack projects. Do not upload `.env`, credentials, local databases, Dockerfiles, Compose files, private keys, dependency directories, or caches.
- Full-stack code runs only through the platform presets `fastapi`, `flask`, and `node`; never request arbitrary container privileges.
- Treat the browser device code as a one-time authorization. Keep it in process memory only and let it expire after use.
- Stop if preflight reports a suspected secret. Help the learner remove the secret or replace the integration with `/__camp/ai/chat`.

## Learner interaction contract

The learner experience is **one prompt, one browser confirmation, one live URL**.

- Own project inspection, project-name inference, build selection, output-directory detection, preflight, packaging, device-flow startup, upload, progress polling, online verification, and ordinary failure diagnosis.
- Do not turn those internal actions into a checklist for the learner. Do not ask the learner to write a manifest, choose `dist/` versus `build/`, run the deployment client, copy a token into chat, configure a domain, or test a technical endpoint.
- Ask a clarifying question only when two genuinely different projects or output directories remain plausible after inspection. Otherwise make the safe choice and proceed.
- The only expected learner action during a normal deployment is confirming their training-camp identity in the browser. The learner never gives the agent an account password or infrastructure credential.
- After publication, perform the verification yourself and return a short result: live URL, deployment ID, checks passed, and any learner-visible limitation.
- For a later release, interpret “重新部署当前项目” as the same workflow. Reuse the stable hostname automatically.

## Deploy workflow

1. Inspect and classify the project:
   - Plain HTML: the project directory containing `index.html`.
   - React/Vue frontend only: build and upload `dist/`, `build/`, or `out/`.
   - FastAPI or Flask: upload sanitized Python source and `requirements.txt`.
   - Node.js: upload sanitized source when `package.json` has a supported server framework and `start`.
   - Detect PostgreSQL, MySQL, or SQLite; inspect code and pass `--database` if ambiguous.
2. Run the deterministic deploy client:

   ```powershell
   python -X utf8 scripts/deploy.py --project-dir "<project>" --project-name "<name>"
   ```

   Pass `--preset`, `--database`, `--entrypoint`, or `--healthcheck` when detection is ambiguous. Pass `--dry-run --json` to inspect the deployment plan without uploading.
3. Show the learner the device code and browser URL printed by the script. Explain that the browser authorization grants one upload for the current team and reveals no infrastructure secret.
4. Continue waiting while the script uploads and polls. Do not ask the learner to copy a token back into chat.
5. On success, return the complete live URL and verify:
   - the home page loads;
   - a deep link falls back to `index.html` for SPA projects;
   - the configured health endpoint succeeds for full-stack projects;
   - database-backed create/read behavior survives one redeploy;
   - `GET /__camp/ai/health` reports the project and AI availability when the app needs AI.
6. On failure, read the reported stage and use [references/troubleshooting.md](references/troubleshooting.md). Fix the project, then request a new device code and redeploy.

Treat steps 1–6 as internal Skill execution. Summarize them after completion; never assign them to the learner as homework.

## AI gateway contract

Frontend code calls the same deployment origin:

```js
const response = await fetch("/__camp/ai/chat", {
  method: "POST",
  headers: {"Content-Type": "application/json"},
  body: JSON.stringify({
    messages: [{role: "user", content: "请帮我总结这段内容"}]
  })
});
const result = await response.json();
const answer = result.data.message;
```

Do not add a model name, Base URL, or API key. The server selects the approved model, enforces the project quota, and owns the upstream credential.

## Redeploy and rollback

Run the same deploy command for a new version. The project keeps its hostname and database. The server switches versions only after build, database provisioning, startup, and health validation.

Rollback is a browser-account action in the training platform. Do not attempt SSH or direct filesystem rollback.

Read [references/api.md](references/api.md) only when diagnosing protocol details or extending the client.
