# Yasmina Home Assistant Bridge

> **Unofficial community project.** This project is not affiliated with, sponsored by, or endorsed by Yango, Yasmina, or Home Assistant.

A private smart-home bridge that connects Yasmina / Yango Smart Home to Home Assistant, with first-class support for multi-light control and Android TV actions.

Status: v1.0.1 reference release. Built from a real home deployment, then sanitized for public use. No credentials, device IDs, private IPs or runtime databases are included.

For the full anonymized journey from the original home setup to the final architecture, see **[the live sanitized case study](docs/LIVE_CASE_STUDY_AR.md)**.

## What it does

- exposes a Yango Smart Home provider;
- OAuth authorization with hashed tokens and lights/TV scopes;
- controls 1-6 Home Assistant light entities;
- supports group power, brightness, white temperature and exact/relative bulb count;
- controls Android TV power, volume, mute, pause/resume and navigation;
- opens YouTube, Shahid, Watch It and Yango Play by package;
- handles cold TV wake with bounded Wake-on-LAN + ADB verification;
- uses a private Unix-socket TV worker and never exposes ADB keys publicly.

## What it deliberately does not do

- capture raw Yasmina microphone audio;
- provide a free-form speech transcript to an LLM;
- replace Yasmina's built-in music/Quran provider globally;
- publish Home Assistant tokens, Yango cookies, ADB keys, logs or audio.

## Requirements

- Python 3.11+
- Home Assistant reachable locally at 127.0.0.1:8123
- Android TV with ADB already authorized for TV features
- HTTPS reverse proxy for the public provider endpoint
- a Yango/Yasmina Smart Home developer skill/account

Install:

    python3 -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    cp examples/native_config.example.json bridge/native_config.json

Set your entities, TV IP/MAC and host in .env. Load the environment before starting the service.

The Home Assistant long-lived token belongs in bridge/secrets/ha_token with mode 0600. Never commit it.

For the TV worker, copy tv_worker.py and tvcmd.py into Home Assistant, copy the four TV bundle modules into /config/yasmina_bundle, and create /config/yasmina_bridge.env from the provided example. See docs/DEPLOYMENT.md for the complete deployment sequence.

## Activation safety

The example configuration starts with all production gates off. Enable the skill/account/device discovery gates only after validating your own setup, then set bundle_published, bundle_active and actions_enabled explicitly.

## TV app mapping

| Mode | App |
| --- | --- |
| one | YouTube |
| two | Shahid |
| three | Watch It |
| four | Yango Play |

Packages are configurable in .env.

## Voice behavior

Yasmina's own NLU decides which spoken phrase maps to a Smart Home action/scenario. The bridge validates and executes the resulting action; it does not pretend to receive a transcript it cannot access.

See docs/VOICE_COMMANDS_AR.md.

## Setup guides

- Real anonymized live journey and lessons learned: `docs/LIVE_CASE_STUDY_AR.md`
- Arabic Yango/Yasmina setup from zero: `docs/YANGO_SETUP_AR.md`
- Reference two-assistant setup and muted secondary microphone: `docs/REFERENCE_SETUP_AR.md`
- Public-release safety and channel-risk notes: `docs/PUBLISHING_RISK_AR.md`

## Architecture

See docs/ARCHITECTURE.md.

## Security

See SECURITY.md.

## Tests

Run:

    PYTHONPATH=bridge python3 -m unittest discover -s tests -v

## License

Licensed under the **Apache License 2.0**. You may use, modify, distribute, and use the project commercially subject to the license terms. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
