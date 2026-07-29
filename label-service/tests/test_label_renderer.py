from __future__ import annotations

import pytest

import config
from app.label_renderer import (
    LabelContent,
    build_qr_payload,
    render_blank_label,
    render_individual_label,
    render_pair_page,
    save_print_file,
)


def test_build_qr_payload_returns_label_id_unchanged():
    assert build_qr_payload("0042") == "0042"


def test_render_individual_label_with_max_length_sample_id_does_not_raise():
    record = LabelContent(
        label_id="0001",
        date="28/07/2026",
        sample_id="S" * config.MAX_SAMPLE_ID_LENGTH,
        experiment_id="E" * config.MAX_EXPERIMENT_ID_LENGTH,
        requestor_id="R" * config.MAX_REQUESTOR_ID_LENGTH,
    )
    img = render_individual_label(record)
    assert img.size == (config.INDIVIDUAL_LABEL_WIDTH_PX, config.INDIVIDUAL_LABEL_HEIGHT_PX)


def test_render_individual_label_accepts_three_sample_id_rows():
    record = LabelContent(
        label_id="0001",
        date="28/07/2026",
        sample_id="S001\nS002\nS003",
        experiment_id="EXP01",
        requestor_id="RA",
    )

    img = render_individual_label(record)

    assert img.size == (config.INDIVIDUAL_LABEL_WIDTH_PX, config.INDIVIDUAL_LABEL_HEIGHT_PX)


def test_render_blank_label_size_and_all_white():
    img = render_blank_label()
    assert img.size == (config.INDIVIDUAL_LABEL_WIDTH_PX, config.INDIVIDUAL_LABEL_HEIGHT_PX)
    extrema = img.getextrema()
    assert extrema == (255, 255)


def _sample_record(label_id="0001"):
    return LabelContent(
        label_id=label_id,
        date="28/07/2026",
        sample_id="S1",
        experiment_id="E1",
        requestor_id="RA",
    )


def test_render_pair_page_with_none_second_record_leaves_blank_half():
    page = render_pair_page(_sample_record(), None)

    # The page is now the printer's own fixed named-page size, not derived
    # from label/margin math (see label_renderer.compose_two_up_page).
    assert page.size == (config.PAGE_WIDTH_PX, config.PAGE_HEIGHT_PX)


def test_render_pair_page_does_not_crash_with_two_records():
    page = render_pair_page(_sample_record("0001"), _sample_record("0002"))
    assert page.size[0] > 0 and page.size[1] > 0


def test_save_print_file_embeds_configured_dpi(tmp_path):
    path = save_print_file(render_pair_page(_sample_record(), None), tmp_path / "label.png")

    from PIL import Image

    with Image.open(path) as saved:
        dpi_x, dpi_y = saved.info["dpi"]
    # PNG stores pixels/metre as an integer, so Pillow's round-trip value is
    # normally 299.9994 rather than exactly 300.
    assert dpi_x == pytest.approx(config.LABEL_DPI, abs=0.01)
    assert dpi_y == pytest.approx(config.LABEL_DPI, abs=0.01)


def _pair_up(records):
    pairs = []
    for i in range(0, len(records), 2):
        a = records[i]
        b = records[i + 1] if i + 1 < len(records) else None
        pairs.append((a, b))
    return pairs


def test_odd_sized_batch_pairing_leaves_last_position_blank():
    records = [_sample_record(f"000{i}") for i in range(1, 4)]  # 3 records, odd
    pairs = _pair_up(records)
    assert len(pairs) == 2
    assert pairs[0] == (records[0], records[1])
    assert pairs[1][0] == records[2]
    assert pairs[1][1] is None


def test_odd_sized_batch_pairing_matches_routes_pair_up():
    from app.routes import _pair_up as routes_pair_up

    records = [_sample_record(f"000{i}") for i in range(1, 6)]  # 5 records, odd
    ours = _pair_up(records)
    theirs = routes_pair_up(records)
    assert ours == theirs
    assert theirs[-1][1] is None
