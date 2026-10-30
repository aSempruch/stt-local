import os
from pathlib import Path

APP_NAME = "STT Local"
APPLICATION_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / APP_NAME
CONFIG_PATH = APPLICATION_SUPPORT_DIR / "config.json"
PROCESSORS_DIR = APPLICATION_SUPPORT_DIR / "processors"
MODEL = "mlx-community/whisper-large-v3-turbo"
LANGUAGE = "en"
SAMPLE_RATE = 16_000
POLL_INTERVAL_SECONDS = 0.15
DEFAULT_IDLE_UNLOAD_SECONDS = 600.0
DEFAULT_DOUBLE_TAP_SECONDS = 0.20
DEFAULT_LONG_PRESS_SECONDS = 0.50
STREAM_STOP_TIMEOUT_SECONDS = 2.0
SPEECH_MODEL = "mlx-community/Kokoro-82M-bf16"
SPEECH_VOICE = "af_heart"
SPEECH_SAMPLE_RATE = 24_000
CONTROL_SOCKET = APPLICATION_SUPPORT_DIR / "control.sock"
VOICE_SESSIONS_DIR = APPLICATION_SUPPORT_DIR / "voice-sessions"
MEETINGS_DIR = APPLICATION_SUPPORT_DIR / "meetings"
DEFAULT_MEETINGS_FOLDER = "~/Documents/Meetings"
MEETING_MODEL = "ibm-granite/granite-speech-4.1-2b"
MEETING_MODEL_REVISION = "de575db64086f84fdc79da4932d1076e965bc546"
MEETING_MODEL_LABEL = "Granite Speech 4.1 2B"
VAD_MODEL = "mlx-community/silero-vad"
# Built from native/meeting-recorder by scripts/install-launch-agent.sh.
MEETING_RECORDER = Path(
    os.environ.get("STT_LOCAL_MEETING_RECORDER")
    or Path(__file__).resolve().parents[2] / "build" / "stt-local-meeting-recorder"
)
MEETING_MAX_SECONDS = 4 * 3600
MEETING_MIN_FREE_BYTES = 2_000_000_000
