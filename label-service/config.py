"""Central configuration for the label service.

Every physical/alignment value and every tunable limit lives here so that
calibration and policy changes never require hunting through the codebase.
Values can be overridden with environment variables of the same name.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value is not None else default


# ---------------------------------------------------------------------------
# Physical layout (all values in pixels at LABEL_DPI unless noted)
#
# These are nominal starting values, confirmed against a physical caliper
# measurement of the actual die-cut stock on 2026-07-28 (25.0mm x 12.8mm per
# sticker). Update the *_MM constants below if a different stock is used.
# ---------------------------------------------------------------------------
LABEL_DPI = _env_int("LABEL_DPI", 300)


def mm_to_px(mm: float, dpi: int = LABEL_DPI) -> int:
    return round(mm / 25.4 * dpi)


def pt_to_px(pt: float, dpi: int = LABEL_DPI) -> int:
    return round(pt / 72.0 * dpi)


# ---------------------------------------------------------------------------
# Printer / CUPS
#
# Full-bleed printing hinges on one detail in the PPD:
#
#   *ImageableArea w72h72: "4.08 4.32 69.12 65.28"   (65.04 x 60.96 pt)
#   *PaperDimension w72h72: "72.00 72.00"            (72 x 72 pt)
#   *HWMargins: 0 0 0 0
#
# CUPS applies ImageableArea to NAMED page sizes, which insets everything by
# ~1.0-2.4mm per edge -- that inset is a driver declaration, not a hardware
# limit, and it is what produced the stubborn ~2mm border on test prints.
# CUSTOM page sizes use *HWMargins instead, which is zero here, so asking for
# a custom page of exactly the media size gives true edge-to-edge output.
#
# The custom page MUST equal the physical media (1in x 1in for this 2-up
# stock). Declaring a larger custom page just runs the image off the label.
#
# Placement is further pinned down by writing 300dpi metadata into the PNG
# (see label_renderer.save_print_file) so the image's natural size is exactly
# one page, plus print-scaling=none so CUPS may not rescale it. Without the
# dpi metadata CUPS has no natural size to honour and falls back to fitting
# the imageable area, reintroducing the border.
# ---------------------------------------------------------------------------
PRINTER_NAME = _env_str("PRINTER_NAME", "DYMO_LabelWriter_450")

# Physical media: DYMO "1/2 in x 1 in (2 up)" -- two 12.7 x 25.4mm stickers
# tiling a 1 inch square. Caliper check on 2026-07-28 read 25.0 x 12.8mm per
# sticker, consistent with that pitch minus die-cut kerf.
PAGE_WIDTH_PT = _env_float("PAGE_WIDTH_PT", 72.0)   # across the label pair
PAGE_HEIGHT_PT = _env_float("PAGE_HEIGHT_PT", 72.0)  # along the feed
PAGE_WIDTH_PX = _env_int("PAGE_WIDTH_PX", pt_to_px(PAGE_WIDTH_PT))
PAGE_HEIGHT_PX = _env_int("PAGE_HEIGHT_PX", pt_to_px(PAGE_HEIGHT_PT))

# The named w72h72 media applies the PPD's smaller ImageableArea and scales a
# 10 mm calibration interval to about 9 mm. The driver advertises
# Custom.WIDTHxHEIGHT; a custom page uses its zero HWMargins instead.
PAGE_SIZE = _env_str("PAGE_SIZE", "Custom.72x72")
# Prevent CUPS from fitting the 1-inch raster into the smaller ImageableArea
# declared by the DYMO PPD. The PNG's 300-DPI metadata supplies its natural
# physical size; this option tells CUPS to preserve it.
LP_EXTRA_OPTIONS: list[str] = ["-o", "print-scaling=none"]

# ---------------------------------------------------------------------------
# Page geometry
#
# Every dimension below is derived from the page so the composed canvas
# always tiles it exactly. Deriving these independently (e.g. from measured
# millimetres) previously produced 304px of content on a 300px page, which
# silently clipped.
# ---------------------------------------------------------------------------
LABELS_PER_PAGE = _env_int("LABELS_PER_PAGE", 2)

# One sticker's slot on the page, in printed (post-rotation) orientation.
CELL_WIDTH_PX = PAGE_WIDTH_PX // LABELS_PER_PAGE
CELL_HEIGHT_PX = PAGE_HEIGHT_PX

# The rotation confirmed empirically on the Mac test queue.
OUTPUT_ROTATION_DEGREES = _env_int("OUTPUT_ROTATION_DEGREES", 90)

# Individual labels are drawn in landscape and rotated 90 degrees, so the
# pre-rotation canvas is the cell with its axes swapped.
if OUTPUT_ROTATION_DEGREES % 180 == 90:
    _LABEL_W, _LABEL_H = CELL_HEIGHT_PX, CELL_WIDTH_PX
else:
    _LABEL_W, _LABEL_H = CELL_WIDTH_PX, CELL_HEIGHT_PX

# Measured from a photographed calibration print (2026-07-28): the raster
# origin sits this far into the sticker along the FEED axis, so a page of the
# full sticker length overhangs the far end by the same amount. The page
# cannot start before its own origin, so the label's long axis is shortened
# to what is actually addressable. Recover this space by reducing the offset,
# not by lengthening the label past the page.
FEED_OFFSET_MM = _env_float("FEED_OFFSET_MM", -2.0)
FEED_OFFSET_PX = _env_int("FEED_OFFSET_PX", mm_to_px(FEED_OFFSET_MM))

INDIVIDUAL_LABEL_WIDTH_PX = _env_int("INDIVIDUAL_LABEL_WIDTH_PX", CELL_HEIGHT_PX)
INDIVIDUAL_LABEL_HEIGHT_PX = _env_int("INDIVIDUAL_LABEL_HEIGHT_PX", CELL_WIDTH_PX)

# Labels tile the page edge to edge; separation between the two printed
# stickers comes from LABEL_PADDING_PX inside each cell, not from a gap
# carved out of the page (which would push content off the media).
PAIR_GAP_PX = _env_int("PAIR_GAP_PX", 0)
PAIR_MARGIN_X_PX = _env_int("PAIR_MARGIN_X_PX", 0)
PAIR_MARGIN_Y_PX = _env_int("PAIR_MARGIN_Y_PX", 0)

# ---------------------------------------------------------------------------
# Sticker registration
#
# The page origin and the die-cut stickers are NOT aligned. Test prints on
# 2026-07-28 put the raster origin roughly 2mm right and 1mm up from the
# sticker corner, and that offset did not move when the page width was
# changed from 72pt to 108pt to 161pt -- consistent with the printer's real
# unprintable margins, which the PPD documents for this media as
# ImageableArea "4.08 4.32 69.12 65.28" (left 1.44mm, bottom 1.52mm) and
# which the driver applies regardless of a custom page's HWMargins.
#
# These constants say where the sticker pair sits in page coordinates, so
# content is laid out on the stickers rather than on the page. Negative
# values mean the sticker starts before the printable origin; that strip
# cannot be printed and is clipped.
#
# Recalibrate by running `print_labels.py --calibration` and measuring the
# gap between each printed boundary and the sticker edge. If the media is
# ever repositioned so page and sticker align, set both back to 0.
# ---------------------------------------------------------------------------
# Axis mapping, established from a photographed print: page X is the
# CROSS-WEB axis (which sticker, and the sticker's short side); page Y is the
# FEED axis (along the sticker's long side). Feed-axis registration is
# handled by FEED_OFFSET_MM above, so only X is corrected here.
#
# Calibration: X = -2.0mm put the A/B split 1mm below the die-cut gap, and
# X = 0 put it 1mm above, so the response is 1:1.
#
# Custom-page calibration on 2026-07-28 put the A/B raster boundary about
# 1 mm into sticker B and let B overhang its far edge. Shift the complete
# raster 1 mm toward A while retaining the correct 1:1 physical scale.
STICKER_ORIGIN_X_MM = _env_float("STICKER_ORIGIN_X_MM", -1.0)

# The sticker pair starts this far BEFORE the printable origin on the
# cross-web axis, so label A's printable width is short by this much while
# label B's is unaffected. Measured 2026-07-28: with equal cells the A/B
# split landed 1mm past the die-cut gap.
CROSS_OFFSET_MM = _env_float("CROSS_OFFSET_MM", 0.0)
CROSS_OFFSET_PX = _env_int("CROSS_OFFSET_PX", mm_to_px(CROSS_OFFSET_MM))

# Label A's printable width, short by the registration error; label B keeps
# the full cell width.
CELL_A_WIDTH_PX = _env_int("CELL_A_WIDTH_PX", CELL_WIDTH_PX - CROSS_OFFSET_PX)

# The custom-page print left about 2.5 mm blank at the leading/left edge
# while overhanging the far/right edge. Use a conservative 2 mm translation
# into that inaccessible leading strip; this changes registration, not scale.
STICKER_ORIGIN_Y_MM = _env_float("STICKER_ORIGIN_Y_MM", -2.0)
STICKER_ORIGIN_X_PX = _env_int("STICKER_ORIGIN_X_PX", mm_to_px(STICKER_ORIGIN_X_MM))
STICKER_ORIGIN_Y_PX = _env_int("STICKER_ORIGIN_Y_PX", mm_to_px(STICKER_ORIGIN_Y_MM))

PAIR_PAGE_SIZE = PAGE_SIZE

# ---------------------------------------------------------------------------
# Label content layout
# ---------------------------------------------------------------------------
QR_QUIET_ZONE_MODULES = _env_int("QR_QUIET_ZONE_MODULES", 4)
# Keep discretionary whitespace tight because the calibrated print path
# already reserves an empirical 2 mm left / 1 mm bottom safe zone. Do not
# reduce QR_QUIET_ZONE_MODULES: that whitespace is part of QR reliability.
LABEL_PADDING_PX = _env_int("LABEL_PADDING_PX", mm_to_px(0.15))
TEXT_COLUMN_GAP_PX = _env_int("TEXT_COLUMN_GAP_PX", mm_to_px(0.4))
TEXT_TRAILING_MARGIN_PX = _env_int("TEXT_TRAILING_MARGIN_PX", mm_to_px(1.2))
QR_SIZE_REDUCTION_PX = _env_int("QR_SIZE_REDUCTION_PX", mm_to_px(0.5))
# Most of the QR's left quiet zone may overlap the consistently blank
# physical strip. Retain 0.5 mm inside the source raster as tolerance.
QR_LEADING_INSET_PX = _env_int("QR_LEADING_INSET_PX", mm_to_px(0.5))

# Sticker B sits physically above sticker A. Its content was observed about
# 1 mm too high, so translate only its post-rotation ink toward the centre
# gap without moving either cell boundary.
LABEL_B_CONTENT_SHIFT_X_PX = _env_int(
    "LABEL_B_CONTENT_SHIFT_X_PX", -mm_to_px(1.0)
)

FONT_REGULAR_PATH = str(BASE_DIR / "app" / "static" / "fonts" / "Roboto_Regular.ttf")
FONT_BOLD_PATH = str(BASE_DIR / "app" / "static" / "fonts" / "Roboto_Bold.ttf")

ID_FONT_MAX_SIZE_PX = _env_int("ID_FONT_MAX_SIZE_PX", 28)
ID_FONT_MIN_SIZE_PX = _env_int("ID_FONT_MIN_SIZE_PX", 14)
SAMPLE_FONT_MAX_SIZE_PX = _env_int("SAMPLE_FONT_MAX_SIZE_PX", 28)
SAMPLE_FONT_MIN_SIZE_PX = _env_int("SAMPLE_FONT_MIN_SIZE_PX", 14)
TEXT_FONT_MAX_SIZE_PX = _env_int("TEXT_FONT_MAX_SIZE_PX", 16)
TEXT_FONT_MIN_SIZE_PX = _env_int("TEXT_FONT_MIN_SIZE_PX", 9)
LINE_SPACING_PX = _env_int("LINE_SPACING_PX", 1)
GROUP_SPACING_PX = _env_int("GROUP_SPACING_PX", 4)

# ---------------------------------------------------------------------------
# Field length limits (section 4 of the spec)
# ---------------------------------------------------------------------------
DATE_LENGTH = 10  # DD/MM/YYYY
MAX_SAMPLE_ID_LENGTH = _env_int("MAX_SAMPLE_ID_LENGTH", 16)
MAX_EXPERIMENT_ID_LENGTH = _env_int("MAX_EXPERIMENT_ID_LENGTH", 16)
MAX_REQUESTOR_ID_LENGTH = _env_int("MAX_REQUESTOR_ID_LENGTH", 12)

# ---------------------------------------------------------------------------
# ID / counter behaviour
# ---------------------------------------------------------------------------
LABEL_ID_DIGITS = _env_int("LABEL_ID_DIGITS", 4)
LABEL_ID_MIN = 1
LABEL_ID_MAX = 10 ** LABEL_ID_DIGITS - 1  # 9999 for 4 digits
COUNTER_NAME = "label_id"

# ---------------------------------------------------------------------------
# Batch / upload limits (section 18)
# ---------------------------------------------------------------------------
MAX_COPIES_PER_ROW = _env_int("MAX_COPIES_PER_ROW", 100)
MAX_BATCH_LABELS = _env_int("MAX_BATCH_LABELS", 1000)
MAX_SPREADSHEET_ROWS = _env_int("MAX_SPREADSHEET_ROWS", 1000)
MAX_UPLOAD_BYTES = _env_int("MAX_UPLOAD_BYTES", 5 * 1024 * 1024)

# ---------------------------------------------------------------------------
# Storage locations
# ---------------------------------------------------------------------------
INSTANCE_DIR = Path(_env_str("INSTANCE_DIR", str(BASE_DIR / "instance")))
DATABASE_PATH = Path(_env_str("DATABASE_PATH", str(INSTANCE_DIR / "labels.db")))
GENERATED_DIR = Path(_env_str("GENERATED_DIR", str(BASE_DIR / "generated")))
BATCH_UPLOAD_DIR = Path(_env_str("BATCH_UPLOAD_DIR", str(INSTANCE_DIR / "batch_uploads")))
GENERATED_RETENTION_HOURS = _env_int("GENERATED_RETENTION_HOURS", 24)

# ---------------------------------------------------------------------------
# Web / auth
# ---------------------------------------------------------------------------
HOST = _env_str("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8000)
WAITRESS_THREADS = _env_int("WAITRESS_THREADS", 2)
SECRET_KEY = _env_str("SECRET_KEY", "dev-secret-key-change-me")
ADMIN_PASSWORD_HASH = _env_str("ADMIN_PASSWORD_HASH", "")
AUTH_REQUIRED = os.environ.get("AUTH_REQUIRED", "1") != "0"
SESSION_LIFETIME = timedelta(hours=_env_int("SESSION_LIFETIME_HOURS", 8))
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
LOGIN_MAX_FAILURES = _env_int("LOGIN_MAX_FAILURES", 5)
LOGIN_LOCKOUT_SECONDS = _env_int("LOGIN_LOCKOUT_SECONDS", 300)

# ---------------------------------------------------------------------------
# Batch failure policy
# ---------------------------------------------------------------------------
STOP_ON_FIRST_BATCH_FAILURE = os.environ.get("STOP_ON_FIRST_BATCH_FAILURE", "1") != "0"

INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
BATCH_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
