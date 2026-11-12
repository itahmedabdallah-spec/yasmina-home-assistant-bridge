# Architecture

The bridge exposes a Yango/Yasmina Smart Home provider and delegates physical state to Home Assistant.

Flow:

1. Yasmina / Yango Smart Home sends an authenticated provider request.
2. native_http.py validates OAuth claims, request schema and per-device scope.
3. Lighting calls are sent to Home Assistant's local API.
4. TV calls are strictly typed. A private Home Assistant shell bridge talks to a persistent ADB worker over a Unix socket.
5. Power-on and app-launch may use a bounded, deduplicated Wake-on-LAN sequence.
6. Device state is read back before a synchronous action is reported as verified. Long cold-start app launches are accepted as a single deferred sequence and verified by the worker journal.

Security properties:
- no raw arbitrary shell command from the public provider;
- no HA token in public routes;
- tokens are stored hashed;
- Unix socket instead of a TCP listener for the TV worker;
- no retry of uncertain physical writes;
- explicit device/scope allowlists.
