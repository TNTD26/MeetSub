"""PyInstaller hook for webrtcvad-wheels.

The upstream contrib hook calls copy_metadata("webrtcvad"), but the
distribution is installed as "webrtcvad-wheels". MeetSub does not need the
distribution metadata at runtime, so this small override avoids the failed
metadata lookup while the native extension is still collected normally.
"""

hiddenimports = []
