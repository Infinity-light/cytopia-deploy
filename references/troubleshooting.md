# Troubleshooting

## `index.html` missing

Point `--dist` at the final build output, not the source directory. Common outputs are `dist`, `build`, and `out`.

## A secret or `.env` was detected

Do not weaken or bypass the check. Remove the credential from the project and its build output. For model calls, use the same-origin `/__camp/ai/chat` endpoint.

## Unsupported file type

Remove source-only, executable, database, archive, or server configuration files from the static output. The shared deployer publishes browser assets only.

## Device code expired

Run the deploy command again. Never save or reuse an old device code.

## Authorization page asks for login

Sign in with the existing training-camp account, select the current camp, and return to the same authorization URL.

## Domain is not ready

Use the returned `fallback_url` under `summercamp.godpenai.com` while a mentor checks the wildcard record or pre-resolved domain pool. Do not request DNS credentials.

## AI gateway unavailable

Check `GET /__camp/ai/health` on the deployed hostname. If `available` is false, report it to a mentor; do not add a personal model key to the frontend.

## New deployment failed

The previous successful release remains live. Fix the stage-specific error, request a new device code, and redeploy. Use the training platform rollback action only when a previously published version must be restored.
