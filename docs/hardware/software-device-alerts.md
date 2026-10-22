# Camera preview and mechanism errors

GPIO device checks and hardware fault/recovery bell notifications have been removed.
The mechanism no longer runs the periodic GPIO monitor or exposes device-health data.
Notification requests no longer poll the hardware bridge. Historical hardware audit
records are retained, but do not appear in the bell. Document request, approval and
applicant intake notifications continue normally.

Camera preview, scan progress, Stop, browser heartbeat timeouts, and error handling
for actual GPIO commands and scan failures remain enabled. GPIO outputs cannot detect
unplugged motor/servo wires without feedback sensors.

Restart the local app and refresh the browser to activate the changes:

```bash
systemctl --user restart adgvs-local.service
```

## Live Raspberry Pi camera preview

The selected camera station shows an authenticated JPEG video preview, up to 5 frames
per second at 640×480. It shows LIVE only while frames are recent, shows the frame
timestamp, and dims the last image on error or staleness. Start/Stop preview controls
viewing; leaving the page releases the idle camera after three seconds without requests.
Frames stay in memory and are returned with Cache-Control: no-store. Only authorized
validation operators can fetch `/api/mechanism/camera/frame`; bridge keys stay server-side.

The preview and scan share camera ownership. Before document capture, the preview's
idle camera closes, and the workflow opens its full resolution camera. During feeding,
the preview uses frames from that same camera under an I/O lock. It briefly pauses
during camera switching and the document capture, then resumes after processing.
Preview does not start motors, GPIO outputs or scan sessions. Close any external
camera applications before opening the station preview.

