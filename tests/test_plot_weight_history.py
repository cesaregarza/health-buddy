from datetime import date
from pathlib import Path
import pytest

from scripts.plot_weight_history import _date_ticks, _parser, render_svg


def test_chart_stops_at_as_of_and_labels_inferred_segment(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    (data / "measurements.csv").write_text(
        "measured_at_local,weight_lb\n"
        "2030-03-10T09:00:00,160.0\n"
        "2030-03-11T09:00:00,159.8\n"
        "2030-03-12T09:00:00,160.2\n"
        "2030-03-13T09:00:00,159.9\n"
        "2030-03-14T09:00:00,159.6\n"
        "2030-03-15T09:00:00,159.8\n"
        "2030-03-16T09:00:00,159.6\n"
        "2030-03-17T09:00:00,159.5\n",
        encoding="utf-8",
    )
    (data / "medication_events.csv").write_text(
        "event_date,medication,event_type,injection_number\n"
        "2030-03-10,Synthetic chart selection,dose_taken,3\n",
        encoding="utf-8",
    )
    (data / "goals.csv").write_text(
        "goal_id,metric,direction,target_value,unit,status,priority,created_on,"
        "target_date,source,notes\n",
        encoding="utf-8",
    )

    svg = render_svg(tmp_path, date(2030, 3, 16), medication="Synthetic chart selection")

    assert "Back-projected before first measurement" in svg
    assert "Feb 24" in svg
    assert "Mar 16" in svg
    assert "Mar 17" not in svg
    assert "no forward projection" in svg
    assert "The treatment-start value is estimated" in svg


def test_treatment_chart_requires_explicit_medication_selection(tmp_path):
    with pytest.raises(ValueError, match="select a recorded medication"):
        render_svg(tmp_path, None, medication="")
    with pytest.raises(SystemExit):
        _parser().parse_args(["--output", str(tmp_path / "plot.svg")])


def test_chart_ticks_follow_supplied_window():
    start, end = date(2030, 2, 1), date(2030, 3, 1)
    ticks = _date_ticks(start, end)
    assert ticks[0] == start
    assert ticks[-1] == end
    assert all(start <= tick <= end for tick in ticks)
