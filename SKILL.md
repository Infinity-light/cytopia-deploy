---
name: cytopia-deploy
description: Build, preflight, authorize, and publish a static student web project to the Cytopia Summer Camp without exposing infrastructure or model keys. Use when a learner asks to deploy, publish, go live, redeploy, obtain a camp domain, roll back, or use the camp AI gateway. Supports plain HTML and locally built frontend projects such as React or Vue; never uploads or executes student backends, databases, Dockerfiles, Compose files, or server processes.
---

# Cytopia Deploy

Publish locally built static output through the training-camp deployment control plane. Keep every server, DNS, storage, and model credential on the server.

## Non-negotiable boundary

- Never request, read, copy, print, or store an SSH key, DNS AccessKey, object-storage key, model API key, or platform administrator password.
- Never add a secret to the learner project, this Skill, a prompt, a screenshot, a log, or a Git commit.
- Upload only built static output. Reject `.env`, source/build manifests, backend code, databases, Dockerfiles, Compose files, private keys, dependency directories, and caches.
- Do not execute or upload a learner backend, container configuration, database migration, or arbitrary server command. If the project depends on a backend, explain that this release supports static output only and help move AI calls to `/__camp/ai/chat`.
- Stop on suspected secrets or unresolved localhost URLs. Fix the project before authorizing or uploading.

## Learner contract

The normal experience is **one prompt, one browser confirmation, one live URL**.

- Own project inspection, project-name inference, local build, output-directory detection, manifest generation, preflight, packaging, authorization, upload, progress polling, verification, retries, and ordinary failure diagnosis.
- Do not ask the learner to write a manifest, choose `dist/` versus `build/`, run the client, copy a token into chat, configure DNS, or test technical endpoints.
- Ask only when two genuinely different projects or output directories remain plausible after inspection.
- Confirm the learner's training-camp identity once in the browser on the current device. Fixes, retries, and later versions reuse that scoped session for up to eight hours.
- Store the session only in operating-system-protected user state: Windows DPAPI or a user-only state file. Never store it in the learner project.
- After publication, return only the live URL, deployment ID, checks passed, and any learner-visible limitation.

## Workflow

1. Inspect the project and identify static output:
   - Plain HTML: use the directory containing `index.html`.
   - React/Vue or another frontend build: run the existing build script, then use `dist/`, `build/`, or `out/`.
   - If server routes, database access, or a runtime process are required, stop. Do not downgrade the product silently.
2. Run the client:

   ```powershell
   python -X utf8 scripts/deploy.py --project-dir "<project>" --project-name "<name>"
   ```

   Use `--dist "<directory>"` only when output detection is ambiguous. Use `--dry-run --json` for build, security, and packaging checks without upload.
3. Let the client reuse a valid local deployment session. Only when none exists, show the device code and browser URL. Explain that the confirmation grants a short, team-scoped deployment session and reveals no infrastructure credential.
4. Continue automatically while the client uploads and polls. On failure, preserve the stage, error code, and safe filename evidence; fix and rerun with the same valid session.
5. On success, let the client verify the homepage, same-origin JavaScript/CSS assets, and SPA deep link. Then use the Browser tool to confirm meaningful content, console/network health, and desktop/mobile rendering.
6. If the app uses AI, verify `GET /__camp/ai/health`. Frontend code must call the same-origin gateway and contain no provider URL, model name, or key.

Treat this workflow as internal execution, not learner homework. Never report success before HTTP and browser verification pass.

## AI gateway

Frontend code calls its own deployment origin:

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

The server selects the model, enforces quota, and owns the upstream credential.

## Versions and rollback

Run the same command to publish a new version. The project keeps its stable hostname, and identical uploads are deduplicated.

Rollback is a signed-in browser action in the training platform. Never attempt SSH or direct filesystem rollback.

Read [references/api.md](references/api.md) only for protocol diagnosis or client maintenance. Read [references/troubleshooting.md](references/troubleshooting.md) when a deployment fails.
