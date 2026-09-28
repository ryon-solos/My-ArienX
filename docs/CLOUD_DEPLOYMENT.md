# ArienX Cloud Core + Device Bridge

Cloud Core is a set of Netlify Functions. It is stateless between requests;
Netlify Blobs holds only device records, queued bridge tasks, and explicitly
selected cloud-safe memory. It never hosts the desktop Python process.

## Deploy

1. Deploy this repository to Netlify using the root `netlify.toml`.
2. Enable Netlify Identity. Cloud routes reject unauthenticated users.
3. Set `GEMINI_API_KEY` in Netlify environment variables for `/api/chat`.
   Optionally set `OPENROUTER_API_KEY` and `OPENROUTER_MODEL`. These keys stay
   in Netlify; desktop API keys are never synchronized.
4. From the desktop, provision `cloud_bridge` with the deployment URL and a
   Netlify Identity access token. Registration uploads only the device's public
   Ed25519 key; its private key remains in local `api_keys.json`.

## Online/offline contract

- A desktop posts an authenticated heartbeat every 30 seconds. Cloud reports it
  offline after 90 seconds without a heartbeat.
- Cloud chat and cloud-safe memory remain usable while no device is online.
- Computer, files, screen, and camera are queued only for an online paired
  device. The desktop executes them through the existing action dispatcher, so
  its confirmation and verification gates still apply.
- `memory/long_term.json`, chats, camera/screen frames, files, provider keys,
  OAuth tokens, and bridge private keys stay device-private. Only entries added
  explicitly to `memory/cloud_safe.json` can be synchronized.
- Sync uses a revision number. A stale writer receives HTTP 409 and must fetch,
  merge, and retry; it never overwrites newer cloud data silently.
- The desktop syncs only non-empty `cloud_safe.json`. On a 409 it pauses and
  reports the conflict locally; it does not retry by overwriting cloud state.

## API surfaces

- `POST /api/chat` — authenticated cloud Gemini or OpenRouter reply.
- `POST /api/research` — authenticated Gemini grounded research; it stays
  separate from OpenRouter because source-grounding is provider-specific.
- `GET|PUT /api/memory` — authenticated cloud-safe memory with revisions.
- `POST /api/bridge/register` — user-authenticated public-key pairing.
- `POST /api/bridge/heartbeat` — device-signed presence update.
- `GET /api/bridge/status` — user-authenticated online/capability state.
- `POST|GET|PUT /api/bridge/tasks` — authenticated command queue, claim, and completion.

No WhatsApp integration is included in this phase.
