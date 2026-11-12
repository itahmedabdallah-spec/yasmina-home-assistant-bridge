# Security

Do not commit:
- Home Assistant long-lived access tokens;
- Yango/Yandex session tokens or cookies;
- OAuth SQLite databases or generated registration files;
- Android TV ADB private keys;
- runtime request databases, logs or raw audio.

The public HTTP service must be placed behind HTTPS. Keep Home Assistant and the TV worker on trusted local interfaces. Review native_config.json before enabling actions_enabled and bundle_active.

If you discover a security issue, report it privately to the repository owner rather than opening a public issue with credentials or logs.
