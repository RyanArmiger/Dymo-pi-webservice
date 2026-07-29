"""Renders individual 25x13mm stickers and composes them into two-up print pages.

This module is shared verbatim between the CLI prototype and the Flask app so
that a browser preview can never drift from what actually gets printed
(spec section 15).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

import config


@dataclass
class LabelContent:
    """Everything printed on one physical sticker."""

    label_id: str
    date: str
    sample_id: str
    experiment_id: str
    requestor_id: str


def build_qr_payload(label_id: str) -> str:
    """QR payload is just the four-digit ID for now.

    Kept as its own function so this can later change to a URL or structured
    record identifier without touching the renderer (spec section 5).
    """
    return label_id


def _make_qr_image(payload: str, box_size_px: int) -> Image.Image:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=config.QR_QUIET_ZONE_MODULES,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    modules = len(matrix)

    # Integer pixel scaling only, no interpolation (spec section 5/14).
    scale = max(1, box_size_px // modules)
    size = modules * scale
    img = Image.new("1", (size, size), color=1)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(matrix):
        for x, is_dark in enumerate(row):
            if is_dark:
                draw.rectangle(
                    [x * scale, y * scale, (x + 1) * scale - 1, (y + 1) * scale - 1],
                    fill=0,
                )
    return img.convert("L")


def _load_font(bold: bool, size_px: int) -> ImageFont.FreeTypeFont:
    path = config.FONT_BOLD_PATH if bold else config.FONT_REGULAR_PATH
    return ImageFont.truetype(path, size_px)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=font)
    return right - left


def _fit_font_for_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    bold: bool,
    max_size: int,
    min_size: int,
    max_width: int,
) -> ImageFont.FreeTypeFont:
    """Shrink font size until `text` fits in `max_width`, floored at `min_size`."""
    size = max_size
    font = _load_font(bold, size)
    while size > min_size and _text_width(draw, text, font) > max_width:
        size -= 1
        font = _load_font(bold, size)
    return font


def render_individual_label(
    record: LabelContent, *, debug: bool = False, height_px: int | None = None
) -> Image.Image:
    """Render one sticker with a QR and up to three prominent sample-ID rows."""
    width = config.INDIVIDUAL_LABEL_WIDTH_PX
    # height_px lets one label be narrowed to its printable width when the
    # sticker pair is not registered to the page (see config.CROSS_OFFSET_MM).
    height = height_px if height_px is not None else config.INDIVIDUAL_LABEL_HEIGHT_PX
    pad = config.LABEL_PADDING_PX

    # The calibrated page translation moves these strips outside the raster
    # canvas. Keep real content out of them, while calibration artwork still
    # deliberately reaches the edges to make registration measurable.
    if config.OUTPUT_ROTATION_DEGREES % 360 == 90:
        bottom_inset = max(0, -config.STICKER_ORIGIN_X_PX)
    else:
        bottom_inset = 0
    usable_height = height - bottom_inset

    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)

    qr_size = usable_height - 2 * pad - config.QR_SIZE_REDUCTION_PX
    qr_img = _make_qr_image(build_qr_payload(record.label_id), qr_size)
    qr_img = qr_img.resize((qr_size, qr_size), resample=Image.NEAREST)
    # The physical blank strip can supply most of the QR quiet zone. Keeping
    # the whole leading inset here would double-count it and waste label
    # width; retain only a small registration tolerance in the source.
    qr_x, qr_y = config.QR_LEADING_INSET_PX, pad
    img.paste(qr_img, (qr_x, qr_y))

    text_x = qr_x + qr_size + config.TEXT_COLUMN_GAP_PX
    text_max_width = width - text_x - config.TEXT_TRAILING_MARGIN_PX

    sample_ids = [line for line in record.sample_id.splitlines() if line][:3]
    top_line = f"{record.date}  {record.requestor_id}"
    lines = [
        (top_line, False, config.TEXT_FONT_MAX_SIZE_PX, config.TEXT_FONT_MIN_SIZE_PX),
        *[
            (
                sample_id,
                True,
                config.SAMPLE_FONT_MAX_SIZE_PX,
                config.SAMPLE_FONT_MIN_SIZE_PX,
            )
            for sample_id in sample_ids
        ],
        (record.experiment_id, False, config.TEXT_FONT_MAX_SIZE_PX, config.TEXT_FONT_MIN_SIZE_PX),
        (record.label_id, True, config.ID_FONT_MAX_SIZE_PX, config.ID_FONT_MIN_SIZE_PX),
    ]

    fonts = [
        _fit_font_for_width(
            draw, text, bold=bold, max_size=max_size, min_size=min_size, max_width=text_max_width
        )
        for text, bold, max_size, min_size in lines
    ]

    # Separate the metadata header and footer from the prominent sample-ID
    # block, but keep multiple sample IDs visually grouped.
    line_gaps = []
    for index in range(len(lines) - 1):
        next_is_first_sample = index == 0
        current_is_last_sample = index == len(sample_ids)
        line_gaps.append(
            config.GROUP_SPACING_PX
            if next_is_first_sample or current_is_last_sample
            else config.LINE_SPACING_PX
        )
    total_text_height = sum(f.size for f in fonts) + sum(line_gaps)
    y = max(pad, (usable_height - total_text_height) // 2)

    for index, ((text, _, _, _), font) in enumerate(zip(lines, fonts)):
        draw.text((text_x, y), text, font=font, fill=0)
        bbox = draw.textbbox((text_x, y), text, font=font)
        if index < len(line_gaps):
            y = bbox[3] + line_gaps[index]

    if debug:
        draw.rectangle([0, 0, width - 1, height - 1], outline=0)
        draw.rectangle([qr_x, qr_y, qr_x + qr_size - 1, qr_y + qr_size - 1], outline=0)

    return img


def render_blank_label() -> Image.Image:
    width = config.INDIVIDUAL_LABEL_WIDTH_PX
    height = config.INDIVIDUAL_LABEL_HEIGHT_PX
    return Image.new("L", (width, height), color=255)


def compose_two_up_page(
    label_a: Image.Image, label_b: Image.Image | None = None, *, debug: bool = False
) -> Image.Image:
    """Tile two already-rotated sticker images across one full-bleed print canvas.

    The canvas is exactly config.PAGE_WIDTH_PX x PAGE_HEIGHT_PX, matching the
    custom CUPS page declared in config.PAGE_SIZE, and each label occupies one
    CELL_WIDTH_PX-wide cell with no gap or margin between cells. Labels are
    sized to their cell, so this is an exact tiling of the page: nothing is
    scaled, clipped or centred with leftover slack. Keeping ink off the
    die-cut edges is LABEL_PADDING_PX's job, inside render_individual_label.

    If `label_b` is None the second position is left completely blank
    (spec section 8 — never duplicate the final record to fill a gap).
    """
    page_width = config.PAGE_WIDTH_PX
    page_height = config.PAGE_HEIGHT_PX
    cell_width = config.CELL_WIDTH_PX
    origin_x = config.STICKER_ORIGIN_X_PX
    origin_y = config.STICKER_ORIGIN_Y_PX

    page = Image.new("L", (page_width, page_height), color=255)

    w, h = label_a.size
    gap = config.PAIR_GAP_PX
    content_width = w * 2 + gap
    x_start = origin_x + (page_width - content_width) // 2
    y_start = origin_y + (page_height - h) // 2
    page.paste(label_a, (x_start, y_start))
    if label_b is not None:
        if config.LABEL_B_CONTENT_SHIFT_X_PX:
            shifted = Image.new("L", label_b.size, color=255)
            shift = config.LABEL_B_CONTENT_SHIFT_X_PX
            if shift < 0:
                shifted.paste(label_b.crop((-shift, 0, label_b.width, label_b.height)), (0, 0))
            else:
                shifted.paste(
                    label_b.crop((0, 0, label_b.width - shift, label_b.height)),
                    (shift, 0),
                )
            label_b = shifted
        page.paste(label_b, (x_start + w + gap, y_start))

    if debug:
        draw = ImageDraw.Draw(page)
        draw.rectangle(
            [origin_x, origin_y,
             origin_x + cell_width * config.LABELS_PER_PAGE - 1, origin_y + page_height - 1],
            outline=0,
        )
        for index in range(1, config.LABELS_PER_PAGE):
            split_x = origin_x + index * cell_width
            draw.line([(split_x, 0), (split_x, page_height - 1)], fill=0, width=1)

    return page


def rotate_for_printer(label: Image.Image) -> Image.Image:
    """Rotate one individual sticker for the printer's expected feed orientation.

    Each sticker is rotated on its own, before the two-up page is composed,
    so the final page's aspect ratio stays close to the square w72h72 page
    (25x13mm landscape stickers become 13x25mm portrait ones, and two of
    those side by side is roughly square). Rotating the whole two-up page
    after compositing instead produced a long narrow strip that printed
    with the stickers arranged incorrectly against the real physical
    two-up stock. Rotation direction was confirmed empirically against the
    Mac test queue (spec section 2) rather than relying on CUPS
    `orientation-requested`.
    """
    if config.OUTPUT_ROTATION_DEGREES % 360 == 0:
        return label
    return label.rotate(-config.OUTPUT_ROTATION_DEGREES, expand=True, fillcolor=255)


def save_print_file(page: Image.Image, path: Path) -> Path:
    """Write the print-ready PNG, stamping physical resolution into it.

    The dpi (pHYs) metadata is load-bearing, not cosmetic: it is what gives
    CUPS a natural size for the image. Without it CUPS cannot know the canvas
    is exactly one page and falls back to scaling the image into the
    printer's declared imageable area, which reintroduces a ~2mm border.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    page.convert("L").save(
        path,
        format="PNG",
        dpi=(config.LABEL_DPI, config.LABEL_DPI),
    )
    return path


def render_pair_page(
    record_a: LabelContent, record_b: LabelContent | None, *, debug: bool = False
) -> Image.Image:
    """Full pipeline for one two-up print page: render -> rotate each -> compose."""
    label_a = rotate_for_printer(
        render_individual_label(record_a, debug=debug)
    )
    label_b = (
        rotate_for_printer(render_individual_label(record_b, debug=debug))
        if record_b is not None
        else None
    )
    return compose_two_up_page(label_a, label_b, debug=debug)


def _render_calibration_half(label_letter: str, height_px: int | None = None) -> Image.Image:
    """One half of the calibration page, drawn in landscape (readable)
    orientation before per-label rotation.

    Carries a millimetre ruler rather than a grid so a physical print can be
    measured directly: if the printed spacing between the numbered ticks is
    not 5mm, the job was scaled somewhere in the pipeline.
    """
    w = config.INDIVIDUAL_LABEL_WIDTH_PX
    h = height_px if height_px is not None else config.INDIVIDUAL_LABEL_HEIGHT_PX
    pad = config.LABEL_PADDING_PX
    half = Image.new("L", (w, h), color=255)
    draw = ImageDraw.Draw(half)

    # Outer edge of the cell: shows exactly where the page boundary lands
    # relative to the physical die cut.
    draw.rectangle([0, 0, w - 1, h - 1], outline=0)
    # Safe area: content is never drawn outside this.
    draw.rectangle([pad, pad, w - 1 - pad, h - 1 - pad], outline=0)

    px_per_mm = config.LABEL_DPI / 25.4
    font = _load_font(False, 13)
    mm = 0
    while True:
        x = round(mm * px_per_mm)
        if x > w - 1:
            break
        major = mm % 5 == 0
        draw.line([(x, 0), (x, 10 if major else 5)], fill=0)
        if major and mm > 0:
            draw.text((x + 2, 12), str(mm), font=font, fill=0)
        mm += 1

    # Solid block in one corner makes any flip or mirror obvious.
    draw.rectangle([w - 1 - pad - 14, h - 1 - pad - 14, w - 1 - pad, h - 1 - pad], fill=0)

    letter_font = _load_font(True, config.TEXT_FONT_MAX_SIZE_PX)
    draw.text((pad + 3, h - 1 - pad - 26), label_letter, font=letter_font, fill=0)

    return half


def render_calibration_page() -> Image.Image:
    """Alignment/debug page: printable boundary, centre line, mm grid, labels A/B.

    Each half is built and rotated exactly like a real sticker (see
    render_pair_page) so the calibration page exercises the same geometry
    a real print job would. Does not consume permanent IDs (spec section 14)
    — call this directly, never via the normal label-allocation path.
    """
    half_a = rotate_for_printer(_render_calibration_half("A"))
    half_b = rotate_for_printer(_render_calibration_half("B"))
    return compose_two_up_page(half_a, half_b, debug=True)
