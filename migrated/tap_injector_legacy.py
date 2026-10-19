"""
Tap injector module.

Two injection strategies are supported:

* ``wda``        — WebDriverAgent over usbmux (Path A). Precise x/y taps.
                  Requires Developer Mode ON + a signed, *running* WDA app on
                  the phone. This is the preferred strategy on iOS 16+ devices
                  where Xcode 26 can launch the WDA test runner.
* ``voicecontrol`` — iOS VoiceControl + Shortcuts (Path B). Native, zero app
                  install on the phone beyond Settings. Works on ANY iOS
                  version (including iOS 15) because it does NOT use the
                  Xcode/instruments test-runner launch channel. Coarse,
                  named-area gestures only (no arbitrary x/y). This is the
                  documented fallback for phones where WDA cannot launch.

VoiceControl trigger mechanism
------------------------------
The controller opens the Shortcuts URL scheme for a *named* VoiceControl-
linked Shortcut:

    shortcuts://run-shortcut?name=<CommandName>

The URL is opened on the device through ``go-ios webinspector launch``, which
is the URL-opener present on the macOS host. Web Inspector must be enabled on
the phone (Settings > Safari > Advanced > Web Inspector) for that opener to
work; if it is not, injection degrades with a clear error rather than failing
silently.

Each ``gesture_path`` action maps to a named VoiceControl command via a
per-device command map (see ``VOICECONTROL_COMMAND_MAP`` and the device
config ``strategy_args``). Example device config entry::

    {"label": "iPhone-01", "udid": "...", "strategy": "voicecontrol",
     "ios_version": "15.8.8",
     "strategy_args": {"commands": {
         "tap_top_right": "Tap Top Right",
         "tap_top_left":  "Tap Top Left",
         "swipe_up":      "Swipe Up",
         "swipe_down":    "Swipe Down",
         "go_home":       "Go Home"
     }}}
"""

import time
import sys
import subprocess
import atexit
import socket
import shlex
import urllib.parse
from typing import Dict, List, Optional

from src.controller.exceptions import HardwareNotAvailable

# Default gesture -> VoiceControl command-name map. Overridable per device via
# config ``strategy_args.commands``. Keys are the gesture ``action`` verbs this
# injector understands; values are the exact Shortcut/voice-command names the
# operator created on the phone (Settings > Accessibility > Voice Control >
# Customize Commands).
VOICECONTROL_COMMAND_MAP: Dict[str, str] = {
    "tap_top_right": "Tap Top Right",
    "tap_top_left": "Tap Top Left",
    "tap_bottom_right": "Tap Bottom Right",
    "tap_bottom_left": "Tap Bottom Left",
    "tap_center": "Tap Center",
    "swipe_up": "Swipe Up",
    "swipe_down": "Swipe Down",
    "swipe_left": "Swipe Left",
    "swipe_right": "Swipe Right",
    "long_press": "Long Press",
    "go_home": "Go Home",
    "double_tap": "Double Tap",
}


class TapInjector:
    """Injects tap gestures to iOS devices."""

    def __init__(self, strategy: str = "wda",
                 command_map: Optional[Dict[str, str]] = None):
        """
        Initialize TapInjector.

        Args:
            strategy: Injection strategy ("wda" or "voicecontrol").
            command_map: Optional per-device override of the gesture->command
                name mapping (used by voicecontrol strategy).
        """
        self.strategy = strategy
        self._command_map = dict(VOICECONTROL_COMMAND_MAP)
        if command_map:
            self._command_map.update(command_map)
        self._tunnels = {}
        self._clients = {}
        self._wda_launches = {}
        atexit.register(self._cleanup)

    def _cleanup(self):
        """Cleanup tunnels on shutdown."""
        for p in self._tunnels.values():
            if p.poll() is None:
                p.terminate()
        self._tunnels.clear()
        for p in self._wda_launches.values():
            if p.poll() is None:
                p.terminate()
        self._wda_launches.clear()

    def _get_free_port(self):
        """Get an ephemeral free port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def get_wda_client(self, device_id: Optional[str] = None):
        """Return a facebook-wda Client bound to the supervisor-owned WDA on 127.0.0.1:8100.
        Does NOT launch WDA (the launchd supervisor owns it). Raises HardwareNotAvailable
        if wda is not importable."""
        try:
            import wda
        except ImportError:
            raise HardwareNotAvailable("facebook-wda not installed")
        return wda.Client("http://127.0.0.1:8100")

    # ------------------------------------------------------------------ #
    # WDA (Path A)
    # ------------------------------------------------------------------ #
    @property
    def available(self) -> bool:
        """Check if tap injection is available for the configured strategy."""
        if self.strategy == "wda":
            try:
                import wda  # noqa: F401
                return True
            except ImportError:
                return False
        if self.strategy == "voicecontrol":
            # The opener (go-ios) presence is checked at inject time; treat the
            # strategy as available if the binary resolves.
            from shutil import which
            return which("ios") is not None
        return False

    def _ensure_wda_running(self, device_id: str) -> None:
        """
        Relaunch WebDriverAgent on the device if its HTTP server is not up.

        WDA is launched by go-ios ``runwda`` and only stays alive while that
        process runs. This method starts ``runwda`` in the background (once)
        so subsequent taps have a live server. Idempotent: skips if a launch
        process is already tracked and alive.
        """
        if device_id in self._wda_launches and self._wda_launches[device_id].poll() is None:
            return
        from shutil import which
        ios_bin = which("ios")
        if ios_bin is None:
            return  # go-ios missing; let the tunnel/client step surface the error
        # Doubled bundle id is what Xcode provisions for the test runner.
        cmd = [
            ios_bin, "runwda",
            "--bundleid", "com.sashapaskal.wda.xctrunner.xctrunner",
            "--testrunnerbundleid", "com.sashapaskal.wda.xctrunner.xctrunner",
            "--xctestconfig", "WebDriverAgentRunner-Runner",
            "--udid", device_id,
        ]
        p = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._wda_launches[device_id] = p
        # Give the runner a moment to come up before the tunnel is opened.
        time.sleep(8)

    async def inject(self, device_id: str, gesture_path: list[dict]) -> bool:
        """
        Inject a gesture sequence to the device.

        Args:
            device_id: The device UDID.
            gesture_path: The gesture sequence to inject.

        Returns:
            bool: True if injection was successful.

        Raises:
            HardwareNotAvailable: If the strategy cannot execute.
        """
        if self.strategy != "wda":
            raise HardwareNotAvailable(
                f"Unknown strategy: {self.strategy} - requires physical hardware."
            )

        try:
            import wda
        except ImportError:
            raise HardwareNotAvailable("facebook-wda not installed")

        # WDA is not a persistent daemon — it dies when its launch process
        # exits. Ensure it is running before we try to use it.
        self._ensure_wda_running(device_id)

        if device_id not in self._clients:
            port = self._get_free_port()
            # Use sys.executable -m so the venv's pymobiledevice3 is found
            # regardless of the worker process PATH (subprocess does not inherit
            # the venv bin on PATH when launched via nohup).
            tunnel_cmd = [
                sys.executable, "-m", "pymobiledevice3", "usbmux", "forward",
                str(port), "8100", "--udid", device_id,
            ]

            p = subprocess.Popen(
                tunnel_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._tunnels[device_id] = p

            # Wait briefly for tunnel to establish
            time.sleep(1)

            if p.poll() is not None:
                raise HardwareNotAvailable(
                    f"Failed to establish WDA tunnel for device {device_id}. "
                    f"Is pymobiledevice3 installed?"
                )

            try:
                c = wda.Client(f"http://127.0.0.1:{port}")
                self._clients[device_id] = c
            except Exception as e:
                raise HardwareNotAvailable(
                    f"WDA Client initialization failed. Developer Mode might be "
                    f"off or WDA is not running: {e}"
                )

        c = self._clients[device_id]

        try:
            win_size = c.window_size()
            width, height = win_size.width, win_size.height

            for gesture in gesture_path:
                action = gesture.get("action")
                if action == "tap":
                    x = gesture.get("x", 0)
                    y = gesture.get("y", 0)
                    if isinstance(x, float) and 0 <= x <= 1:
                        x = int(x * width)
                    if isinstance(y, float) and 0 <= y <= 1:
                        y = int(y * height)
                    c.tap(x, y)
                elif action == "swipe":
                    x1 = gesture.get("x1", 0)
                    y1 = gesture.get("y1", 0)
                    x2 = gesture.get("x2", 0)
                    y2 = gesture.get("y2", 0)
                    if isinstance(x1, float) and 0 <= x1 <= 1:
                        x1 = int(x1 * width)
                    if isinstance(y1, float) and 0 <= y1 <= 1:
                        y1 = int(y1 * height)
                    if isinstance(x2, float) and 0 <= x2 <= 1:
                        x2 = int(x2 * width)
                    if isinstance(y2, float) and 0 <= y2 <= 1:
                        y2 = int(y2 * height)
                    duration = gesture.get("duration", 0.2)
                    c.swipe(x1, y1, x2, y2, duration)
                elif action == "long_press":
                    x = gesture.get("x", 0)
                    y = gesture.get("y", 0)
                    if isinstance(x, float) and 0 <= x <= 1:
                        x = int(x * width)
                    if isinstance(y, float) and 0 <= y <= 1:
                        y = int(y * height)
                    duration = gesture.get("duration", 1.0)
                    c.tap_hold(x, y, duration)
                elif action == "scroll":
                    direction = gesture.get("direction", "up")
                    if direction == "up":
                        c.swipe(width // 2, height // 2, width // 2, height // 4)
                    elif direction == "down":
                        c.swipe(width // 2, height // 4, width // 2, height // 2)
                elif action == "type_text":
                    # New high-level verb: type a string into whatever field is
                    # currently focused on the device (used by the ``comment``
                    # command). facebook-wda's ``send_keys`` injects the text.
                    text = gesture.get("text", "")
                    c.send_keys(text)
                elif action == "app_start":
                    # New high-level verb: bring an app to the foreground by its
                    # bundle id (used by the ``open_app`` command).
                    bundle_id = gesture.get("bundle_id", "")
                    if not bundle_id:
                        raise HardwareNotAvailable(
                            "app_start gesture requires a 'bundle_id'"
                        )
                    c.app_start(bundle_id)
            return True
        except Exception as e:
            if device_id in self._tunnels:
                self._tunnels[device_id].terminate()
                del self._tunnels[device_id]
            if device_id in self._clients:
                del self._clients[device_id]
            raise HardwareNotAvailable(
                f"WDA interaction failed: {e}. Is Developer Mode ON and WDA "
                f"running?"
            )

    # ------------------------------------------------------------------ #
    # VoiceControl (Path B) — iOS 15 fallback, no WDA
    # ------------------------------------------------------------------ #
    async def inject_voicecontrol(self, device_id: str,
                                  gesture_path: list[dict]) -> bool:
        """
        Inject gestures via iOS VoiceControl named commands (Path B).

        Each gesture ``action`` is mapped to a named VoiceControl command and
        fired by opening its Shortcuts URL on the device. Coarse, named-area
        gestures only — no arbitrary x/y.

        Raises:
            HardwareNotAvailable: if the opener is missing or a gesture has no
            mapped command.
        """
        from shutil import which

        ios_bin = which("ios")
        if ios_bin is None:
            raise HardwareNotAvailable(
                "go-ios ('ios') not installed — required to open the Shortcuts "
                "URL that triggers VoiceControl on the device."
            )

        fired: List[str] = []
        for gesture in gesture_path:
            action = gesture.get("action")
            command_name = self._command_map.get(action)
            if command_name is None:
                raise HardwareNotAvailable(
                    f"No VoiceControl command mapped for gesture action "
                    f"'{action}'. Add it to the device config strategy_args or "
                    f"VOICECONTROL_COMMAND_MAP."
                )
            url = (
                "shortcuts://run-shortcut?name="
                + urllib.parse.quote(command_name)
            )
            # go-ios webinspector launch opens an arbitrary URL scheme on the
            # device (requires Web Inspector enabled on the phone).
            cmd = [ios_bin, "webinspector", "launch", url,
                   "--udid", device_id]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                err = (proc.stderr or "").strip()
                raise HardwareNotAvailable(
                    f"VoiceControl trigger failed for '{action}' "
                    f"({command_name}): {err}. Enable Settings > Safari > "
                    f"Advanced > Web Inspector on the device, and ensure the "
                    f"named Shortcut exists."
                )
            fired.append(action)
            # Small inter-gesture gap so VoiceControl can process each command.
            time.sleep(1.2)

        return bool(fired)
