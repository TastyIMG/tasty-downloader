from pathlib import Path

# Custom-node root (parent of python/), where registry.json / config.json / js live.
EXTENSION_DIR = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY_PATH = EXTENSION_DIR / "registry.json"
LOCAL_REGISTRY_PATH = EXTENSION_DIR / "registry.local.json"
CONFIG_PATH = EXTENSION_DIR / "config.json"
CHUNK_SIZE = 1024 * 1024
SECRET_PLACEHOLDER = "********"

DEFAULT_PUSH_FOLDERS = [
    "diffusion_models",
    "unet",
    "loras",
    "vae",
    "clip",
    "clip_vision",
    "controlnet",
    "upscale_models",
    "checkpoints",
    "embeddings",
    "hypernetworks",
]

DEFAULT_R2 = {
    "account_id": "",
    "access_key_id": "",
    "secret_access_key": "",
    "bucket": "",
    "endpoint": "",
    "public_base_url": "",
    "config_url": "",
    "remote_name": "tasty-r2",
    "chunk_size": "64M",
    "upload_concurrency": 4,
    "push_folders": list(DEFAULT_PUSH_FOLDERS),
}
