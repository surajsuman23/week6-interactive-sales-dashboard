"""Tests for the dashboard pipeline. Run with:  python -m pytest -v"""

import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dashboard  # noqa: E402

SALES = ROOT / "data" / "sales_data.csv"
CHURN = ROOT / "data" / "customer_churn.csv"
SALES_HEADER = "Date,Product,Quantity,Price,Customer_ID,Region,Total_Sales\n"
CHURN_HEADER = ("CustomerID,Tenure,MonthlyCharges,TotalCharges,Contract,PaymentMethod,"
                "PaperlessBilling,SeniorCitizen,Churn\n")


@pytest.fixture(scope="module")
def sales():
    return dashboard.load_sales(SALES)


@pytest.fixture(scope="module")
def churn():
    return dashboard.load_churn(CHURN)


@pytest.fixture(scope="module")
def kpis(sales, churn):
    return dashboard.compute_kpis(sales, churn)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("build")
    return dashboard.run(SALES, CHURN, out)


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


# ---------- Loading & validation ----------
def test_load_shapes(sales, churn):
    assert len(sales) == 100
    assert len(churn) == 500


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        dashboard.load_sales("nope.csv")


def test_missing_columns(tmp_path):
    with pytest.raises(dashboard.DataValidationError, match="missing required columns"):
        dashboard.load_churn(write(tmp_path, "c.csv", "CustomerID,Tenure\nC1,5\n"))


def test_empty_file(tmp_path):
    with pytest.raises(dashboard.DataValidationError):
        dashboard.load_sales(write(tmp_path, "s.csv", ""))


def test_sales_cleaning(tmp_path):
    p = write(tmp_path, "s.csv", SALES_HEADER
              + "2024-01-01, phone ,2,100,C1, east ,999\n"   # wrong total -> recomputed, text fixed
              + "2024-01-01, phone ,2,100,C1, east ,999\n"   # duplicate
              + "bad-date,Laptop,1,50,C2,West,50\n"          # bad date -> dropped
              + "2024-01-03,Tablet,0,80,C3,North,0\n")       # zero qty -> dropped
    df = dashboard.load_sales(p)
    assert len(df) == 1
    assert df.loc[0, "Product"] == "Phone" and df.loc[0, "Region"] == "East"
    assert df.loc[0, "Total_Sales"] == 200


def test_churn_cleaning(tmp_path):
    p = write(tmp_path, "c.csv", CHURN_HEADER
              + "C1,5,100,500,Month-to-month,Credit Card,Yes,0,1\n"
              + "C1,5,100,500,Month-to-month,Credit Card,Yes,0,1\n"   # duplicate ID
              + "C2,30,80,2400,One year,Bank Transfer,No,1,7\n"       # invalid churn flag
              + "C3,abc,80,2400,Two year,Bank Transfer,No,1,0\n")     # bad tenure
    df = dashboard.load_churn(p)
    assert list(df["CustomerID"]) == ["C1"]
    assert df.loc[0, "Churn_Label"] == "Churned"
    assert str(df.loc[0, "Tenure_Group"]) == "0-12 m"


# ---------- KPIs ----------
def test_kpis(kpis):
    assert kpis["total_revenue"] == 12_365_048
    assert kpis["top_product"] == "Laptop"
    assert kpis["top_region"] == "North"
    assert kpis["churn_rate"] == pytest.approx(10.6)
    assert kpis["churn_month_to_month"] > kpis["churn_two_year"] > kpis["churn_one_year"]


def test_churn_only_in_first_year(kpis):
    assert kpis["max_tenure_churned"] <= 12
    assert kpis["corr_tenure_churn"] < -0.4


def test_color_map_is_stable():
    a = dashboard.color_map(["Phone", "Laptop", "Tablet"])
    b = dashboard.color_map(["Tablet", "Phone", "Laptop", "Phone"])
    assert a == b


# ---------- Outputs ----------
def test_static_charts(built):
    assert len(built["static"]) >= 9
    for png in built["static"]:
        assert png.exists() and png.stat().st_size > 20_000


def test_interactive_html_features(built):
    names = {p.name: p.read_text(encoding="utf-8") for p in built["interactive"]}
    assert len(names) == 4
    assert '"updatemenus"' in names["interactive_dashboard.html"]          # region dropdown
    assert '"updatemenus"' in names["sales_trend_dropdown.html"]           # metric dropdown
    assert "Plotly.addFrames" in names["product_revenue_animation.html"]   # animation frames
    assert '"animate"' in names["product_revenue_animation.html"]          # play button
    assert "hovertemplate" in names["customer_segments_dropdown.html"]     # custom hover


def test_pdf_and_gif(built):
    pdf = built["report"]
    assert pdf.read_bytes()[:4] == b"%PDF"
    gif = Image.open(built["gif"])
    assert gif.n_frames == len(built["static"])
    assert built["gif"].stat().st_size < 2_000_000
