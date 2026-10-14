import base64
import html
import json
import mimetypes
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime
from copy import deepcopy
from pathlib import Path

import requests
from dotenv import load_dotenv
from PySide6.QtCore import (
    Qt, QPoint, QRect, QObject, QThread, Signal, Slot, QTimer, QSize, QUrl
)
from PySide6.QtGui import (
    QColor, QCursor, QDesktopServices, QGuiApplication, QPainter, QPainterPath,
    QPen, QPixmap, QFont, QIcon, QTransform
)
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QTextEdit, QTextBrowser,
    QVBoxLayout, QWidget, QMenu, QSizePolicy, QGridLayout, QStackedWidget
)


APP_NAME = "Ferret"

# ---------------------------------------------------------------------------
# Desktop application paths
# ---------------------------------------------------------------------------
# PyInstaller bundles read-only resources separately from user data.  Ferret
# therefore keeps bundled images next to the packaged application resources,
# while settings, Recents, Library images and the selected theme live under
# %LOCALAPPDATA%\Ferret so they survive upgrades and reinstalls.
SOURCE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
EXECUTABLE_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else SOURCE_DIR
)

LOCAL_APPDATA = Path(
    os.getenv("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
)
USER_DATA_DIR = LOCAL_APPDATA / "Ferret"

ENV_FILE = USER_DATA_DIR / ".env"
ASSET_DIR = RESOURCE_DIR / "assets"
FERRET_LOGO = ASSET_DIR / "ferret_logo.png"
FERRET_PEEK_LEFT = ASSET_DIR / "ferret_peek_left.png"
FERRET_PEEK_RIGHT = ASSET_DIR / "ferret_peek_right.png"
FERRET_HANG_TOP = ASSET_DIR / "ferret_hang_top.png"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemma-4-31b-it:free"
CONFIG_VERSION = "13-desktop"

DATA_DIR = USER_DATA_DIR / "data"
CHAT_FILE = DATA_DIR / "chats.json"
LIBRARY_DIR = DATA_DIR / "library"
THEME_FILE = DATA_DIR / "theme.json"

USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
LIBRARY_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_portable_user_data() -> None:
    """Import data from an older portable Ferret folder on first desktop run."""
    candidates = []
    for candidate in (EXECUTABLE_DIR, SOURCE_DIR, Path.cwd()):
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if resolved not in candidates:
            candidates.append(resolved)

    if not ENV_FILE.exists():
        for base in candidates:
            old = base / ".env"
            if old.exists() and old.resolve() != ENV_FILE.resolve():
                try:
                    shutil.copy2(old, ENV_FILE)
                    break
                except Exception:
                    pass

    if not CHAT_FILE.exists():
        for base in candidates:
            old = base / "data" / "chats.json"
            if old.exists() and old.resolve() != CHAT_FILE.resolve():
                try:
                    CHAT_FILE.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old, CHAT_FILE)
                    break
                except Exception:
                    pass

    if not THEME_FILE.exists():
        for base in candidates:
            old = base / "data" / "theme.json"
            if old.exists() and old.resolve() != THEME_FILE.resolve():
                try:
                    THEME_FILE.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(old, THEME_FILE)
                    break
                except Exception:
                    pass

    try:
        library_is_empty = not any(LIBRARY_DIR.iterdir())
    except Exception:
        library_is_empty = True

    if library_is_empty:
        for base in candidates:
            old_dir = base / "data" / "library"
            if old_dir.exists() and old_dir.resolve() != LIBRARY_DIR.resolve():
                try:
                    for item in old_dir.iterdir():
                        if item.is_file():
                            target = LIBRARY_DIR / item.name
                            if not target.exists():
                                shutil.copy2(item, target)
                    break
                except Exception:
                    pass


_migrate_portable_user_data()
load_dotenv(ENV_FILE)

MODELS = {
    "Gemma 4 31B · Free · Text + Vision (recommended)": "google/gemma-4-31b-it:free",
    "Gemma 4 26B A4B · Free · Text + Vision": "google/gemma-4-26b-a4b-it:free",
    "OpenRouter Free Router · Automatic backup": "openrouter/free",
    "Qwen3-VL 8B · Paid": "qwen/qwen3-vl-8b-instruct",
    "Qwen3-VL 30B A3B · Paid": "qwen/qwen3-vl-30b-a3b-instruct",
}

FREE_FALLBACK_MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
]

PROMPTS = {
    "Explain": "Explain this clearly and simply. If an image is attached, explain what is shown in it.",
    "Solve": "Solve this step by step and give the final answer clearly. Use the attached image if relevant.",
    "Summarize": "Summarize the important information concisely. Use bullet points when helpful.",
    "Extract": "Extract the useful text, code, table, or data from the attached image while preserving structure.",
    "Debug": "Inspect the code or error, identify the cause, explain it simply, and provide a corrected solution.",
    "Describe": "Describe the attached image and highlight the most important details.",
    "Translate": "Translate the supplied content accurately. Preserve formatting where possible.",
    "Write Email": "Write a polished professional email based on my instructions.",
    "Write Essay": "Write a clear, well-structured essay based on my topic and requirements.",
    "Generate Code": "Generate clean, working code for my request and briefly explain the important parts.",
}

SYSTEM_PROMPT = (
    "You are Ferret, a general-purpose multimodal desktop AI assistant. "
    "You can analyze screenshots and uploaded images, answer questions, write essays, draft emails, "
    "generate and debug code, summarize, translate, extract text, and help with general knowledge. "
    "When an image is supplied, inspect it carefully before answering and never invent details that are not visible. "
    "Be practical, accurate, clear, and appropriately concise. When solving or debugging, show understandable steps."
)


def current_model() -> str:
    return (
        os.getenv("FERRET_MODEL", "").strip()
        or os.getenv("SNIPIT_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def current_api_key() -> str:
    return os.getenv("OPENROUTER_API_KEY", "").strip()


def image_to_data_url(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if not mime or not mime.startswith("image/"):
        mime = "image/png"
    with open(path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def save_settings(api_key: str, model: str) -> None:
    ENV_FILE.write_text(
        f"OPENROUTER_API_KEY={api_key.strip()}\n"
        f"FERRET_MODEL={model.strip()}\n"
        f"FERRET_CONFIG_VERSION={CONFIG_VERSION}\n",
        encoding="utf-8",
    )
    load_dotenv(ENV_FILE, override=True)


def migrate_legacy_settings() -> None:
    """Move older free-model configs to the current default once."""
    if not ENV_FILE.exists():
        return
    key = current_api_key()
    version = os.getenv("FERRET_CONFIG_VERSION", "").strip()
    model = os.getenv("FERRET_MODEL", "").strip() or os.getenv("SNIPIT_MODEL", "").strip()
    legacy_free = {"", "openrouter/free", "qwen/qwen3.8-27b:free", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"}
    if key and version != CONFIG_VERSION and model in legacy_free:
        save_settings(key, DEFAULT_MODEL)


migrate_legacy_settings()



def _load_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_chats() -> list[dict]:
    chats = _load_json(CHAT_FILE, [])
    return chats if isinstance(chats, list) else []


def save_chats(chats: list[dict]) -> None:
    _save_json(CHAT_FILE, chats)


def chat_title(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") == "user":
            title = " ".join(str(msg.get("content", "")).strip().split())
            if title:
                return title[:42] + ("…" if len(title) > 42 else "")
    return "New conversation"


def persist_library_image(source: str) -> str:
    """Copy snipped/uploaded images into Ferret's persistent local Library."""
    if not source:
        return ""
    src = Path(source)
    if not src.exists():
        return source
    try:
        src.resolve().relative_to(LIBRARY_DIR.resolve())
        return str(src.resolve())
    except ValueError:
        pass

    suffix = src.suffix.lower() if src.suffix else ".png"
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        suffix = ".png"
    name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}{suffix}"
    dest = LIBRARY_DIR / name
    shutil.copy2(src, dest)
    return str(dest.resolve())


def library_images() -> list[str]:
    items = []
    for p in LIBRARY_DIR.iterdir():
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            items.append(p)
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [str(p.resolve()) for p in items]


THEMES = {
    "original": {
        "label": "Original · Dark sidebar / Light main",
        "sidebar": "#0B0D10", "sidebar_panel": "#15181D", "sidebar_text": "#F5F5F5",
        "sidebar_muted": "#8B929C", "main": "#F5F6F8", "panel": "#FFFFFF", "panel2": "#ECEFF3",
        "text": "#14161A", "muted": "#6E7580", "border": "#D5DAE1", "accent": "#111318",
        "accent_text": "#FFFFFF", "hover": "#E7EAF0", "code": "#111318", "code_text": "#F4F5F7",
        "dark_main": False,
    },
    "inverted": {
        "label": "Inverted · Light sidebar / Dark main",
        "sidebar": "#F6F7F9", "sidebar_panel": "#E9ECF1", "sidebar_text": "#15171B",
        "sidebar_muted": "#747C87", "main": "#101216", "panel": "#171A20", "panel2": "#20242B",
        "text": "#F2F3F5", "muted": "#939BA6", "border": "#2D323B", "accent": "#F4F5F7",
        "accent_text": "#111318", "hover": "#242932", "code": "#080A0D", "code_text": "#F4F5F7",
        "dark_main": True,
    },
    "black": {
        "label": "All Black · Dark mode",
        "sidebar": "#050607", "sidebar_panel": "#0D0F12", "sidebar_text": "#F6F7F8",
        "sidebar_muted": "#858D97", "main": "#050607", "panel": "#0D0F12", "panel2": "#15181D",
        "text": "#F4F5F6", "muted": "#8D959F", "border": "#272C33", "accent": "#FFFFFF",
        "accent_text": "#050607", "hover": "#171B20", "code": "#000000", "code_text": "#F7F7F7",
        "dark_main": True,
    },
    "white": {
        "label": "All White · Clean minimal",
        "sidebar": "#FFFFFF", "sidebar_panel": "#F1F3F6", "sidebar_text": "#14161A",
        "sidebar_muted": "#7A828D", "main": "#FFFFFF", "panel": "#FFFFFF", "panel2": "#F3F5F7",
        "text": "#111318", "muted": "#737B86", "border": "#DDE1E6", "accent": "#050607",
        "accent_text": "#FFFFFF", "hover": "#EEF1F4", "code": "#111318", "code_text": "#F7F7F7",
        "dark_main": False,
    },
}

def load_theme() -> str:
    data = _load_json(THEME_FILE, {"theme": "original"})
    name = str(data.get("theme", "original")) if isinstance(data, dict) else "original"
    return name if name in THEMES else "original"

def save_theme(name: str) -> None:
    if name in THEMES:
        _save_json(THEME_FILE, {"theme": name})

def palette(name: str) -> dict:
    return THEMES.get(name, THEMES["original"])

def is_artifact_request(prompt: str) -> bool:
    p = prompt.lower()
    signals = (
        "write an essay", "write essay", "essay about", "write an email", "draft an email",
        "write email", "cover letter", "generate code", "write code", "create code",
        "write a letter", "draft a letter", "write a report", "create a report",
    )
    return any(s in p for s in signals)


def _strip_markdown_for_copy(text: str) -> str:
    """Make generated prose cleaner when pasted into email/docs."""
    s = text.strip()
    s = re.sub(r"(?m)^#{1,6}\s+", "", s)
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s, flags=re.S)
    s = re.sub(r"__(.*?)__", r"\1", s, flags=re.S)
    s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", s)
    return s.strip()


def copy_payload_for_response(text: str, prompt: str = "") -> str:
    """Return only the useful deliverable for the Copy button.

    Ferret can still display conversational framing in the UI, but generated
    emails/essays/code/letters/reports copy only the actual artifact.
    """
    raw = (text or "").strip()
    p = (prompt or "").lower()
    if not raw:
        return ""

    is_code = any(k in p for k in (
        "generate code", "write code", "create code", "code for", "python code",
        "sql query", "javascript code", "java code", "c++ code",
    ))
    if is_code:
        blocks = re.findall(r"```(?:[A-Za-z0-9_+.#-]+)?\s*\n(.*?)```", raw, flags=re.S)
        if blocks:
            return max(blocks, key=len).strip()

    # Models often wrap the requested deliverable between horizontal rules.
    ruled = re.split(r"(?m)^\s*---+\s*$", raw)
    middle = [part.strip() for part in ruled[1:-1] if part.strip()]
    if middle:
        return _strip_markdown_for_copy(max(middle, key=len))

    is_email_or_letter = any(k in p for k in (
        "email", "mail", "letter", "cover letter", "leave request",
    ))
    if is_email_or_letter:
        # Start at Subject when present, excluding conversational introduction.
        subject = re.search(r"(?im)^\s*(?:\*\*)?subject(?:\*\*)?\s*:", raw)
        content = raw[subject.start():] if subject else raw

        # Remove common conversational follow-up paragraphs after the deliverable.
        tail = re.search(
            r"(?im)^\s*(?:feel free|would you like|let me know|if you(?:'|’)d like|"
            r"i can also|you can customize|you may customize|hope this helps)\b",
            content,
        )
        if tail:
            content = content[:tail.start()].rstrip()

        # If there was no Subject, remove a short "Here's..." style intro paragraph.
        if not subject:
            content = re.sub(
                r"(?is)^\s*(?:sure[,!.]?\s*)?(?:here(?:'|’)s|here is|below is|"
                r"i(?:'|’)ve drafted|i have drafted)\b.*?\n\s*\n",
                "",
                content,
                count=1,
            )
        return _strip_markdown_for_copy(content)

    if is_artifact_request(prompt):
        content = raw
        content = re.sub(
            r"(?is)^\s*(?:sure[,!.]?\s*)?(?:here(?:'|’)s|here is|below is|"
            r"i(?:'|’)ve created|i(?:'|’)ve drafted|i have created|i have drafted)\b.*?\n\s*\n",
            "",
            content,
            count=1,
        )
        tail = re.search(
            r"(?im)^\s*(?:feel free|would you like|let me know|if you(?:'|’)d like|"
            r"i can also|hope this helps)\b",
            content,
        )
        if tail:
            content = content[:tail.start()].rstrip()
        return _strip_markdown_for_copy(content)

    # For ordinary Q&A, the response itself is the useful answer.
    return raw

# -----------------------------------------------------------------------------
# Ferret brand / mascot painting
# -----------------------------------------------------------------------------


def _draw_ferret_head(p: QPainter, rect: QRect, facing: str = "right", upside_down: bool = False):
    """Draw a sleek ferret head inspired by the approved Ferret logo.

    Compared with the old prototype this uses a longer muzzle, smaller nose,
    almond eye-mask, larger rounded ear and tapered neck so it reads as a
    ferret rather than a mole/badger.
    """
    p.save()
    p.setRenderHint(QPainter.Antialiasing)

    cx, cy = rect.center().x(), rect.center().y()
    if upside_down:
        p.translate(cx, cy)
        p.rotate(180)
        p.translate(-cx, -cy)
    if facing == "left":
        p.translate(cx, 0)
        p.scale(-1, 1)
        p.translate(-cx, 0)

    dark = QColor("#090A0D")
    cream = QColor("#FFFDF7")

    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    lw = max(1.5, w * 0.04)

    # Long, tapered ferret silhouette: high back skull -> long muzzle -> narrow neck.
    head = QPainterPath()
    head.moveTo(x + 0.12*w, y + 0.66*h)
    head.cubicTo(x + 0.05*w, y + 0.48*h, x + 0.09*w, y + 0.27*h, x + 0.25*w, y + 0.20*h)
    head.cubicTo(x + 0.33*w, y + 0.05*h, x + 0.52*w, y + 0.07*h, x + 0.61*w, y + 0.18*h)
    head.cubicTo(x + 0.70*w, y + 0.25*h, x + 0.78*w, y + 0.30*h, x + 0.93*w, y + 0.37*h)
    head.cubicTo(x + 1.00*w, y + 0.41*h, x + 1.00*w, y + 0.48*h, x + 0.92*w, y + 0.51*h)
    head.cubicTo(x + 0.82*w, y + 0.54*h, x + 0.77*w, y + 0.62*h, x + 0.69*w, y + 0.67*h)
    head.cubicTo(x + 0.58*w, y + 0.76*h, x + 0.45*w, y + 0.79*h, x + 0.36*w, y + 0.74*h)
    head.cubicTo(x + 0.29*w, y + 0.83*h, x + 0.22*w, y + 0.91*h, x + 0.18*w, y + 0.96*h)
    head.cubicTo(x + 0.16*w, y + 0.86*h, x + 0.14*w, y + 0.76*h, x + 0.12*w, y + 0.66*h)
    head.closeSubpath()
    p.setPen(QPen(dark, lw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.setBrush(cream)
    p.drawPath(head)

    # Prominent rounded ear with black inner crescent, matching the approved logo.
    ear = QPainterPath()
    ear.moveTo(x + 0.20*w, y + 0.33*h)
    ear.cubicTo(x + 0.16*w, y + 0.15*h, x + 0.28*w, y + 0.06*h, x + 0.40*w, y + 0.12*h)
    ear.cubicTo(x + 0.48*w, y + 0.16*h, x + 0.48*w, y + 0.27*h, x + 0.39*w, y + 0.34*h)
    ear.closeSubpath()
    p.setPen(QPen(dark, lw))
    p.setBrush(cream)
    p.drawPath(ear)

    inner_ear = QPainterPath()
    inner_ear.moveTo(x + 0.25*w, y + 0.30*h)
    inner_ear.cubicTo(x + 0.22*w, y + 0.20*h, x + 0.29*w, y + 0.13*h, x + 0.36*w, y + 0.17*h)
    inner_ear.cubicTo(x + 0.40*w, y + 0.20*h, x + 0.39*w, y + 0.27*h, x + 0.34*w, y + 0.31*h)
    inner_ear.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(dark)
    p.drawPath(inner_ear)

    # Logo-like tapered eye mask rather than a round panda patch.
    mask = QPainterPath()
    mask.moveTo(x + 0.46*w, y + 0.32*h)
    mask.cubicTo(x + 0.53*w, y + 0.20*h, x + 0.65*w, y + 0.19*h, x + 0.73*w, y + 0.28*h)
    mask.cubicTo(x + 0.77*w, y + 0.33*h, x + 0.76*w, y + 0.38*h, x + 0.71*w, y + 0.40*h)
    mask.cubicTo(x + 0.61*w, y + 0.44*h, x + 0.52*w, y + 0.42*h, x + 0.46*w, y + 0.32*h)
    mask.closeSubpath()
    p.setBrush(dark)
    p.setPen(Qt.NoPen)
    p.drawPath(mask)

    # Small bright eye inside the mask.
    eye_r = max(1.6, w * 0.035)
    p.setBrush(cream)
    p.drawEllipse(QPoint(int(x + 0.63*w), int(y + 0.30*h)), int(eye_r), int(eye_r))

    # Small pointed nose at the very end of the muzzle.
    nose = QPainterPath()
    nose.moveTo(x + 0.92*w, y + 0.37*h)
    nose.lineTo(x + 0.99*w, y + 0.40*h)
    nose.lineTo(x + 0.93*w, y + 0.47*h)
    nose.cubicTo(x + 0.89*w, y + 0.45*h, x + 0.89*w, y + 0.40*h, x + 0.92*w, y + 0.37*h)
    nose.closeSubpath()
    p.setBrush(dark)
    p.drawPath(nose)

    # Soft smile/jaw line for the recognizable logo expression.
    p.setPen(QPen(dark, max(1.2, w * 0.025), Qt.SolidLine, Qt.RoundCap))
    smile = QPainterPath()
    smile.moveTo(x + 0.72*w, y + 0.53*h)
    smile.cubicTo(x + 0.78*w, y + 0.58*h, x + 0.86*w, y + 0.57*h, x + 0.90*w, y + 0.51*h)
    p.drawPath(smile)

    # Two short neck-fur cuts make the silhouette feel like a ferret coat.
    p.setPen(QPen(dark, max(1.0, w * 0.022), Qt.SolidLine, Qt.RoundCap))
    p.drawLine(int(x + 0.28*w), int(y + 0.66*h), int(x + 0.22*w), int(y + 0.76*h))
    p.drawLine(int(x + 0.33*w), int(y + 0.69*h), int(x + 0.28*w), int(y + 0.81*h))

    p.restore()


def _draw_paw(p: QPainter, center: QPoint, radius: int = 13):
    dark = QColor("#111217")
    cream = QColor("#FAF5EA")
    p.setPen(QPen(dark, 3))
    p.setBrush(cream)
    p.drawEllipse(center, radius, radius - 2)
    p.setPen(QPen(dark, 1.7))
    for dx in (-6, 0, 6):
        p.drawLine(center.x() + dx, center.y() - 5, center.x() + dx - 1, center.y() + 2)


class FerretMark(QWidget):
    """Compact circular brand mark used in the header."""
    def __init__(self, size=42, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if FERRET_LOGO.exists():
            pix = QPixmap(str(FERRET_LOGO))
            if not pix.isNull():
                p.drawPixmap(self.rect(), pix.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                return
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#111217"))
        p.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        _draw_ferret_head(p, self.rect().adjusted(7, 7, -5, -8), "right")


class MiniPeekButton(QPushButton):
    """Header control uses the approved peeking-ferret artwork."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 32)
        self.setToolTip("Hide Ferret to the nearest screen edge")
        self.setStyleSheet(
            "QPushButton{background:#20232B;border:1px solid #343842;border-radius:10px;padding:0;}"
            "QPushButton:hover{background:#292D36;border-color:#C69B6D;}"
        )
        self._pix = QPixmap(str(FERRET_PEEK_LEFT)) if FERRET_PEEK_LEFT.exists() else QPixmap()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self._pix.isNull():
            pix = self._pix.scaled(25, 28, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            p.drawPixmap(6, (self.height()-pix.height())//2, pix)
        else:
            _draw_ferret_head(p, QRect(4, 6, 26, 18), "right")


class PeekingMascot(QWidget):
    """Compact edge mascot with symmetric left/right geometry and a flush top hang."""
    activated = Signal(str)

    SIDE_VISIBLE_W = 36
    VISIBLE_H = 48
    HIT_PAD = 8

    def __init__(self):
        super().__init__()
        self.edge = "right"
        self.drag_start = None
        self.window_start = None
        self.dragged = False
        self.current_screen = None

        left = QPixmap(str(FERRET_PEEK_LEFT)) if FERRET_PEEK_LEFT.exists() else QPixmap()
        right = QPixmap(str(FERRET_PEEK_RIGHT)) if FERRET_PEEK_RIGHT.exists() else QPixmap()
        top = QPixmap(str(FERRET_HANG_TOP)) if FERRET_HANG_TOP.exists() else QPixmap()
        self._assets = {"left": left, "right": right, "top": top}

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Ferret — click to open, drag to another edge")
        self.hide()

    def _nearest_edge(self, pos: QPoint, screen=None) -> str:
        screen = screen or QGuiApplication.screenAt(pos) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        right_boundary = g.left() + g.width()
        distances = {
            "left": abs(pos.x() - g.left()),
            "right": abs(right_boundary - pos.x()),
            "top": abs(pos.y() - g.top()),
        }
        return min(distances, key=distances.get)

    def _configure_size(self):
        # 8 px transparent drag/hit area is kept outside the screen edge.
        self.resize(self.SIDE_VISIBLE_W + self.HIT_PAD, self.VISIBLE_H + 8)

    def show_on_edge(self, edge: str, anchor: QPoint | None = None):
        self.edge = edge
        anchor = anchor or QCursor.pos()
        self.current_screen = QGuiApplication.screenAt(anchor) or QApplication.primaryScreen()
        g = self.current_screen.availableGeometry()
        self._configure_size()
        right_boundary = g.left() + g.width()

        if edge == "left":
            y = max(g.top() + 8, min(anchor.y() - self.height() // 2, g.bottom() - self.height() - 8))
            # Extra hit area lives outside the left edge; the full 36x48 sprite remains visible.
            self.move(g.left() - self.HIT_PAD, y)
        elif edge == "right":
            y = max(g.top() + 8, min(anchor.y() - self.height() // 2, g.bottom() - self.height() - 8))
            # Exact mirror of left: 36 px on-screen, 8 px hit area beyond the right edge.
            self.move(right_boundary - self.SIDE_VISIBLE_W, y)
        else:
            x = max(g.left() + 8, min(anchor.x() - self.width() // 2, right_boundary - self.width() - 8))
            # Paws sit directly against the top desktop boundary; nothing is hidden above the screen.
            self.move(x, g.top())

        self.update()
        self.show()
        self.raise_()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        if self.edge == "left":
            pix = self._assets.get("left", QPixmap())
            if not pix.isNull():
                # Left state is the approved reference: keep exactly as-is.
                target = pix.scaled(self.SIDE_VISIBLE_W, self.VISIBLE_H, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                p.drawPixmap(self.HIT_PAD, 4, target)
                return

        elif self.edge == "right":
            # Build the right state as a true mirror of the approved left state,
            # and make it slightly fuller so it doesn't look undersized.
            base = self._assets.get("left", QPixmap())
            if not base.isNull():
                mirrored = base.transformed(QTransform().scale(-1, 1))
                target = mirrored.scaled(self.SIDE_VISIBLE_W + 4, self.VISIBLE_H + 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                # Shift 4 px left so more of the mascot stays visible on screen.
                p.drawPixmap(-4, 3, target)
                return

        else:
            pix = self._assets.get("top", QPixmap())
            if not pix.isNull():
                # Top state looked too tall. Render it smaller and tighter.
                top_w = 28
                top_h = 38
                target = pix.scaled(top_w, top_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                x = (self.width() - top_w) // 2
                y = 0
                p.drawPixmap(x, y, target)
                return

        # Fallback only when an asset is missing.
        if self.edge == "left":
            _draw_ferret_head(p, QRect(self.HIT_PAD, 12, 34, 28), "right")
        elif self.edge == "right":
            _draw_ferret_head(p, QRect(2, 12, 34, 28), "left")
        else:
            _draw_ferret_head(p, QRect(5, 8, 34, 29), "right", upside_down=True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_start = event.globalPosition().toPoint()
            self.window_start = self.pos()
            self.dragged = False

    def mouseMoveEvent(self, event):
        if self.drag_start is None:
            return
        pos = event.globalPosition().toPoint()
        delta = pos - self.drag_start
        if delta.manhattanLength() > 4:
            self.dragged = True
            screen = QGuiApplication.screenAt(pos) or QApplication.primaryScreen()
            new_edge = self._nearest_edge(pos, screen)
            if new_edge != self.edge or screen != self.current_screen:
                self.edge = new_edge
                self.current_screen = screen
                self._configure_size()
                self.update()
            self.move(pos.x() - self.width() // 2, pos.y() - self.height() // 2)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        pos = event.globalPosition().toPoint()
        if not self.dragged:
            self.activated.emit(self.edge)
        else:
            screen = QGuiApplication.screenAt(pos) or QApplication.primaryScreen()
            self.show_on_edge(self._nearest_edge(pos, screen), pos)
        self.drag_start = None
        self.window_start = None



class QuickIconButton(QPushButton):
    """Icon-only quick action used beside the floating Ferret mascot."""
    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.theme_name = load_theme()
        self.setFixedSize(42, 42)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Snip" if mode == "snip" else "Prompt")
        self.setStyleSheet("QPushButton{background:transparent;border:none;padding:0;}")

    def set_theme(self, name: str):
        self.theme_name = name
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = palette(self.theme_name)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.setPen(QPen(QColor(t["border"]), 1.2))
        p.setBrush(QColor(t["panel"]))
        p.drawRoundedRect(r, 10, 10)
        p.setPen(QPen(QColor(t["text"]), 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        if self.mode == "snip":
            # minimal scissors glyph
            p.drawEllipse(QRect(9, 24, 7, 7))
            p.drawEllipse(QRect(21, 24, 7, 7))
            p.drawLine(15, 25, 29, 11)
            p.drawLine(22, 25, 11, 13)
        else:
            # minimal speech bubble glyph
            bubble = QRect(9, 10, 24, 19)
            p.drawRoundedRect(bubble, 5, 5)
            path = QPainterPath()
            path.moveTo(16, 29)
            path.lineTo(15, 34)
            path.lineTo(22, 29)
            p.drawPath(path)
            p.setBrush(QColor(t["text"]))
            p.setPen(Qt.NoPen)
            for x in (16, 21, 26):
                p.drawEllipse(QPoint(x, 20), 1.2, 1.2)


class QuickActionMenu(QWidget):
    snip_requested = Signal()
    prompt_requested = Signal(str, QPoint)

    def __init__(self):
        super().__init__()
        self.edge = "right"
        self.anchor = QPoint()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(92, 48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(4)
        self.snip_btn = QuickIconButton("snip")
        self.prompt_btn = QuickIconButton("prompt")
        self.snip_btn.clicked.connect(self._snip)
        self.prompt_btn.clicked.connect(self._prompt)
        layout.addWidget(self.snip_btn)
        layout.addWidget(self.prompt_btn)
        self.hide()

    def refresh_theme(self):
        name = load_theme()
        self.snip_btn.set_theme(name)
        self.prompt_btn.set_theme(name)

    def show_near(self, mascot: QWidget, edge: str):
        self.edge = edge
        self.anchor = mascot.geometry().center()
        self.refresh_theme()
        screen = QGuiApplication.screenAt(self.anchor) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        mg = mascot.geometry()
        if edge == "left":
            x = mg.right() + 8
            y = mg.center().y() - self.height() // 2
        elif edge == "right":
            x = mg.left() - self.width() - 8
            y = mg.center().y() - self.height() // 2
        else:
            x = mg.center().x() - self.width() // 2
            y = mg.bottom() + 8
        x = max(g.left() + 4, min(x, g.right() - self.width() - 4))
        y = max(g.top() + 4, min(y, g.bottom() - self.height() - 4))
        self.move(x, y)
        self.show()
        self.raise_()

    def _snip(self):
        self.hide()
        self.snip_requested.emit()

    def _prompt(self):
        self.hide()
        self.prompt_requested.emit(self.edge, self.anchor)

# -----------------------------------------------------------------------------
# Settings / API
# -----------------------------------------------------------------------------


class SettingsDialog(QDialog):
    settings_saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ferret Settings")
        self.setMinimumWidth(470)
        self.setModal(True)
        self.setStyleSheet(
            """
            QDialog { background:#121419; color:#F7F2E9; }
            QLabel { color:#EDE8DF; }
            QLineEdit, QComboBox {
                color:#FAF7F1; background:#1C1F26; border:1px solid #343842;
                border-radius:10px; padding:10px;
            }
            QPushButton {
                color:#FAF7F1; background:#242831; border:1px solid #383D47;
                border-radius:10px; padding:9px 13px;
            }
            QPushButton:hover { border-color:#C69B6D; background:#2B2F38; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(13)

        title = QLabel("Connect Ferret")
        title.setStyleSheet("font-size:22px;font-weight:750;")
        layout.addWidget(title)
        note = QLabel(
            "Ferret sends your prompt and any attached image to the selected OpenRouter model. "
            "The API key is stored only in this project's local .env file."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#9CA3AF;")
        layout.addWidget(note)

        layout.addWidget(QLabel("OpenRouter API key"))
        self.key_box = QLineEdit(current_api_key())
        self.key_box.setEchoMode(QLineEdit.Password)
        self.key_box.setPlaceholderText("sk-or-v1-...")
        layout.addWidget(self.key_box)

        key_actions = QHBoxLayout()
        get_key = QPushButton("Get API key")
        get_key.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://openrouter.ai/keys")))
        show_key = QPushButton("Show / hide")
        show_key.clicked.connect(self._toggle_key)
        key_actions.addWidget(get_key)
        key_actions.addWidget(show_key)
        key_actions.addStretch()
        layout.addLayout(key_actions)

        layout.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        for label, model_id in MODELS.items():
            self.model_combo.addItem(label, model_id)
        idx = self.model_combo.findData(current_model())
        self.model_combo.setCurrentIndex(max(0, idx))
        layout.addWidget(self.model_combo)

        model_note = QLabel(
            "The Free Router is best for prototype testing. Free endpoints can be rate-limited; "
            "paid models are optional."
        )
        model_note.setWordWrap(True)
        model_note.setStyleSheet("color:#858C98;font-size:11px;")
        layout.addWidget(model_note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.setStyleSheet(
            "QPushButton{background:#C69B6D;color:#17120D;border:none;font-weight:750;padding:10px 18px;}"
            "QPushButton:hover{background:#D8AC7C;}"
        )
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _toggle_key(self):
        self.key_box.setEchoMode(
            QLineEdit.Normal if self.key_box.echoMode() == QLineEdit.Password else QLineEdit.Password
        )

    def _save(self):
        key = self.key_box.text().strip()
        if not key:
            QMessageBox.warning(self, APP_NAME, "Paste your OpenRouter API key first.")
            return
        save_settings(key, self.model_combo.currentData())
        self.settings_saved.emit()
        self.accept()


class AIWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, messages: list[dict], prompt: str, image_path: str = ""):
        super().__init__()
        self.messages = deepcopy(messages)
        self.prompt = prompt
        self.image_path = image_path

    def _request_messages(self):
        clean_history = [
            {"role": m.get("role", "user"), "content": m.get("content", "")}
            for m in self.messages[-14:]
            if m.get("role") in {"user", "assistant"}
        ]
        request_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + clean_history
        if self.image_path:
            user_content = [
                {"type": "text", "text": self.prompt},
                {"type": "image_url", "image_url": {"url": image_to_data_url(self.image_path)}},
            ]
        else:
            user_content = self.prompt
        request_messages.append({"role": "user", "content": user_content})
        return request_messages

    @staticmethod
    def _extract_content(data: dict) -> str:
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict):
                    if part.get("type") in {"text", "output_text"}:
                        parts.append(str(part.get("text", "")))
                    elif isinstance(part.get("content"), str):
                        parts.append(part["content"])
            content = "\n".join(p for p in parts if p.strip())
        if content is None:
            content = message.get("reasoning") or ""
        return str(content).strip()

    def run(self):
        try:
            key = current_api_key()
            if not key:
                raise RuntimeError("OpenRouter API key is not configured.")

            headers = {
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "X-Title": APP_NAME,
                "HTTP-Referer": "https://openrouter.ai/",
                "User-Agent": "Ferret-Desktop-AI/2.0",
            }
            messages = self._request_messages()

            # Try the user's selected model first, then other free multimodal routes.
            models_to_try = []
            for model in [current_model(), *FREE_FALLBACK_MODELS]:
                if model and model not in models_to_try:
                    models_to_try.append(model)

            failures = []
            for model in models_to_try:
                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": 0.25,
                    "max_tokens": 1800,
                }

                try:
                    response = requests.post(
                        OPENROUTER_URL,
                        headers=headers,
                        json=payload,
                        timeout=(15, 180),
                    )
                except requests.exceptions.Timeout:
                    failures.append(f"{model}: timeout")
                    continue
                except requests.exceptions.ConnectionError:
                    raise RuntimeError("Could not reach OpenRouter. Check your internet connection and try again.")

                if response.ok:
                    try:
                        content = self._extract_content(response.json())
                    except Exception:
                        content = ""
                    if content:
                        self.finished.emit(content)
                        return
                    failures.append(f"{model}: empty response")
                    continue

                status = response.status_code
                try:
                    data = response.json()
                    detail = data.get("error", {}).get("message") or response.text
                except Exception:
                    detail = response.text

                # Invalid key cannot be fixed by changing models.
                if status == 401:
                    raise RuntimeError("The API key was rejected. Open Settings and check your OpenRouter key.")

                # Rate limits, unavailable providers, insufficient credit on a chosen paid model,
                # or modality mismatch all move silently to the next free vision-capable route.
                if status in {400, 402, 404, 408, 409, 429, 502, 503, 504}:
                    failures.append(f"{model}: HTTP {status}")
                    time.sleep(0.8)
                    continue

                failures.append(f"{model}: HTTP {status} {str(detail)[:120]}")

            raise RuntimeError(
                "All available free vision routes are temporarily busy or unavailable. "
                "Wait a minute and try again. Ferret automatically tried its backup models."
            )

        except Exception as exc:
            self.failed.emit(str(exc))


# -----------------------------------------------------------------------------
# DPI-safe snipping
# -----------------------------------------------------------------------------


class SnipOverlay(QWidget):
    captured = Signal(str)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.origin = QPoint()
        self.current = QPoint()
        self.selecting = False
        self.screen = QGuiApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(self.screen.geometry())
        self.base_pixmap = self.screen.grabWindow(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def _capture_rect(self, logical: QRect) -> QRect:
        sx = self.base_pixmap.width() / max(1, self.width())
        sy = self.base_pixmap.height() / max(1, self.height())
        left = max(0, int(logical.left() * sx))
        top = max(0, int(logical.top() * sy))
        right = min(self.base_pixmap.width(), int((logical.right() + 1) * sx + 0.999))
        bottom = min(self.base_pixmap.height(), int((logical.bottom() + 1) * sy + 0.999))
        return QRect(left, top, max(1, right - left), max(1, bottom - top))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(self.rect(), self.base_pixmap)
        p.fillRect(self.rect(), QColor(8, 10, 14, 165))
        if self.selecting:
            rect = QRect(self.origin, self.current).normalized().intersected(self.rect())
            if not rect.isNull():
                p.save()
                p.setClipRect(rect)
                p.drawPixmap(self.rect(), self.base_pixmap)
                p.restore()
                p.setPen(QPen(QColor("#E6B77E"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(rect.adjusted(0, 0, -1, -1))
                p.setPen(QColor("#FFFFFF"))
                p.drawText(rect.adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignLeft,
                           f"{rect.width()} × {rect.height()}   •   release to capture   •   Esc cancels")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.origin = event.position().toPoint()
            self.current = self.origin
            self.selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.selecting:
            self.current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or not self.selecting:
            return
        logical = QRect(self.origin, self.current).normalized().intersected(self.rect())
        self.selecting = False
        if logical.width() < 12 or logical.height() < 12:
            self.cancelled.emit()
            self.close()
            return
        cropped = self.base_pixmap.copy(self._capture_rect(logical))
        cropped.setDevicePixelRatio(1.0)
        out = Path(tempfile.gettempdir()) / "ferret_capture.png"
        if cropped.save(str(out), "PNG"):
            self.captured.emit(str(out))
        else:
            self.cancelled.emit()
        self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            self.close()
        else:
            super().keyPressEvent(event)


# -----------------------------------------------------------------------------
# Conversation + composer UI
# -----------------------------------------------------------------------------


class ClickableImage(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PromptEdit(QTextEdit):
    send_requested = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ImagePreviewDialog(QDialog):
    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ferret · Image Preview")
        self.resize(900, 650)
        self.setStyleSheet(
            "QDialog{background:#0F1116;color:#F7F2E9;}"
            "QPushButton{background:#20242C;color:#F7F2E9;border:1px solid #363B45;border-radius:10px;padding:8px 12px;}"
            "QPushButton:hover{border-color:#C69B6D;}"
        )
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        back = QPushButton("←  Back to chat")
        back.clicked.connect(self.accept)
        close = QPushButton("×")
        close.setFixedSize(36, 34)
        close.clicked.connect(self.reject)
        top.addWidget(back)
        top.addStretch()
        top.addWidget(close)
        layout.addLayout(top)

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background:#151820;border:1px solid #2B303A;border-radius:16px;")
        pix = QPixmap(image_path)
        if not pix.isNull():
            shown = pix.scaled(820, 540, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.image_label.setPixmap(shown)
        layout.addWidget(self.image_label, 1)

        footer = QLabel("Click Back to return to the conversation")
        footer.setAlignment(Qt.AlignCenter)
        footer.setStyleSheet("color:#858C98;")
        layout.addWidget(footer)


class MessageCard(QFrame):
    copy_clicked = Signal(str)

    def __init__(self, role: str, text: str, image_path: str = "", artifact: bool = False, theme_name: str = "original", copy_text: str = "", parent=None):
        super().__init__(parent)
        self.text = text
        self.copy_text = copy_text or text
        self.theme_name = theme_name
        t = palette(theme_name)
        is_user = role == "user"
        self.setObjectName("message")
        # No chat bubble/card around ordinary messages: conversation stays open and natural.
        self.setStyleSheet(
            f"QFrame#message{{background:transparent;border:none;}}"
            f"QLabel{{border:none;background:transparent;color:{t['text']};}}"
            f"QPushButton{{background:transparent;color:{t['muted']};border:1px solid transparent;border-radius:7px;padding:3px 6px;font-size:14px;}}"
            f"QPushButton:hover{{background:{t['hover']};border-color:{t['border']};color:{t['text']};}}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 8, 6, 10)
        layout.setSpacing(7)

        head = QHBoxLayout()
        title = QLabel("You" if is_user else "Ferret")
        title.setStyleSheet(
            f"font-weight:700;color:{t['text']};font-size:12px;" if is_user
            else f"font-weight:800;color:{t['text']};font-size:12px;"
        )
        head.addWidget(title)
        head.addStretch()
        if not is_user:
            copy_btn = QPushButton("⧉")
            copy_btn.setFixedSize(30, 28)
            copy_btn.setToolTip("Copy response")
            copy_btn.clicked.connect(lambda: self.copy_clicked.emit(self.copy_text))
            head.addWidget(copy_btn)
        layout.addLayout(head)

        if image_path and Path(image_path).exists():
            pix = QPixmap(image_path)
            if not pix.isNull():
                img = ClickableImage()
                img.setCursor(Qt.PointingHandCursor)
                img.setPixmap(pix.scaled(300, 170, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                img.setFixedHeight(min(170, max(72, img.pixmap().height())))
                img.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                img.setStyleSheet(f"background:{t['panel2']};border:1px solid {t['border']};border-radius:9px;padding:4px;")
                img.clicked.connect(lambda: ImagePreviewDialog(image_path, self).exec())
                layout.addWidget(img)

        body = QTextBrowser()
        body.setOpenExternalLinks(True)
        body.setFrameShape(QFrame.NoFrame)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.document().setDefaultStyleSheet(
            "p{margin:0 0 9px 0;}"
            "ul,ol{margin-top:4px;margin-bottom:8px;}"
            "li{margin-bottom:3px;}"
            f"pre{{background-color:{t['code']};color:{t['code_text']};border:1px solid {t['border']};padding:10px;margin:8px 0;}}"
            f"code{{background-color:{t['code']};color:{t['code_text']};padding:2px 4px;}}"
            f"h1,h2,h3{{color:{t['text']};margin-top:8px;margin-bottom:7px;}}"
            f"a{{color:{t['text']};}}"
        )
        if artifact and not is_user:
            # Essays/emails/generated artifacts get one focused content surface, like a writing/code block.
            body.setStyleSheet(
                f"QTextBrowser{{background:{t['panel2']};color:{t['text']};border:1px solid {t['border']};"
                "border-radius:12px;font-size:13px;padding:12px;}"
            )
        else:
            body.setStyleSheet(
                f"QTextBrowser{{background:transparent;color:{t['text']};border:none;font-size:13px;padding:0;}}"
            )

        if is_user:
            body.setPlainText(text)
        else:
            try:
                body.setMarkdown(text)
            except Exception:
                body.setPlainText(text)

        body.document().setTextWidth(720)
        height = int(body.document().size().height()) + (18 if artifact else 6)
        body.setFixedHeight(max(30, min(height, 650)))
        layout.addWidget(body)


class ConversationView(QScrollArea):
    def __init__(self, parent=None, theme_name: str = "original"):
        super().__init__(parent)
        self.theme_name = theme_name
        t = palette(theme_name)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(f"QScrollArea{{background:{t['main']};border:none;}}")
        self.host = QWidget()
        self.host.setStyleSheet(f"background:{t['main']};")
        self.layout_ = QVBoxLayout(self.host)
        self.layout_.setContentsMargins(18, 12, 18, 12)
        self.layout_.setSpacing(3)
        self.layout_.addStretch()
        self.setWidget(self.host)
        self.copy_handler = None
        self.empty = None
        self._show_empty()

    def set_theme(self, theme_name: str):
        self.theme_name = theme_name
        t = palette(theme_name)
        self.setStyleSheet(f"QScrollArea{{background:{t['main']};border:none;}}")
        self.host.setStyleSheet(f"background:{t['main']};")
        if self.empty:
            self.empty.setStyleSheet(f"color:{t['muted']};font-size:14px;padding:60px;")

    def _show_empty(self):
        self.empty = QLabel(
            "Ask Ferret anything — or attach a snip/image with +\n\n"
            "Write essays · Generate code · Draft emails · Analyze screenshots"
        )
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color:{palette(self.theme_name)['muted']};font-size:14px;padding:60px;")
        self.layout_.insertWidget(0, self.empty)

    def clear_messages(self):
        while self.layout_.count() > 1:
            item = self.layout_.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._show_empty()

    def add_message(self, role: str, text: str, image_path: str = "", artifact: bool = False, copy_text: str = ""):
        if self.empty and self.empty.parent() is not None:
            self.empty.hide()
        msg = MessageCard(role, text, image_path, artifact, self.theme_name, copy_text=copy_text)
        if self.copy_handler:
            msg.copy_clicked.connect(self.copy_handler)
        self.layout_.insertWidget(self.layout_.count() - 1, msg)
        QTimer.singleShot(50, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))


class LibraryTile(QFrame):
    selected = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, image_path: str, parent=None, theme_name: str = "original"):
        super().__init__(parent)
        self.image_path = image_path
        t = palette(theme_name)
        self.setFixedSize(190, 155)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"QFrame{{background:{t['panel']};border:1px solid {t['border']};border-radius:12px;}}"
            f"QFrame:hover{{border-color:{t['text']};background:{t['hover']};}}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(7, 7, 7, 7)
        lay.setSpacing(5)
        self.img = QLabel()
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setFixedHeight(112)
        pix = QPixmap(image_path)
        if not pix.isNull():
            self.img.setPixmap(pix.scaled(174, 108, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.img.setStyleSheet(f"background:{t['panel2']};border-radius:8px;")
        lay.addWidget(self.img)
        name = QLabel(Path(image_path).name)
        name.setStyleSheet(f"color:{t['muted']};font-size:9px;border:none;background:transparent;")
        name.setAlignment(Qt.AlignCenter)
        lay.addWidget(name)

        self.delete_btn = QPushButton("×", self)
        self.delete_btn.setFixedSize(22, 22)
        self.delete_btn.move(self.width() - 28, 8)
        self.delete_btn.setCursor(Qt.PointingHandCursor)
        self.delete_btn.setStyleSheet(
            f"QPushButton{{background:{t['panel2']};color:{t['text']};border:1px solid {t['border']};border-radius:11px;font-weight:800;padding:0;}}"
            f"QPushButton:hover{{background:#4A2A2A;border-color:#C78383;color:white;}}"
        )
        self.delete_btn.clicked.connect(lambda: self.delete_requested.emit(self.image_path))
        self.delete_btn.raise_()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self.delete_btn.geometry().contains(event.pos()):
            self.selected.emit(self.image_path)
        super().mousePressEvent(event)


class FerretWindow(QWidget):
    request_snip = Signal()
    hide_requested = Signal(QPoint)

    def __init__(self):
        super().__init__()
        self.image_path = ""
        self.messages: list[dict] = []
        self.current_chat_id: str | None = None
        self.worker_thread = None
        self.worker = None
        self.pending_prompt = ""
        self.pending_image = ""
        self.last_answer = ""
        self.drag_origin = None
        self.window_origin = None
        self.current_theme = load_theme()

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(QIcon(str(FERRET_LOGO)) if FERRET_LOGO.exists() else QIcon())
        self.resize(940, 640)
        self.setMinimumSize(760, 540)
        # Normal Window (not Qt.Tool) so minimize behaves like a real Windows app and appears on taskbar.
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._build_ui()
        self.refresh_recents()
        self.refresh_library()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame()
        self.shell.setObjectName("shell")
        self.shell.setStyleSheet(
            """
            QFrame#shell{background:#101216;border:1px solid #2B2F36;border-radius:22px;}
            QLabel{color:#ECE8E0;}
            QPushButton{color:#EEE9E0;background:#1E2229;border:1px solid #30353F;border-radius:10px;padding:8px 11px;}
            QPushButton:hover{background:#252A32;border-color:#C69B6D;}
            """
        )
        root.addWidget(self.shell)
        outer = QVBoxLayout(self.shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Header: approved Ferret brand remains unchanged. Duplicate settings/tagline removed.
        self.header = QFrame()
        self.header.setFixedHeight(62)
        self.header.setStyleSheet(
            "background:#14171C;border-bottom:1px solid #252A31;"
            "border-top-left-radius:22px;border-top-right-radius:22px;"
        )
        h = QHBoxLayout(self.header)
        h.setContentsMargins(18, 8, 14, 8)
        h.setSpacing(10)
        mark = FerretMark(40)
        self.brand_label = QLabel("Ferret")
        h.addWidget(mark)
        h.addWidget(self.brand_label)
        h.addStretch()

        self.model_label = QLabel()
        h.addWidget(self.model_label)
        self.theme_btn = QPushButton("◐")
        self.theme_btn.setFixedSize(36, 36)
        self.theme_btn.setToolTip("Theme")
        self.theme_btn.clicked.connect(self.show_theme_menu)
        h.addWidget(self.theme_btn)
        self.peek_btn = MiniPeekButton()
        self.peek_btn.clicked.connect(lambda: self.hide_requested.emit(self.geometry().center()))
        h.addWidget(self.peek_btn)

        self.minimize_btn = QPushButton("—")
        self.minimize_btn.setFixedSize(36, 36)
        self.minimize_btn.setToolTip("Minimize")
        self.minimize_btn.clicked.connect(self.minimize_to_taskbar)
        h.addWidget(self.minimize_btn)

        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setFixedSize(36, 36)
        self.maximize_btn.setToolTip("Maximize")
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        h.addWidget(self.maximize_btn)

        self.close_btn = QPushButton("×")
        self.close_btn.setFixedSize(36, 36)
        self.close_btn.setToolTip("Close")
        self.close_btn.clicked.connect(QApplication.quit)
        h.addWidget(self.close_btn)
        outer.addWidget(self.header)
        self.refresh_model_label()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        # Sidebar: New Chat + Recents + Library + persistent recent conversations + one Settings button.
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(190)
        s = QVBoxLayout(self.sidebar)
        s.setContentsMargins(12, 16, 12, 14)
        s.setSpacing(8)

        self.new_chat_btn = QPushButton("＋  New Chat")
        self.new_chat_btn.clicked.connect(self.new_chat)
        s.addWidget(self.new_chat_btn)

        self.recents_btn = QPushButton("▱  Recents")
        self.recents_btn.setStyleSheet(self._nav_style(True))
        self.recents_btn.clicked.connect(self.show_recents)
        s.addWidget(self.recents_btn)

        self.library_btn = QPushButton("▧  Library")
        self.library_btn.setStyleSheet(self._nav_style(False))
        self.library_btn.clicked.connect(self.show_library)
        s.addWidget(self.library_btn)

        self.recent_label = QLabel("RECENTS")
        s.addWidget(self.recent_label)

        self.recents_scroll = QScrollArea()
        self.recents_scroll.setWidgetResizable(True)
        self.recents_scroll.setFrameShape(QFrame.NoFrame)
        self.recents_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recents_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.recents_host = QWidget()
        self.recents_host.setStyleSheet("background:transparent;")
        self.recents_layout = QVBoxLayout(self.recents_host)
        self.recents_layout.setContentsMargins(0, 0, 0, 0)
        self.recents_layout.setSpacing(3)
        self.recents_layout.addStretch()
        self.recents_scroll.setWidget(self.recents_host)
        s.addWidget(self.recents_scroll, 1)

        self.settings_btn = QPushButton("⚙  Settings")
        self.settings_btn.clicked.connect(self.open_settings)
        s.addWidget(self.settings_btn)
        body.addWidget(self.sidebar)

        # Main stack keeps the existing chat design and adds a Library page without redesigning Ferret.
        self.main_stack = QStackedWidget()
        self.main_stack.setStyleSheet("background:#12151A;border:none;")

        self.chat_page = QFrame()
        self.chat_page.setStyleSheet("background:#12151A;")
        m = QVBoxLayout(self.chat_page)
        m.setContentsMargins(16, 14, 16, 14)
        m.setSpacing(10)

        conv_header = QHBoxLayout()
        self.conv_title = QLabel("")
        self.conv_title.hide()
        conv_header.addWidget(self.conv_title)
        conv_header.addStretch()
        m.addLayout(conv_header)

        self.conversation = ConversationView(theme_name=self.current_theme)
        self.conversation.copy_handler = self.copy_text
        m.addWidget(self.conversation, 1)

        self.composer = QFrame()
        self.composer.setObjectName("composer")
        self.composer.setStyleSheet(
            "QFrame#composer{background:#1A1E25;border:1px solid #343A45;border-radius:17px;}"
        )
        c = QVBoxLayout(self.composer)
        c.setContentsMargins(10, 8, 10, 9)
        c.setSpacing(6)

        self.attachment_row = QFrame()
        self.attachment_row.setFixedHeight(74)
        self.attachment_row.setStyleSheet("QFrame{background:transparent;border:none;}")
        ar = QHBoxLayout(self.attachment_row)
        ar.setContentsMargins(0, 0, 0, 0)
        self.thumb_container = QFrame()
        self.thumb_container.setFixedSize(120, 68)
        self.thumb_container.setStyleSheet("QFrame{background:#101218;border:1px solid #303641;border-radius:10px;}")
        self.thumb = ClickableImage(self.thumb_container)
        self.thumb.setGeometry(2, 2, 116, 64)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setCursor(Qt.PointingHandCursor)
        self.thumb.clicked.connect(self.preview_image)
        self.thumb.setStyleSheet("border:none;background:transparent;border-radius:8px;")
        self.remove_image_btn = QPushButton("×", self.thumb_container)
        self.remove_image_btn.setFixedSize(22, 22)
        self.remove_image_btn.move(94, 4)
        self.remove_image_btn.setStyleSheet(
            "QPushButton{background:#0C0E12;color:white;border:1px solid #555C68;border-radius:11px;padding:0;font-weight:800;}"
            "QPushButton:hover{background:#4A2A2A;border-color:#C78383;}"
        )
        self.remove_image_btn.clicked.connect(self.remove_image)
        self.remove_image_btn.raise_()
        ar.addWidget(self.thumb_container)
        self.attachment_meta = QLabel("Image attached · click to preview")
        self.attachment_meta.setStyleSheet("color:#7E8793;font-size:10px;")
        ar.addWidget(self.attachment_meta)
        ar.addStretch()
        c.addWidget(self.attachment_row)
        self.attachment_row.hide()

        entry = QHBoxLayout()
        entry.setSpacing(7)
        self.snip_btn = QPushButton("✂")
        self.snip_btn.setFixedSize(38, 38)
        self.snip_btn.setToolTip("Snip part of the screen")
        self.snip_btn.setStyleSheet(
            "QPushButton{border-radius:19px;background:#252A32;font-size:17px;padding:0;}"
            "QPushButton:hover{border-color:#C69B6D;background:#2D333C;}"
        )
        self.snip_btn.clicked.connect(self.request_snip.emit)
        entry.addWidget(self.snip_btn)

        self.plus_btn = QPushButton("+")
        self.plus_btn.setFixedSize(38, 38)
        self.plus_btn.setStyleSheet(
            "QPushButton{border-radius:19px;background:#252A32;font-size:22px;padding:0;}"
            "QPushButton:hover{border-color:#C69B6D;}"
        )
        self.plus_btn.clicked.connect(self.show_plus_menu)
        entry.addWidget(self.plus_btn)

        self.prompt_btn = QPushButton("P⌄")
        self.prompt_btn.setFixedSize(48, 38)
        self.prompt_btn.setStyleSheet(
            "QPushButton{border-radius:19px;background:#252A32;color:#E5C39B;font-weight:800;padding:0;}"
            "QPushButton:hover{border-color:#C69B6D;}"
        )
        self.prompt_btn.clicked.connect(self.show_prompt_menu)
        entry.addWidget(self.prompt_btn)

        self.prompt_edit = PromptEdit()
        self.prompt_edit.setPlaceholderText("Ask Ferret anything…")
        self.prompt_edit.setFixedHeight(58)
        self.prompt_edit.setStyleSheet(
            "QTextEdit{background:transparent;color:#F2EEE6;border:none;font-size:13px;padding:7px;}"
        )
        self.prompt_edit.send_requested.connect(self.send_message)
        entry.addWidget(self.prompt_edit, 1)

        self.send_btn = QPushButton("➤")
        self.send_btn.setFixedSize(42, 42)
        self.send_btn.setStyleSheet(
            "QPushButton{border-radius:21px;background:#C69B6D;color:#17120D;border:none;font-size:18px;font-weight:800;padding:0;}"
            "QPushButton:hover{background:#D7AA79;}"
            "QPushButton:disabled{background:#5B5148;color:#82786D;}"
        )
        self.send_btn.clicked.connect(self.send_message)
        entry.addWidget(self.send_btn)
        c.addLayout(entry)

        status_row = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#68717E;font-size:9px;")
        self.status.hide()
        status_row.addWidget(self.status)
        status_row.addStretch()
        self.hint_label = QLabel("Enter to send · Shift+Enter for new line")
        status_row.addWidget(self.hint_label)
        c.addLayout(status_row)
        m.addWidget(self.composer, 0)
        self.main_stack.addWidget(self.chat_page)

        self.library_page = QFrame()
        self.library_page.setStyleSheet("background:#12151A;")
        lp = QVBoxLayout(self.library_page)
        lp.setContentsMargins(18, 16, 18, 16)
        self.lib_title = QLabel("Library")
        lp.addWidget(self.lib_title)
        self.lib_note = QLabel("Your snipped and uploaded images are saved locally here. Click an image to attach it to a new message.")
        self.lib_note.setWordWrap(True)
        lp.addWidget(self.lib_note)
        self.library_scroll = QScrollArea()
        self.library_scroll.setWidgetResizable(True)
        self.library_scroll.setFrameShape(QFrame.NoFrame)
        self.library_scroll.setStyleSheet("QScrollArea{background:#12151A;border:none;}")
        self.library_host = QWidget()
        self.library_host.setStyleSheet("background:#12151A;")
        self.library_grid = QGridLayout(self.library_host)
        self.library_grid.setContentsMargins(0, 12, 0, 0)
        self.library_grid.setSpacing(12)
        self.library_scroll.setWidget(self.library_host)
        lp.addWidget(self.library_scroll, 1)
        self.main_stack.addWidget(self.library_page)

        body.addWidget(self.main_stack, 1)
        outer.addLayout(body, 1)
        self.apply_theme(self.current_theme, rerender=False)

    def _nav_style(self, active: bool) -> str:
        t = palette(self.current_theme)
        if active:
            return (
                f"QPushButton{{text-align:left;background:{t['sidebar_panel']};border:1px solid {t['border']};"
                f"color:{t['sidebar_text']};font-weight:700;padding:9px 11px;}}"
                f"QPushButton:hover{{background:{t['hover']};}}"
            )
        return (
            f"QPushButton{{text-align:left;background:transparent;border:1px solid transparent;color:{t['sidebar_text']};"
            f"padding:9px 11px;}} QPushButton:hover{{background:{t['sidebar_panel']};border-color:{t['border']};}}"
        )

    def show_theme_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())
        for name in ("original", "inverted", "black", "white"):
            action = menu.addAction(THEMES[name]["label"])
            action.setCheckable(True)
            action.setChecked(name == self.current_theme)
            action.triggered.connect(lambda checked=False, n=name: self.apply_theme(n))
        menu.exec(self.theme_btn.mapToGlobal(QPoint(0, self.theme_btn.height() + 4)))

    def _menu_style(self):
        t = palette(self.current_theme)
        return (
            f"QMenu{{background:{t['panel']};color:{t['text']};border:1px solid {t['border']};padding:6px;}}"
            f"QMenu::item{{padding:8px 18px;border-radius:6px;}}"
            f"QMenu::item:selected{{background:{t['hover']};}}"
        )

    def apply_theme(self, name: str, rerender: bool = True):
        if name not in THEMES:
            name = "original"
        self.current_theme = name
        save_theme(name)
        t = palette(name)

        self.shell.setStyleSheet(
            f"QFrame#shell{{background:{t['main']};border:1px solid {t['border']};border-radius:22px;}}"
            f"QLabel{{color:{t['text']};}}"
            f"QPushButton{{color:{t['text']};background:{t['panel2']};border:1px solid {t['border']};border-radius:10px;padding:8px 11px;}}"
            f"QPushButton:hover{{background:{t['hover']};}}"
        )
        self.header.setStyleSheet(
            f"background:{t['panel']};border-bottom:1px solid {t['border']};"
            "border-top-left-radius:22px;border-top-right-radius:22px;"
        )
        self.sidebar.setStyleSheet(f"QFrame{{background:{t['sidebar']};border-right:1px solid {t['border']};}}")
        self.new_chat_btn.setStyleSheet(
            f"QPushButton{{background:{t['accent']};color:{t['accent_text']};border:1px solid {t['accent']};"
            "font-weight:700;text-align:center;padding:10px 11px;}"
            f"QPushButton:hover{{background:{t['sidebar_panel']};color:{t['sidebar_text']};border-color:{t['border']};}}"
        )
        self.settings_btn.setStyleSheet(
            f"QPushButton{{text-align:left;background:transparent;color:{t['sidebar_text']};border:1px solid {t['border']};padding:9px 11px;}}"
            f"QPushButton:hover{{background:{t['sidebar_panel']};}}"
        )
        self.recent_label.setStyleSheet(f"color:{t['sidebar_muted']};font-size:9px;font-weight:750;margin-top:8px;")
        self.recents_btn.setStyleSheet(self._nav_style(self.main_stack.currentWidget() == self.chat_page))
        self.library_btn.setStyleSheet(self._nav_style(self.main_stack.currentWidget() == self.library_page))
        self.recents_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.recents_host.setStyleSheet("background:transparent;")

        self.main_stack.setStyleSheet(f"background:{t['main']};border:none;")
        self.chat_page.setStyleSheet(f"background:{t['main']};")
        self.library_page.setStyleSheet(f"background:{t['main']};")
        self.library_scroll.setStyleSheet(f"QScrollArea{{background:{t['main']};border:none;}}")
        self.library_host.setStyleSheet(f"background:{t['main']};")
        self.conv_title.setStyleSheet(f"font-size:13px;font-weight:700;color:{t['muted']};")
        self.lib_title.setStyleSheet(f"font-size:18px;font-weight:800;color:{t['text']};")
        self.lib_note.setStyleSheet(f"color:{t['muted']};font-size:11px;")

        self.composer.setStyleSheet(f"QFrame#composer{{background:{t['panel']};border:1px solid {t['border']};border-radius:17px;}}")
        self.thumb_container.setStyleSheet(f"QFrame{{background:{t['panel2']};border:1px solid {t['border']};border-radius:10px;}}")
        self.remove_image_btn.setStyleSheet(
            f"QPushButton{{background:{t['accent']};color:{t['accent_text']};border:1px solid {t['border']};border-radius:11px;padding:0;font-weight:800;}}"
            f"QPushButton:hover{{background:{t['hover']};color:{t['text']};}}"
        )
        self.attachment_meta.setStyleSheet(f"color:{t['muted']};font-size:10px;")
        round_style = (
            f"QPushButton{{border-radius:19px;background:{t['panel2']};color:{t['text']};border:1px solid {t['border']};padding:0;}}"
            f"QPushButton:hover{{background:{t['hover']};}}"
        )
        self.snip_btn.setStyleSheet(round_style + "QPushButton{font-size:17px;}")
        self.plus_btn.setStyleSheet(round_style + "QPushButton{font-size:22px;}")
        self.prompt_btn.setStyleSheet(round_style + "QPushButton{font-weight:800;}")
        self.prompt_edit.setStyleSheet(f"QTextEdit{{background:transparent;color:{t['text']};border:none;font-size:13px;padding:7px;}}")
        self.send_btn.setStyleSheet(
            f"QPushButton{{border-radius:21px;background:{t['accent']};color:{t['accent_text']};border:none;font-size:18px;font-weight:800;padding:0;}}"
            f"QPushButton:hover{{background:{t['hover']};color:{t['text']};}}"
            f"QPushButton:disabled{{background:{t['panel2']};color:{t['muted']};}}"
        )
        self.status.setStyleSheet(f"color:{t['muted']};font-size:9px;")
        self.hint_label.setStyleSheet(f"color:{t['muted']};font-size:9px;")
        self.brand_label.setStyleSheet(f"font-size:20px;font-weight:800;color:{t['text']};")
        self.model_label.setStyleSheet(f"color:{t['muted']};font-size:10px;")
        self.peek_btn.setStyleSheet(
            f"QPushButton{{background:{t['panel2']};border:1px solid {t['border']};border-radius:10px;padding:0;}}"
            f"QPushButton:hover{{background:{t['hover']};}}"
        )
        self.theme_btn.setStyleSheet(
            f"QPushButton{{background:{t['panel2']};color:{t['text']};border:1px solid {t['border']};border-radius:10px;padding:0;font-size:15px;}}"
            f"QPushButton:hover{{background:{t['hover']};}}"
        )
        for b in (self.minimize_btn, self.maximize_btn, self.close_btn):
            b.setStyleSheet(
                f"QPushButton{{background:{t['panel2']};color:{t['text']};border:1px solid {t['border']};border-radius:10px;padding:0;}}"
                f"QPushButton:hover{{background:{t['hover']};}}"
            )

        self.conversation.set_theme(name)
        if rerender:
            self._rerender_conversation()
            self.refresh_recents()
            self.refresh_library()

    def _rerender_conversation(self):
        self.conversation.clear_messages()
        previous_user = ""
        for msg in self.messages:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))
            image_path = str(msg.get("image_path", "") or "")
            if role == "user":
                previous_user = content
                self.conversation.add_message("user", content, image_path)
            elif role == "assistant":
                self.conversation.add_message("assistant", content, artifact=is_artifact_request(previous_user), copy_text=copy_payload_for_response(content, previous_user))

    # ----- titlebar dragging -----
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.position().y() <= 62:
            self.drag_origin = event.globalPosition().toPoint()
            self.window_origin = self.pos()

    def mouseMoveEvent(self, event):
        if self.isMaximized():
            return
        if self.drag_origin is not None and (event.buttons() & Qt.LeftButton):
            self.move(self.window_origin + event.globalPosition().toPoint() - self.drag_origin)

    def mouseReleaseEvent(self, event):
        self.drag_origin = None
        self.window_origin = None

    def minimize_to_taskbar(self):
        self.showMinimized()

    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            self.maximize_btn.setText("□")
            self.maximize_btn.setToolTip("Maximize")
        else:
            self.showMaximized()
            self.maximize_btn.setText("❐")
            self.maximize_btn.setToolTip("Restore")

    # ----- navigation / persistence -----
    def show_recents(self):
        self.main_stack.setCurrentWidget(self.chat_page)
        self.recents_btn.setStyleSheet(self._nav_style(True))
        self.library_btn.setStyleSheet(self._nav_style(False))
        self.refresh_recents()

    def show_library(self):
        self.main_stack.setCurrentWidget(self.library_page)
        self.recents_btn.setStyleSheet(self._nav_style(False))
        self.library_btn.setStyleSheet(self._nav_style(True))
        self.refresh_library()

    def refresh_recents(self):
        while self.recents_layout.count() > 1:
            item = self.recents_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        chats = sorted(load_chats(), key=lambda c: c.get("updated_at", ""), reverse=True)
        if not chats:
            empty = QLabel("No saved chats yet")
            empty.setStyleSheet(f"color:{palette(self.current_theme)['sidebar_muted']};font-size:10px;padding:8px;")
            self.recents_layout.insertWidget(0, empty)
            return

        for chat in chats:
            title = str(chat.get("title") or "Conversation")
            chat_id = str(chat.get("id"))
            active = chat.get("id") == self.current_chat_id

            row = QWidget()
            row.setStyleSheet("background:transparent;")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(3)

            btn = QPushButton("▱  " + title)
            btn.setToolTip(title)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            t = palette(self.current_theme)
            btn.setStyleSheet(
                (f"QPushButton{{text-align:left;background:{t['sidebar_panel']};border:1px solid {t['border']};color:{t['sidebar_text']};border-radius:8px;padding:7px;}}" if active
                 else f"QPushButton{{text-align:left;background:transparent;border:none;color:{t['sidebar_text']};border-radius:8px;padding:7px;}} QPushButton:hover{{background:{t['sidebar_panel']};}}")
            )
            btn.clicked.connect(lambda _, cid=chat_id: self.load_chat(cid))
            row_layout.addWidget(btn, 1)

            delete_btn = QPushButton("×")
            delete_btn.setFixedSize(26, 26)
            delete_btn.setToolTip("Delete chat")
            delete_btn.setStyleSheet(
                f"QPushButton{{background:transparent;border:none;color:{t['sidebar_muted']};border-radius:7px;padding:0;font-size:15px;}}"
                f"QPushButton:hover{{background:{t['sidebar_panel']};color:{t['sidebar_text']};}}"
            )
            delete_btn.clicked.connect(lambda _, cid=chat_id: self.delete_chat(cid))
            row_layout.addWidget(delete_btn)

            self.recents_layout.insertWidget(self.recents_layout.count() - 1, row)

    def delete_chat(self, chat_id: str):
        chats = load_chats()
        target = next((c for c in chats if str(c.get("id")) == chat_id), None)
        if target is None:
            return

        title = str(target.get("title") or "this chat")
        answer = QMessageBox.question(
            self,
            "Delete chat",
            f'Delete "{title}" from Recents?',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        chats = [c for c in chats if str(c.get("id")) != chat_id]
        save_chats(chats)

        if self.current_chat_id == chat_id:
            self.current_chat_id = None
            self.messages.clear()
            self.last_answer = ""
            self.conversation.clear_messages()
            self.prompt_edit.clear()
            self.remove_image(silent=True)
            self.status.setText("Chat deleted")

        self.refresh_recents()

    def _save_current_chat(self):
        if not self.messages:
            return
        if not self.current_chat_id:
            self.current_chat_id = uuid.uuid4().hex
        chats = load_chats()
        record = {
            "id": self.current_chat_id,
            "title": chat_title(self.messages),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "messages": deepcopy(self.messages),
        }
        replaced = False
        for i, chat in enumerate(chats):
            if chat.get("id") == self.current_chat_id:
                chats[i] = record
                replaced = True
                break
        if not replaced:
            chats.append(record)
        chats.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        save_chats(chats[:200])
        self.refresh_recents()

    def load_chat(self, chat_id: str):
        if self.worker_thread is not None:
            self.status.setText("Wait for the current response before opening another chat")
            return
        chat = next((c for c in load_chats() if str(c.get("id")) == chat_id), None)
        if not chat:
            return
        self.current_chat_id = chat_id
        self.messages = deepcopy(chat.get("messages") or [])
        self.last_answer = ""
        self.conversation.clear_messages()
        previous_user = ""
        for msg in self.messages:
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))
            image_path = str(msg.get("image_path", "") or "")
            if role == "user":
                previous_user = content
                self.conversation.add_message("user", content, image_path)
            elif role == "assistant":
                self.last_answer = content
                self.conversation.add_message("assistant", content, artifact=is_artifact_request(previous_user), copy_text=copy_payload_for_response(content, previous_user))
        self.prompt_edit.clear()
        self.remove_image(silent=True)
        self.show_recents()
        self.status.setText("Conversation loaded")

    def refresh_library(self):
        while self.library_grid.count():
            item = self.library_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        images = library_images()
        if not images:
            empty = QLabel("Your Library is empty. Snip or upload an image and it will appear here.")
            empty.setAlignment(Qt.AlignCenter)
            empty.setStyleSheet(f"color:{palette(self.current_theme)['muted']};padding:70px;")
            self.library_grid.addWidget(empty, 0, 0, 1, 3)
            return
        for i, path in enumerate(images):
            tile = LibraryTile(path, theme_name=self.current_theme)
            tile.setToolTip("Click to attach this image")
            tile.selected.connect(self.attach_library_image)
            tile.delete_requested.connect(self.delete_library_image)
            self.library_grid.addWidget(tile, i // 3, i % 3)
        self.library_grid.setRowStretch((len(images) + 2) // 3, 1)

    def delete_library_image(self, path: str):
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Delete this image from Library?\n\n{Path(path).name}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            Path(path).unlink(missing_ok=True)
            if self.image_path == path:
                self.remove_image()
            self.refresh_library()
            self.status.setText("Library image deleted")
        except Exception as e:
            QMessageBox.warning(self, APP_NAME, f"Could not delete image.\n\n{e}")

    def attach_library_image(self, path: str):
        self.show_recents()
        self.set_image(path, persist=False)
        self.status.setText("Library image attached")

    # ----- menus -----
    def _menu_style(self):
        t = palette(self.current_theme)
        return (
            f"QMenu{{background:{t['panel']};color:{t['text']};border:1px solid {t['border']};border-radius:10px;padding:6px;}}"
            "QMenu::item{padding:8px 26px 8px 12px;border-radius:7px;}"
            f"QMenu::item:selected{{background:{t['hover']};color:{t['text']};}}"
        )

    def show_plus_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())
        upload = menu.addAction("＋  Upload image")
        action = menu.exec(self.plus_btn.mapToGlobal(QPoint(0, -menu.sizeHint().height())))
        if action == upload:
            self.upload_image()

    def show_prompt_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_style())
        actions = {}
        for label, prompt in PROMPTS.items():
            actions[menu.addAction(label)] = prompt
        action = menu.exec(self.prompt_btn.mapToGlobal(QPoint(0, -menu.sizeHint().height())))
        if action in actions:
            self.set_prompt(actions[action])

    def set_prompt(self, text: str):
        self.prompt_edit.setPlainText(text)
        self.prompt_edit.setFocus()
        cursor = self.prompt_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.prompt_edit.setTextCursor(cursor)
        self.status.setText("Prompt selected · edit it or press Send")

    # ----- image / Library -----
    def set_image(self, path: str, persist: bool = True):
        if persist:
            path = persist_library_image(path)
        self.image_path = path
        pix = QPixmap(path)
        if not pix.isNull():
            shown = pix.scaled(112, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.thumb.setPixmap(shown)
            self.attachment_meta.setText(f"{pix.width()} × {pix.height()} px · click image to preview")
        else:
            self.thumb.setText("Image")
            self.attachment_meta.setText("Image attached")
        self.attachment_row.show()
        self.refresh_library()
        self.status.setText("Image attached · choose a prompt or type your own")
        self.prompt_edit.setFocus()

    def remove_image(self, silent: bool = False):
        self.image_path = ""
        self.thumb.clear()
        self.attachment_row.hide()
        if not silent:
            self.status.setText("Image removed")

    def preview_image(self):
        if self.image_path and Path(self.image_path).exists():
            ImagePreviewDialog(self.image_path, self).exec()

    def upload_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose image", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self.set_image(path, persist=True)

    # ----- AI -----
    def ensure_api_key(self) -> bool:
        if current_api_key():
            return True
        d = SettingsDialog(self)
        d.settings_saved.connect(self.refresh_model_label)
        return d.exec() == QDialog.Accepted and bool(current_api_key())

    def send_message(self):
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            self.status.setText("Type a message or select a prompt")
            return
        if not self.ensure_api_key():
            self.status.setText("API key required")
            return
        if self.worker_thread is not None:
            self.status.setText("Ferret is already responding…")
            return

        image_for_request = self.image_path
        prior_messages = deepcopy(self.messages)
        self.messages.append({"role": "user", "content": prompt, "image_path": image_for_request})
        self.conversation.add_message("user", prompt, image_for_request)
        self._save_current_chat()

        # Once sent, the attachment leaves the composer; it remains in conversation/history/Library.
        if image_for_request:
            self.remove_image(silent=True)

        self.prompt_edit.clear()
        self.status.setText("Ferret is thinking…")
        self.send_btn.setEnabled(False)
        self.pending_prompt = prompt
        self.pending_image = image_for_request

        self.worker_thread = QThread(self)
        self.worker = AIWorker(prior_messages, prompt, image_for_request)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_answer)
        self.worker.failed.connect(self._on_error)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.failed.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self._cleanup_worker)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    @Slot(str)
    def _on_answer(self, text: str):
        self.messages.append({"role": "assistant", "content": text})
        self.last_answer = text
        self.conversation.add_message("assistant", text, artifact=is_artifact_request(self.pending_prompt), copy_text=copy_payload_for_response(text, self.pending_prompt))
        self._save_current_chat()
        self.status.setText("Ready")
        self.send_btn.setEnabled(True)

    @Slot(str)
    def _on_error(self, error: str):
        display = "Error: " + error
        self.messages.append({"role": "assistant", "content": display})
        self.conversation.add_message("assistant", display)
        self._save_current_chat()
        self.status.setText("Request failed · Ferret already tried its backup models")
        self.send_btn.setEnabled(True)

    def _cleanup_worker(self):
        if self.worker is not None:
            try:
                self.worker.deleteLater()
            except RuntimeError:
                pass
        self.worker = None
        self.worker_thread = None
        self.pending_prompt = ""
        self.pending_image = ""

    # ----- utility -----
    def copy_text(self, text: str):
        QApplication.clipboard().setText(text)
        self.status.setText("Copied to clipboard")

    def new_chat(self):
        if self.worker_thread is not None:
            self.status.setText("Wait for the current response before starting a new chat")
            return
        self._save_current_chat()
        self.current_chat_id = None
        self.messages.clear()
        self.last_answer = ""
        self.conversation.clear_messages()
        self.prompt_edit.clear()
        self.remove_image(silent=True)
        self.show_recents()
        self.refresh_recents()
        self.status.setText("New chat")

    def open_settings(self):
        d = SettingsDialog(self)
        d.settings_saved.connect(self.refresh_model_label)
        d.exec()

    def refresh_model_label(self):
        m = current_model().lower()
        if "gemma-4" in m:
            label = "Online · Free Vision"
        elif "nemotron-3-nano-omni" in m:
            label = "Online · Free Vision"
        elif m == "openrouter/free":
            label = "Online · Free Router"
        elif ":free" in m:
            label = "Online · Free Vision"
        elif "8b" in m:
            label = "Online · Qwen 8B"
        else:
            label = "Online · Qwen 30B"
        self.model_label.setText(label)

    def show_from_edge(self, edge: str, mascot_pos: QPoint):
        screen = QGuiApplication.screenAt(mascot_pos) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        if edge == "left":
            x = g.left() + 18
            y = max(g.top() + 18, min(mascot_pos.y() - self.height() // 2, g.bottom() - self.height() - 18))
        elif edge == "right":
            x = g.right() - self.width() - 18
            y = max(g.top() + 18, min(mascot_pos.y() - self.height() // 2, g.bottom() - self.height() - 18))
        else:
            x = max(g.left() + 18, min(mascot_pos.x() - self.width() // 2, g.right() - self.width() - 18))
            y = g.top() + 18
        self.move(x, y)
        self.showNormal()
        self.raise_()
        self.activateWindow()


class FerretApp(QObject):
    def __init__(self):
        super().__init__()
        self.window = FerretWindow()
        self.mascot = PeekingMascot()
        self.quick_menu = QuickActionMenu()
        self.overlay = None
        self.snip_origin = "window"
        self.last_edge = "right"
        self.last_anchor = QPoint()

        self.window.request_snip.connect(self.start_snip_from_window)
        self.window.hide_requested.connect(self.hide_to_edge)
        self.mascot.activated.connect(self.show_quick_actions)
        self.quick_menu.snip_requested.connect(self.start_snip_from_quick)
        self.quick_menu.prompt_requested.connect(self.open_prompt_from_quick)

    def start(self):
        screen = QApplication.primaryScreen().availableGeometry()
        self.window.move(screen.center().x() - self.window.width() // 2,
                         screen.center().y() - self.window.height() // 2)
        # First launch needs Settings; otherwise Ferret starts as the floating assistant.
        if not current_api_key():
            self.window.show()
            QTimer.singleShot(300, self.window.open_settings)
        else:
            anchor = QPoint(screen.right() - 10, screen.center().y())
            self.last_edge = "right"
            self.last_anchor = anchor
            self.mascot.show_on_edge("right", anchor)

    def _nearest_edge(self, anchor: QPoint):
        screen = QGuiApplication.screenAt(anchor) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        d = {
            "left": abs(anchor.x() - g.left()),
            "right": abs(g.right() - anchor.x()),
            "top": abs(anchor.y() - g.top()),
        }
        return min(d, key=d.get)

    def hide_to_edge(self, anchor: QPoint):
        edge = self._nearest_edge(anchor)
        self.last_edge = edge
        self.last_anchor = anchor
        self.quick_menu.hide()
        self.window.hide()
        self.mascot.show_on_edge(edge, anchor)

    def show_quick_actions(self, edge: str):
        self.last_edge = edge
        self.last_anchor = self.mascot.geometry().center()
        if self.quick_menu.isVisible():
            self.quick_menu.hide()
            return
        self.quick_menu.show_near(self.mascot, edge)

    def open_prompt_from_quick(self, edge: str, anchor: QPoint):
        self.last_edge = edge
        self.last_anchor = anchor
        self.quick_menu.hide()
        self.mascot.hide()
        # Prompt path opens the full UI with a clean composer.
        self.window.prompt_edit.clear()
        self.window.remove_image(silent=True)
        self.window.show_from_edge(edge, anchor)
        self.window.prompt_edit.setFocus()

    def start_snip_from_window(self):
        self._start_snip("window")

    def start_snip_from_quick(self):
        self._start_snip("quick")

    def _start_snip(self, origin: str):
        self.snip_origin = origin
        self.quick_menu.hide()
        if origin == "quick":
            self.last_edge = self.mascot.edge
            self.last_anchor = self.mascot.geometry().center()
        self.window.hide()
        self.mascot.hide()
        QApplication.processEvents()
        self.overlay = SnipOverlay()
        self.overlay.captured.connect(self._snip_done)
        self.overlay.cancelled.connect(self._snip_cancelled)

    def _snip_done(self, path: str):
        # Captured image is attached first, then the complete Ferret UI opens.
        self.window.set_image(path)
        self.quick_menu.hide()
        self.mascot.hide()
        screen = QGuiApplication.screenAt(self.last_anchor) or QApplication.primaryScreen()
        g = screen.availableGeometry()
        if not self.window.isVisible():
            self.window.move(g.center().x() - self.window.width() // 2,
                             g.center().y() - self.window.height() // 2)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.prompt_edit.setFocus()
        self.overlay = None

    def _snip_cancelled(self):
        self.overlay = None
        if self.snip_origin == "quick":
            self.mascot.show_on_edge(self.last_edge, self.last_anchor)
        else:
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()


if __name__ == "__main__":
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    if FERRET_LOGO.exists():
        app.setWindowIcon(QIcon(str(FERRET_LOGO)))
    app.setQuitOnLastWindowClosed(False)

    controller = FerretApp()
    controller.start()
    sys.exit(app.exec())
