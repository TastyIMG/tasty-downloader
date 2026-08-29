"""Route registration entrypoint for ComfyUI.

Keep this file thin — handlers live in *_routes.py modules.
"""

from . import download_routes, push_routes, settings_routes  # noqa: F401
from .rclone_ops import bootstrap_rclone_binary_async

bootstrap_rclone_binary_async()
