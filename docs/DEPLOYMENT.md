# Deployment

## 1. Public bridge host

Create a Python environment, install requirements, copy `.env.example` to `.env`, and set your own Home Assistant entities, public hostname, TV address/MAC and application packages.

Copy `examples/native_config.example.json` to `bridge/native_config.json`.

Create `bridge/secrets/ha_token` with a Home Assistant long-lived token and permissions `0600`.

Run the service behind HTTPS. A systemd example is in `examples/systemd/yasmina-native.service`; a minimal Caddy example is in `examples/caddy/Caddyfile`.

## 2. Home Assistant TV worker

Copy these files:

- `bridge/tv_worker.py` → `/config/yasmina_tv_worker.py`
- `bridge/tvcmd.py` → `/config/tvcmd.py`
- `bridge/bundle_catalog.py` → `/config/yasmina_bundle/bundle_catalog.py`
- `bridge/bundle_tv.py` → `/config/yasmina_bundle/bundle_tv.py`
- `bridge/bundle_tv_fast.py` → `/config/yasmina_bundle/bundle_tv_fast.py`
- `bridge/bundle_wake_policy.py` → `/config/yasmina_bundle/bundle_wake_policy.py`

Copy `examples/home_assistant/yasmina_bridge.env.example` to `/config/yasmina_bridge.env` and set your TV IP, MAC, broadcast address and app packages.

Merge `examples/home_assistant/configuration.yaml` into Home Assistant and restart Home Assistant once.

The Android TV ADB key remains Home Assistant's existing key under `/config/.storage/`; this repository never ships or copies it.

## 3. Yango/Yasmina skill

Create a Smart Home skill and point its provider endpoints to your HTTPS host. Use the OAuth client/redirect values from `.env`.

Start with every gate in `native_config.json` disabled. Link the account, confirm device discovery, then enable only the gates you have verified.

## 4. Validation

Run offline tests:

    PYTHONPATH=bridge python3 -m unittest discover -s tests -v

Then validate one real command at a time. Do not infer voice success from current TV state alone: confirm the provider received the intended action and the physical device changed afterward.
