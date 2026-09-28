# Cloud Core and Device Bridge

- `cloud/netlify/functions/` - stateless Netlify Cloud Core endpoints: authenticated bridge presence and cloud-safe memory.
- `cloud/netlify/functions/_shared/auth.ts` - Netlify Identity user checks and Ed25519 device-signature verification.
- `core/cloud_bridge.py` - optional desktop heartbeat/registration client; private key stays on the device.
- `memory/cloud_safe.py` - explicit, secret-rejecting opt-in memory export; `long_term.json` remains device-private.
- `netlify.toml` and `cloud/package.json` - deploy/build configuration and only cloud dependencies.
- `docs/CLOUD_DEPLOYMENT.md` - deployment, environment, and online/offline contract.
