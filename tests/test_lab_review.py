"""Longitudinal calculations use fabricated inputs written per test."""
from scripts.lab_review import build_review_data
from tests.synthetic_workspace import csv_file


def test_longitudinal_values_fasting_and_derived_non_hdl(tmp_path):
    fields=["collected_on","test_name","value","unit","flag","reference_low","reference_high","fasting"]
    rows=[]
    for day,values,fasting in [("2030-01-01",{"cholesterol":180,"hdl cholesterol":50,"calc ldl chol":100,"hemoglobin a1c":5.5},""),("2030-02-01",{"cholesterol":160,"hdl cholesterol":55,"calc ldl chol":85,"hemoglobin a1c":5.1},"yes")]:
        for name,value in values.items():
            rows.append(dict(collected_on=day,test_name=name,value=value,unit="synthetic-unit",flag="",reference_low="",reference_high="",fasting=fasting))
    review=build_review_data(csv_file(tmp_path/"labs.csv",fields,rows))
    assert review["metadata"]["panel_dates"] == ["2030-01-01","2030-02-01"]
    assert review["summary"][0]["ldl_change_mg_dl"] == -15
    assert review["summary"][0]["latest_a1c_percent"] == 5.1
    assert [r["non_hdl_mg_dl"] for r in review["lipids"]] == [130,105]
    assert [r["fasting_status"] for r in review["glycemia"]] == ["Unknown","yes"]


def test_missing_values_do_not_become_zero(tmp_path):
    fields=["collected_on","test_name","value","unit","flag","reference_low","reference_high","fasting"]
    rows=[dict.fromkeys(fields,"") for _ in range(2)]
    rows[0].update(collected_on="2030-01-01",test_name="cholesterol",value="180")
    rows[1].update(collected_on="2030-01-01",test_name="hdl cholesterol",value="")
    review=build_review_data(csv_file(tmp_path/"labs.csv",fields,rows))
    assert review["lipids"][0]["non_hdl_mg_dl"] is None
    assert review["summary"][0]["ldl_change_mg_dl"] is None
    assert review["summary"][0]["ldl_change_fraction"] is None
