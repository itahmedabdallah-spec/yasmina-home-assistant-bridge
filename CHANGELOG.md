# Changelog

## 1.0.0 — 2026-11-12

Initial reference release.

### Included
- Yango/Yasmina Smart Home provider with OAuth account linking.
- Scoped lighting and TV access.
- Home Assistant lighting control for 1–6 configured lights.
- Android TV power, volume, mute, playback and navigation.
- YouTube, Shahid, Watch It and Yango Play package launch support.
- Bounded Wake-on-LAN and persistent ADB worker over a private Unix socket.
- Configuration through environment variables instead of deployment-specific IDs.
- Deployment examples for Home Assistant, systemd and Caddy.
- A full anonymized live case study documenting how the reference installation evolved, what was verified, what failed, and why the final architecture was chosen.
- Arabic setup, publication-risk, and two-assistant reference notes.
- Public tests and secret/private-material checks.

### Security
- No Home Assistant token, Yango/Yandex credential, OAuth runtime database, ADB key, private IP, user path or production device ID is included.
- The published Home Assistant dispatcher accepts only fixed typed worker operations; arbitrary ADB/shell passthrough is not included.

### Known limitations
- Yasmina/Yango performs the voice-language understanding; the bridge does not receive a general raw user transcript.
- Watch It app launching is supported and backend-verified, but exact spoken-name matching can vary by Yasmina locale/account.
- Smart Home scenarios can open YouTube, but they cannot globally replace Yasmina's built-in music/Quran provider or pass arbitrary media names as free-form YouTube searches.
