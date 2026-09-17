from pathlib import Path

APP_NAME = "Local Dictation"
APPLICATION_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_PATH = APPLICATION_SUPPORT_DIR / "config.json"
PROCESSORS_DIR = APPLICATION_SUPPORT_DIR / "processors"
TRIGGER_FILE = Path("/tmp/stt-toggle")

MODEL = "mlx-community/whisper-large-v3-turbo"
LANGUAGE = "en"
SAMPLE_RATE = 16_000
POLL_INTERVAL_SECONDS = 0.15
DEFAULT_IDLE_UNLOAD_SECONDS = 600.0
