from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any

from .coordinator import DictationState

BARS = 24
BAR_WIDTH = 3.0
BAR_GAP = 2.0
MIN_BAR = 3.0
DOT_SIZE = 8.0
DOT_GAP = 10.0
PADDING = 14.0
HEIGHT = 36.0
VERTICAL_PADDING = 9.0
TRACK_WIDTH = BARS * BAR_WIDTH + (BARS - 1) * BAR_GAP
TRACK_HEIGHT = 6.0
WIDTH = PADDING * 2 + DOT_SIZE + DOT_GAP + TRACK_WIDTH
BOTTOM_MARGIN = 24.0
FRAME_INTERVAL = 1 / 30
FLOOR_DB = -55.0
# The top of the scale follows the recent peak so quiet built-in mics and
# hot headsets both fill the pill; the minimum keeps room noise flat.
MIN_CEILING_DB = -40.0
MAX_CEILING_DB = -12.0
GAIN_HISTORY = 128


def overlay_mode(state: DictationState) -> str | None:
    return {
        DictationState.RECORDING_LOADING: "loading",
        DictationState.RECORDING_READY: "recording",
        DictationState.STOPPING: "transcribing",
        DictationState.TRANSCRIBING: "transcribing",
    }.get(state)


def level_to_unit(rms: float, ceiling_db: float = MAX_CEILING_DB) -> float:
    """Map block RMS onto 0..1 using a decibel scale, which tracks loudness."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return min(1.0, max(0.0, (db - FLOOR_DB) / (ceiling_db - FLOOR_DB)))


def adaptive_ceiling(levels: list[float]) -> float:
    peak = max((level for level in levels if level > 0), default=0.0)
    if peak <= 0:
        return MIN_CEILING_DB
    return min(MAX_CEILING_DB, max(MIN_CEILING_DB, 20 * math.log10(peak)))


def recording_heights(levels: list[float], bars: int = BARS) -> list[float]:
    """Newest level on the right; pad the left while a recording is young."""
    latest = list(levels)[-bars:] if bars > 0 else []
    return [0.0] * (bars - len(latest)) + latest


def transcribing_heights(seconds: float, bars: int = BARS) -> list[float]:
    phase = seconds * 2 * math.pi * 0.8
    return [
        0.15 + 0.35 * (0.5 + 0.5 * math.sin(phase - index * 0.45))
        for index in range(bars)
    ]


def progress_fill_width(progress: float, track_width: float = TRACK_WIDTH) -> float:
    """Width of the filled part of the download bar; never fully empty, so
    the bar reads as started."""
    fraction = min(1.0, max(0.0, progress))
    return max(TRACK_HEIGHT, fraction * track_width)


def panel_frame(
    visible: tuple[float, float, float, float], size: tuple[float, float]
) -> tuple[float, float, float, float]:
    x, y, width, _height = visible
    panel_width, panel_height = size
    return (x + (width - panel_width) / 2, y + BOTTOM_MARGIN, panel_width, panel_height)


class DictationOverlay:
    """Floating pill shown while dictating. It never takes focus or clicks,
    because the transcript is pasted into whichever app is focused."""

    def __init__(
        self,
        levels: Callable[[int], list[float]],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._levels = levels
        self._clock = clock
        self._mode: str | None = None
        self._download_progress: float | None = None
        self._panel: Any | None = None
        self._view: Any | None = None
        self._timer: Any | None = None

    def set_state(self, state: DictationState) -> None:
        mode = overlay_mode(state)
        if mode == self._mode:
            return
        self._mode = mode
        if mode is None:
            self._hide()
        else:
            self._show()

    def set_download_progress(self, progress: float | None) -> None:
        """While the model downloads, a progress bar replaces the waveform."""
        self._download_progress = progress

    def close(self) -> None:
        self._mode = None
        self._hide()

    def _show(self) -> None:
        import AppKit

        panel = self._ensure_panel()
        x, y, width, height = panel_frame(_screen_under_mouse(), (WIDTH, HEIGHT))
        panel.setFrame_display_(AppKit.NSMakeRect(x, y, width, height), False)
        self._tick()
        panel.orderFrontRegardless()
        if self._timer is None:
            self._timer = AppKit.NSTimer.timerWithTimeInterval_repeats_block_(
                FRAME_INTERVAL, True, lambda _timer: self._tick()
            )
            # Common modes keep the waveform moving while the status menu is open.
            AppKit.NSRunLoop.mainRunLoop().addTimer_forMode_(
                self._timer, AppKit.NSRunLoopCommonModes
            )

    def _hide(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None
        if self._panel is not None:
            self._panel.orderOut_(None)

    def _tick(self) -> None:
        mode = self._mode
        if mode is None or self._view is None:
            return
        self._view.progress = self._download_progress
        if mode == "transcribing":
            heights = transcribing_heights(self._clock())
        else:
            history = self._levels(GAIN_HISTORY)
            ceiling = adaptive_ceiling(history)
            heights = recording_heights(
                [level_to_unit(level, ceiling) for level in history[-BARS:]]
            )
        self._view.mode = mode
        self._view.heights = heights
        self._view.setNeedsDisplay_(True)

    def _ensure_panel(self) -> Any:
        if self._panel is not None:
            return self._panel
        import AppKit

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT),
            AppKit.NSWindowStyleMaskBorderless
            | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setIgnoresMouseEvents_(True)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )
        view = _view_class().alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT)
        )
        view.mode = "recording"
        view.heights = [0.0] * BARS
        view.progress = None
        panel.setContentView_(view)
        self._panel = panel
        self._view = view
        return panel


def _screen_under_mouse() -> tuple[float, float, float, float]:
    import AppKit

    mouse = AppKit.NSEvent.mouseLocation()
    screens = list(AppKit.NSScreen.screens())
    screen = next(
        (s for s in screens if AppKit.NSMouseInRect(mouse, s.frame(), False)),
        AppKit.NSScreen.mainScreen() or screens[0],
    )
    frame = screen.visibleFrame()
    return (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)


_VIEW_CLASS: Any | None = None


def _view_class() -> Any:
    # Objective-C classes can only be registered once per process.
    global _VIEW_CLASS
    if _VIEW_CLASS is None:
        import AppKit

        class STTLocalOverlayView(AppKit.NSView):
            def drawRect_(self, _rect):
                _draw(self)

        _VIEW_CLASS = STTLocalOverlayView
    return _VIEW_CLASS


def _draw(view: Any) -> None:
    import AppKit

    bounds = view.bounds()
    height = bounds.size.height
    radius = height / 2
    pill = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        bounds, radius, radius
    )
    AppKit.NSColor.colorWithWhite_alpha_(0.08, 0.88).setFill()
    pill.fill()
    border = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSInsetRect(bounds, 0.5, 0.5), radius - 0.5, radius - 0.5
    )
    border.setLineWidth_(1.0)
    AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.14).setStroke()
    border.stroke()

    mode = view.mode
    dot_color, bar_alpha = {
        "recording": (AppKit.NSColor.systemRedColor(), 0.95),
        "loading": (AppKit.NSColor.systemOrangeColor(), 0.6),
        "transcribing": (AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.45), 0.75),
    }[mode]
    dot_color.setFill()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        AppKit.NSMakeRect(PADDING, (height - DOT_SIZE) / 2, DOT_SIZE, DOT_SIZE)
    ).fill()

    x = PADDING + DOT_SIZE + DOT_GAP
    if view.progress is not None:
        _draw_progress(x, height, view.progress)
        return

    AppKit.NSColor.colorWithWhite_alpha_(1.0, bar_alpha).setFill()
    usable = height - 2 * VERTICAL_PADDING - MIN_BAR
    for value in view.heights:
        bar_height = MIN_BAR + value * usable
        rect = AppKit.NSMakeRect(x, (height - bar_height) / 2, BAR_WIDTH, bar_height)
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            rect, BAR_WIDTH / 2, BAR_WIDTH / 2
        ).fill()
        x += BAR_WIDTH + BAR_GAP


def _draw_progress(x: float, height: float, progress: float) -> None:
    import AppKit

    y = (height - TRACK_HEIGHT) / 2
    radius = TRACK_HEIGHT / 2
    AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.18).setFill()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(x, y, TRACK_WIDTH, TRACK_HEIGHT), radius, radius
    ).fill()
    AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.9).setFill()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        AppKit.NSMakeRect(x, y, progress_fill_width(progress), TRACK_HEIGHT),
        radius,
        radius,
    ).fill()
