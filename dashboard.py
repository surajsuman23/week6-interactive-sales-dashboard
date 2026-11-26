"""
Week 6 - Interactive Sales Dashboard (Seaborn + Plotly)

Builds a sales & customer dashboard from two datasets:
  - data/sales_data.csv      : 100 orders (Jan-Apr 2024) -> sales trends, product & region performance
  - data/customer_churn.csv  : 500 customers             -> customer segmentation & churn

Outputs (all paths are relative to this file, so the project runs on any machine):
  visualizations/*.png              static Seaborn charts + 2x2 dashboard grid
  visualizations/interactive/*.html interactive Plotly charts (hover, dropdowns, animation)
  report/dashboard_report.pdf       multi-page PDF report
  dashboard_demo.gif                slideshow walkthrough of the dashboard panels

Run:
    python dashboard.py
"""

import argparse
import logging
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # render to files, no display needed
import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
import seaborn as sns
from matplotlib.backends.backend_pdf import PdfPages
from PIL import Image
from plotly.subplots import make_subplots

BASE_DIR = Path(__file__).resolve().parent

SALES_COLUMNS = ["Date", "Product", "Quantity", "Price", "Customer_ID", "Region", "Total_Sales"]
CHURN_COLUMNS = [
    "CustomerID", "Tenure", "MonthlyCharges", "TotalCharges", "Contract",
    "PaymentMethod", "PaperlessBilling", "SeniorCitizen", "Churn",
]
CONTRACT_ORDER = ["Month-to-month", "One year", "Two year"]
TENURE_BINS = [0, 12, 24, 48, 72]
TENURE_LABELS = ["0-12 m", "13-24 m", "25-48 m", "49-72 m"]

# ---- Theme: one palette used by every chart (Seaborn and Plotly) ----------
PALETTE = ["#1B4F72", "#2E86C1", "#F39C12", "#C0392B", "#27AE60", "#7D3C98"]
CHURN_COLORS = {"Stayed": "#2E86C1", "Churned": "#C0392B"}
ACCENT = "#F39C12"
FONT = "DejaVu Sans"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logging.getLogger("matplotlib").setLevel(logging.WARNING)
log = logging.getLogger("dashboard")


class DataValidationError(Exception):
    """Raised when an input file cannot be used."""


def apply_theme():
    """Set one consistent Seaborn/Matplotlib style for all static charts."""
    sns.set_theme(
        style="whitegrid",
        palette=PALETTE,
        font=FONT,
        rc={
            "axes.titleweight": "bold",
            "axes.titlesize": 13,
            "axes.labelsize": 11,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "figure.dpi": 100,
        },
    )


def color_map(values):
    """Give every category a fixed palette colour, so e.g. 'Laptop' looks the same in every chart."""
    return {v: PALETTE[i % len(PALETTE)] for i, v in enumerate(sorted(set(values)))}


def rupees_lakh(x, _pos=None):
    """Axis formatter: 250000 -> ₹2.5L (Indian lakh notation)."""
    return f"₹{x / 1e5:.1f}L"


# --------------------------------------------------------------------------- #
# 1. LOAD & CLEAN
# --------------------------------------------------------------------------- #
def _read_csv(path, required):
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError as exc:
        raise DataValidationError(f"Data file is empty: {path}") from exc
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise DataValidationError(f"{path.name}: missing required columns {missing}")
    if df.empty:
        raise DataValidationError(f"{path.name}: has headers but no rows")
    return df


def load_sales(path):
    """Load and clean the order-level sales data."""
    df = _read_csv(path, SALES_COLUMNS).copy()
    rows_in = len(df)

    for col in ["Product", "Region", "Customer_ID"]:
        df[col] = df[col].astype("string").str.strip()
    df["Product"] = df["Product"].str.title()
    df["Region"] = df["Region"].str.title()
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for col in ["Quantity", "Price", "Total_Sales"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.drop_duplicates()
    df = df.dropna(subset=["Date", "Product", "Region", "Quantity", "Price"])
    df = df[(df["Quantity"] > 0) & (df["Price"] > 0)].copy()

    # Total_Sales must equal Quantity x Price; recompute where missing/wrong
    expected = df["Quantity"] * df["Price"]
    df["Total_Sales"] = df["Total_Sales"].where(df["Total_Sales"] == expected, expected)

    if df.empty:
        raise DataValidationError("sales data: no valid rows after cleaning")

    df["Quantity"] = df["Quantity"].astype(int)
    df["Month"] = df["Date"].dt.to_period("M").astype(str)
    df = df.sort_values("Date").reset_index(drop=True)
    log.info("Sales: %d rows in, %d rows after cleaning", rows_in, len(df))
    return df


def load_churn(path):
    """Load and clean customer data; add segment columns used by the dashboard."""
    df = _read_csv(path, CHURN_COLUMNS).copy()
    rows_in = len(df)

    for col in ["CustomerID", "Contract", "PaymentMethod", "PaperlessBilling"]:
        df[col] = df[col].astype("string").str.strip()
    for col in ["Tenure", "MonthlyCharges", "TotalCharges", "SeniorCitizen", "Churn"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.drop_duplicates(subset="CustomerID")
    df = df.dropna(subset=["Tenure", "MonthlyCharges", "Contract", "Churn"])
    df = df[df["Churn"].isin([0, 1]) & (df["Tenure"] >= 0)].copy()
    if df.empty:
        raise DataValidationError("churn data: no valid rows after cleaning")

    df["Churn"] = df["Churn"].astype(int)
    df["Churn_Label"] = df["Churn"].map({0: "Stayed", 1: "Churned"})
    df["Senior"] = df["SeniorCitizen"].map({0: "Non-senior", 1: "Senior"})
    df["Tenure_Group"] = pd.cut(df["Tenure"], bins=TENURE_BINS, labels=TENURE_LABELS)
    df["Contract"] = pd.Categorical(df["Contract"], categories=CONTRACT_ORDER, ordered=True)
    log.info("Customers: %d rows in, %d rows after cleaning", rows_in, len(df))
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 2. KPIs
# --------------------------------------------------------------------------- #
def compute_kpis(sales, churn):
    by_product = sales.groupby("Product")["Total_Sales"].sum().sort_values(ascending=False)
    by_region = sales.groupby("Region")["Total_Sales"].sum().sort_values(ascending=False)
    churn_by_contract = churn.groupby("Contract", observed=True)["Churn"].mean()
    churn_by_tenure = churn.groupby("Tenure_Group", observed=True)["Churn"].mean()
    return {
        "total_revenue": int(sales["Total_Sales"].sum()),
        "orders": int(len(sales)),
        "avg_order_value": float(sales["Total_Sales"].mean()),
        "top_product": by_product.index[0],
        "top_product_share": float(by_product.iloc[0] / by_product.sum() * 100),
        "top_region": by_region.index[0],
        "customers": int(len(churn)),
        "churn_rate": float(churn["Churn"].mean() * 100),
        "churn_month_to_month": float(churn_by_contract["Month-to-month"] * 100),
        "churn_one_year": float(churn_by_contract["One year"] * 100),
        "churn_two_year": float(churn_by_contract["Two year"] * 100),
        "churn_first_year": float(churn_by_tenure.iloc[0] * 100),
        "max_tenure_churned": int(churn.loc[churn["Churn"] == 1, "Tenure"].max()),
        "corr_tenure_churn": float(churn["Tenure"].corr(churn["Churn"])),
        "corr_tenure_totalcharges": float(churn["Tenure"].corr(churn["TotalCharges"])),
        "avg_monthly_churned": float(churn.loc[churn["Churn"] == 1, "MonthlyCharges"].mean()),
        "avg_monthly_stayed": float(churn.loc[churn["Churn"] == 0, "MonthlyCharges"].mean()),
    }


# --------------------------------------------------------------------------- #
# 3. STATIC SEABORN CHARTS
# --------------------------------------------------------------------------- #
def _save(fig, out_dir, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    log.info("Saved %s", path.relative_to(out_dir.parent))
    return path


def _annotate_medians(ax, data, x, y, order):
    """Write the median value above each box/violin and mark the mean with a diamond."""
    stats = data.groupby(x, observed=True)[y].agg(["median", "mean"]).reindex(order)
    top = data[y].max()
    for i, (median, mean) in enumerate(zip(stats["median"], stats["mean"])):
        ax.scatter(i, mean, marker="D", color="white", edgecolor="black", s=45, zorder=5)
        ax.text(i, top * 1.04, f"median\n{rupees_lakh(median)}", ha="center", va="bottom", fontsize=8.5)
    ax.set_ylim(0, top * 1.22)


def chart_box_by_product(sales, out_dir):
    """Box plot: spread of order values per product, with mean (◆) and median labels."""
    order = sales.groupby("Product")["Total_Sales"].median().sort_values(ascending=False).index.tolist()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sns.boxplot(data=sales, x="Product", y="Total_Sales", order=order, hue="Product",
                palette=color_map(sales["Product"]), legend=False, width=0.55, ax=ax)
    sns.stripplot(data=sales, x="Product", y="Total_Sales", order=order, color="black",
                  alpha=0.35, size=3.5, jitter=0.15, ax=ax)
    _annotate_medians(ax, sales, "Product", "Total_Sales", order)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(rupees_lakh))
    ax.set(title="Order Value Distribution by Product  (◆ = mean)", xlabel="Product", ylabel="Order value")
    return _save(fig, out_dir, "01_box_order_value_by_product.png")


def chart_violin_by_region(sales, out_dir):
    """Violin plot: shape of the order-value distribution per region."""
    order = sales.groupby("Region")["Total_Sales"].sum().sort_values(ascending=False).index.tolist()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sns.violinplot(data=sales, x="Region", y="Total_Sales", order=order, hue="Region",
                   palette=color_map(sales["Region"]), legend=False, inner="quartile", cut=0, ax=ax)
    _annotate_medians(ax, sales, "Region", "Total_Sales", order)
    counts = sales["Region"].value_counts()
    ax.set_xticks(range(len(order)), [f"{r}\n(n={counts[r]})" for r in order])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(rupees_lakh))
    ax.set(title="Order Value Distribution by Region  (◆ = mean)", xlabel="Region", ylabel="Order value")
    return _save(fig, out_dir, "02_violin_order_value_by_region.png")


def chart_correlation_heatmap(churn, out_dir):
    """Heatmap: Pearson correlations between numeric customer variables."""
    cols = ["Tenure", "MonthlyCharges", "TotalCharges", "SeniorCitizen", "Churn"]
    corr = churn[cols].corr()
    fig, ax = plt.subplots(figsize=(7.5, 6))
    sns.heatmap(corr, annot=True, fmt=".2f", cmap="RdBu_r", vmin=-1, vmax=1, center=0,
                square=True, linewidths=0.5, cbar_kws={"label": "Pearson r"}, ax=ax)
    ax.set_title("Customer Metrics - Correlation Matrix")
    return _save(fig, out_dir, "03_heatmap_customer_correlation.png")


def chart_churn_by_contract(churn, out_dir):
    """Bar chart: churn rate per contract type, split by senior status (95% CI bars)."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sns.barplot(data=churn, x="Contract", y="Churn", hue="Senior", order=CONTRACT_ORDER,
                palette=[PALETTE[1], PALETTE[2]], errorbar=("ci", 95), capsize=0.08, ax=ax)
    overall = churn.groupby("Contract", observed=True)["Churn"].mean().reindex(CONTRACT_ORDER)
    counts = churn["Contract"].value_counts().reindex(CONTRACT_ORDER)
    ax.set_xticks(range(len(CONTRACT_ORDER)),
                  [f"{c}\nall customers: {r:.1%} (n={n})" for c, r, n in zip(CONTRACT_ORDER, overall, counts)])
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0%}"))
    ax.set_ylim(0, None)
    ax.set(title="Churn Rate by Contract Type (bars = 95% CI)", xlabel="", ylabel="Churn rate")
    ax.legend(title="")
    return _save(fig, out_dir, "04_bar_churn_by_contract.png")


def chart_tenure_histogram(churn, out_dir):
    """Histogram + KDE: tenure of customers who stayed vs churned."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sns.histplot(data=churn, x="Tenure", hue="Churn_Label", hue_order=["Stayed", "Churned"],
                 palette=CHURN_COLORS, bins=24, kde=True, element="step", stat="count", ax=ax)
    ax.axvline(12, color="#555", linestyle="--", linewidth=1)
    ax.text(12.5, ax.get_ylim()[1] * 0.92, "12 months", fontsize=9, color="#555")
    ax.set(title="Customer Tenure: Stayed vs Churned", xlabel="Tenure (months)", ylabel="Customers")
    ax.get_legend().set_title("")
    return _save(fig, out_dir, "05_hist_tenure_by_churn.png")


def chart_scatter_regression(churn, out_dir):
    """Scatter + regression line: monthly charges vs tenure, coloured by churn."""
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sns.scatterplot(data=churn, x="Tenure", y="MonthlyCharges", hue="Churn_Label",
                    hue_order=["Stayed", "Churned"], palette=CHURN_COLORS, alpha=0.7, s=28, ax=ax)
    sns.regplot(data=churn, x="Tenure", y="MonthlyCharges", scatter=False,
                color=ACCENT, line_kws={"linewidth": 2}, ax=ax)
    r = churn["Tenure"].corr(churn["MonthlyCharges"])
    ax.set(title=f"Monthly Charges vs Tenure (trend line, r = {r:.2f})",
           xlabel="Tenure (months)", ylabel="Monthly charges (₹)")
    ax.legend(title="", loc="upper left", bbox_to_anchor=(1.01, 1), frameon=False)
    return _save(fig, out_dir, "06_scatter_charges_vs_tenure.png")


def chart_monthly_trend(sales, out_dir):
    """Line chart: monthly revenue by product."""
    monthly = sales.groupby(["Month", "Product"], as_index=False)["Total_Sales"].sum()
    fig, ax = plt.subplots(figsize=(9, 5.5))
    sns.lineplot(data=monthly, x="Month", y="Total_Sales", hue="Product", marker="o",
                 palette=color_map(sales["Product"]), linewidth=2, ax=ax)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(rupees_lakh))
    ax.set(title="Monthly Revenue by Product (Apr 2024 = 9 days only; no Phone orders in Apr)",
           xlabel="Month", ylabel="Revenue")
    ax.legend(title="", ncol=3, fontsize=9)
    return _save(fig, out_dir, "07_line_monthly_revenue_by_product.png")


def chart_pairplot(churn, out_dir):
    """Pair plot: every pair of numeric customer variables, coloured by churn."""
    grid = sns.pairplot(churn, vars=["Tenure", "MonthlyCharges", "TotalCharges"], hue="Churn_Label",
                        hue_order=["Stayed", "Churned"], palette=CHURN_COLORS, corner=True,
                        plot_kws={"alpha": 0.6, "s": 18}, height=2.6)
    grid.figure.suptitle("Customer Variables - Pair Plot", fontweight="bold", y=1.02)
    grid._legend.set_title("")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "08_pairplot_customers.png"
    grid.figure.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(grid.figure)
    log.info("Saved %s", path.relative_to(out_dir.parent))
    return path


def chart_dashboard_grid(sales, churn, kpis, out_dir):
    """2x2 dashboard: sales trend, product performance, customer segments, churn drivers."""
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle(
        f"Sales & Customer Dashboard  |  Revenue {rupees_lakh(kpis['total_revenue'])}  ·  "
        f"{kpis['orders']} orders  ·  {kpis['customers']} customers  ·  churn {kpis['churn_rate']:.1f}%",
        fontsize=15, fontweight="bold",
    )

    # (1) Sales trend
    ax = axes[0, 0]
    monthly = sales.groupby("Month")["Total_Sales"].sum()
    sns.barplot(x=monthly.index, y=monthly.values, color=PALETTE[1], ax=ax)
    ax.plot(range(len(monthly)), monthly.values, color=ACCENT, marker="o", linewidth=2)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(rupees_lakh))
    ax.set(title="Sales Trend - Revenue by Month", xlabel="", ylabel="Revenue")

    # (2) Product performance
    ax = axes[0, 1]
    order = sales.groupby("Product")["Total_Sales"].median().sort_values(ascending=False).index
    sns.boxplot(data=sales, x="Product", y="Total_Sales", order=order, hue="Product",
                palette=color_map(sales["Product"]), legend=False, width=0.55, ax=ax)
    means = sales.groupby("Product")["Total_Sales"].mean().reindex(order)
    ax.scatter(range(len(order)), means, marker="D", color="white", edgecolor="black", zorder=5)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(rupees_lakh))
    ax.set(title="Product Performance - Order Value (◆ = mean)", xlabel="", ylabel="Order value")

    # (3) Customer segmentation
    ax = axes[1, 0]
    seg = churn.pivot_table(index="Contract", columns="Tenure_Group", values="CustomerID",
                            aggfunc="count", observed=True)
    sns.heatmap(seg, annot=True, fmt="d", cmap="Blues", cbar_kws={"label": "Customers"}, ax=ax)
    ax.set(title="Customer Segments - Contract × Tenure", xlabel="Tenure", ylabel="")

    # (4) Churn drivers
    ax = axes[1, 1]
    rate = churn.groupby("Tenure_Group", observed=True)["Churn"].mean().mul(100)
    sns.barplot(x=rate.index.astype(str), y=rate.values, color=CHURN_COLORS["Churned"], ax=ax)
    for i, v in enumerate(rate.values):
        ax.text(i, v + 1.5, f"{v:.0f}%", ha="center", fontweight="bold")
    ax.set_ylim(0, max(rate.max() * 1.2, 10))
    ax.set(title="Churn Driver - Churn Rate by Tenure", xlabel="Tenure", ylabel="Churn rate (%)")

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "00_dashboard_2x2.png"
    fig.savefig(path, dpi=110)
    plt.close(fig)
    log.info("Saved %s", path.relative_to(out_dir.parent))
    return path


def create_static_charts(sales, churn, kpis, out_dir):
    apply_theme()
    return [
        chart_dashboard_grid(sales, churn, kpis, out_dir),
        chart_box_by_product(sales, out_dir),
        chart_violin_by_region(sales, out_dir),
        chart_correlation_heatmap(churn, out_dir),
        chart_churn_by_contract(churn, out_dir),
        chart_tenure_histogram(churn, out_dir),
        chart_scatter_regression(churn, out_dir),
        chart_monthly_trend(sales, out_dir),
        chart_pairplot(churn, out_dir),
    ]


# --------------------------------------------------------------------------- #
# 4. INTERACTIVE PLOTLY CHARTS
# --------------------------------------------------------------------------- #
PLOTLY_LAYOUT = dict(
    template="plotly_white",
    font=dict(family="Arial", size=13),
    colorway=PALETTE,
    hoverlabel=dict(bgcolor="white"),
    margin=dict(t=90, l=60, r=30, b=60),
)


def _write_html(fig, out_dir, name):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    # include_plotlyjs="cdn" keeps files small; an internet connection is needed to view them
    fig.write_html(path, include_plotlyjs="cdn", full_html=True)
    log.info("Saved %s", path.relative_to(out_dir.parent.parent))
    return path


def interactive_sales_trend(sales, out_dir):
    """Line chart with a dropdown to switch metric (revenue / orders / units)."""
    monthly = sales.groupby(["Month", "Product"]).agg(
        Revenue=("Total_Sales", "sum"), Orders=("Total_Sales", "size"), Units=("Quantity", "sum")
    ).reset_index()
    products = sorted(monthly["Product"].unique())
    metrics = ["Revenue", "Orders", "Units"]

    colors = color_map(products)
    fig = go.Figure()
    for metric in metrics:
        for product in products:
            d = monthly[monthly["Product"] == product]
            fig.add_trace(go.Scatter(
                x=d["Month"], y=d[metric], mode="lines+markers", name=product,
                visible=(metric == "Revenue"), line=dict(color=colors[product], width=3),
                hovertemplate=f"<b>{product}</b><br>Month: %{{x}}<br>{metric}: %{{y:,}}<extra></extra>",
            ))

    buttons = []
    n = len(products)
    for i, metric in enumerate(metrics):
        visible = [False] * (n * len(metrics))
        visible[i * n:(i + 1) * n] = [True] * n
        buttons.append(dict(label=metric, method="update",
                            args=[{"visible": visible},
                                  {"title": f"Monthly {metric} by Product", "yaxis": {"title": metric}}]))
    fig.update_layout(
        **PLOTLY_LAYOUT,
        title="Monthly Revenue by Product",
        xaxis_title="Month", yaxis_title="Revenue",
        updatemenus=[dict(buttons=buttons, direction="down", x=1.0, xanchor="right", y=1.15, yanchor="top")],
        annotations=[dict(text="Metric:", x=0.83, xref="paper", y=1.12, yref="paper", showarrow=False)],
    )
    return _write_html(fig, out_dir, "sales_trend_dropdown.html")


def interactive_product_race(sales, out_dir):
    """Animated bar chart: cumulative revenue by product, one frame per month."""
    months = sorted(sales["Month"].unique())
    products = sorted(sales["Product"].unique())
    cum = (
        sales.groupby(["Month", "Product"])["Total_Sales"].sum()
        .unstack(fill_value=0).reindex(index=months, columns=products, fill_value=0).cumsum()
    )
    colors = color_map(products)

    def bar(month):
        values = cum.loc[month]
        return go.Bar(x=products, y=values.values, marker_color=[colors[p] for p in products],
                      text=[f"₹{v / 1e5:.1f}L" for v in values.values], textposition="outside",
                      hovertemplate="<b>%{x}</b><br>Cumulative revenue: ₹%{y:,.0f}<extra></extra>")

    fig = go.Figure(data=[bar(months[0])], frames=[go.Frame(data=[bar(m)], name=m) for m in months])
    fig.update_layout(
        **PLOTLY_LAYOUT,
        title="Cumulative Revenue by Product (press ▶ Play)",
        yaxis=dict(title="Cumulative revenue (₹)", range=[0, cum.values.max() * 1.15]),
        xaxis_title="Product",
        updatemenus=[dict(type="buttons", showactive=False, x=0.0, y=1.12, xanchor="left",
                          buttons=[dict(label="▶ Play", method="animate",
                                        args=[None, {"frame": {"duration": 900, "redraw": True},
                                                     "fromcurrent": True, "transition": {"duration": 400}}]),
                                   dict(label="❚❚ Pause", method="animate",
                                        args=[[None], {"frame": {"duration": 0}, "mode": "immediate"}])])],
        sliders=[dict(active=0, currentvalue={"prefix": "Up to month: "},
                      steps=[dict(label=m, method="animate",
                                  args=[[m], {"frame": {"duration": 0, "redraw": True}, "mode": "immediate"}])
                             for m in months])],
    )
    return _write_html(fig, out_dir, "product_revenue_animation.html")


def interactive_customer_segments(churn, out_dir):
    """Scatter of tenure vs monthly charges with a contract-type dropdown filter."""
    fig = go.Figure()
    groups = ["All"] + CONTRACT_ORDER
    for g in groups:
        sub = churn if g == "All" else churn[churn["Contract"] == g]
        for label in ["Stayed", "Churned"]:
            d = sub[sub["Churn_Label"] == label]
            fig.add_trace(go.Scatter(
                x=d["Tenure"], y=d["MonthlyCharges"], mode="markers", name=label,
                visible=(g == "All"), marker=dict(color=CHURN_COLORS[label], size=8, opacity=0.7),
                customdata=d[["CustomerID", "Contract", "PaymentMethod"]].astype(str).values,
                hovertemplate=("<b>%{customdata[0]}</b><br>Tenure: %{x} months<br>"
                               "Monthly: ₹%{y}<br>Contract: %{customdata[1]}<br>"
                               "Payment: %{customdata[2]}<extra>" + label + "</extra>"),
            ))
    buttons = []
    for i, g in enumerate(groups):
        visible = [False] * (2 * len(groups))
        visible[2 * i] = visible[2 * i + 1] = True
        sub = churn if g == "All" else churn[churn["Contract"] == g]
        buttons.append(dict(label=g, method="update",
                            args=[{"visible": visible},
                                  {"title": f"Customer Segments - {g} (n={len(sub)}, churn {sub['Churn'].mean():.1%})"}]))
    fig.update_layout(
        **PLOTLY_LAYOUT,
        title=f"Customer Segments - All (n={len(churn)}, churn {churn['Churn'].mean():.1%})",
        xaxis_title="Tenure (months)", yaxis_title="Monthly charges (₹)",
        updatemenus=[dict(buttons=buttons, direction="down", x=1.0, xanchor="right", y=1.15, yanchor="top")],
    )
    return _write_html(fig, out_dir, "customer_segments_dropdown.html")


def interactive_dashboard(sales, churn, kpis, out_dir):
    """Combined 2x2 interactive dashboard with a region dropdown for the sales panels."""
    fig = make_subplots(
        rows=2, cols=2, vertical_spacing=0.16, horizontal_spacing=0.1,
        subplot_titles=("Revenue by Month", "Revenue by Product",
                        "Churn Rate by Contract", "Tenure vs Monthly Charges"),
    )
    regions = ["All regions"] + sorted(sales["Region"].unique())
    products = sorted(sales["Product"].unique())
    sales_traces_per_region = 2

    for r in regions:
        d = sales if r == "All regions" else sales[sales["Region"] == r]
        monthly = d.groupby("Month")["Total_Sales"].sum()
        prod = d.groupby("Product")["Total_Sales"].sum().reindex(products, fill_value=0)
        show = r == "All regions"
        fig.add_trace(go.Scatter(x=monthly.index, y=monthly.values, mode="lines+markers", name="Revenue",
                                 line=dict(color=PALETTE[1], width=3), visible=show, showlegend=False,
                                 hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>"), row=1, col=1)
        fig.add_trace(go.Bar(x=products, y=prod.values, marker_color=[color_map(products)[p] for p in products],
                             visible=show,
                             showlegend=False, hovertemplate="%{x}<br>₹%{y:,.0f}<extra></extra>"),
                      row=1, col=2)

    rate = churn.groupby("Contract", observed=True)["Churn"].mean().reindex(CONTRACT_ORDER) * 100
    fig.add_trace(go.Bar(x=CONTRACT_ORDER, y=rate.values, marker_color=CHURN_COLORS["Churned"],
                         text=[f"{v:.1f}%" for v in rate.values], textposition="outside", showlegend=False,
                         hovertemplate="%{x}<br>Churn %{y:.1f}%<extra></extra>"), row=2, col=1)
    for label in ["Stayed", "Churned"]:
        d = churn[churn["Churn_Label"] == label]
        fig.add_trace(go.Scatter(x=d["Tenure"], y=d["MonthlyCharges"], mode="markers", name=label,
                                 marker=dict(color=CHURN_COLORS[label], size=6, opacity=0.65),
                                 customdata=d["CustomerID"],
                                 hovertemplate="%{customdata}<br>Tenure %{x}m · ₹%{y}/month<extra>"
                                 + label + "</extra>"), row=2, col=2)

    n_sales = len(regions) * sales_traces_per_region
    buttons = []
    for i, r in enumerate(regions):
        visible = [False] * n_sales + [True] * 3
        visible[i * 2] = visible[i * 2 + 1] = True
        buttons.append(dict(label=r, method="update", args=[{"visible": visible}]))

    fig.update_layout(
        **{**PLOTLY_LAYOUT, "margin": dict(t=140, l=60, r=30, b=60)},
        height=850,
        title=dict(text=(f"<b>Interactive Sales & Customer Dashboard</b><br><sup>Revenue ₹{kpis['total_revenue']:,} · "
                         f"{kpis['orders']} orders · {kpis['customers']} customers · churn {kpis['churn_rate']:.1f}%"
                         "  |  Region filter applies to the top two panels</sup>")),
        updatemenus=[dict(buttons=buttons, direction="down", x=1.0, xanchor="right", y=1.1, yanchor="top")],
        legend=dict(orientation="h", x=0.75, y=0.43),
    )
    fig.update_yaxes(title_text="Revenue (₹)", row=1, col=1)
    fig.update_yaxes(title_text="Revenue (₹)", row=1, col=2)
    fig.update_yaxes(title_text="Churn rate (%)", range=[0, rate.max() * 1.3], row=2, col=1)
    fig.update_yaxes(title_text="Monthly charges (₹)", row=2, col=2)
    fig.update_xaxes(title_text="Tenure (months)", row=2, col=2)
    return _write_html(fig, out_dir, "interactive_dashboard.html")


def create_interactive_charts(sales, churn, kpis, out_dir):
    return [
        interactive_dashboard(sales, churn, kpis, out_dir),
        interactive_sales_trend(sales, out_dir),
        interactive_product_race(sales, out_dir),
        interactive_customer_segments(churn, out_dir),
    ]


# --------------------------------------------------------------------------- #
# 5. REPORT (PDF) + DEMO GIF
# --------------------------------------------------------------------------- #
def insights(kpis):
    return [
        f"{kpis['top_product']} is the top product ({kpis['top_product_share']:.1f}% of revenue); "
        f"{kpis['top_region']} is the top region.",
        f"Overall churn is {kpis['churn_rate']:.1f}% of {kpis['customers']} customers.",
        f"Month-to-month contracts churn at {kpis['churn_month_to_month']:.1f}% vs "
        f"{kpis['churn_one_year']:.1f}% (one year) and {kpis['churn_two_year']:.1f}% (two year).",
        f"All churn happens in the first year: {kpis['churn_first_year']:.0f}% of customers with "
        f"≤12 months tenure churned; no customer above {kpis['max_tenure_churned']} months churned.",
        f"Tenure has the strongest link to churn (r = {kpis['corr_tenure_churn']:.2f}).",
        f"Churned customers paid more per month on average (₹{kpis['avg_monthly_churned']:.0f} vs "
        f"₹{kpis['avg_monthly_stayed']:.0f}).",
        f"Data-quality note: TotalCharges is almost unrelated to tenure (r = {kpis['corr_tenure_totalcharges']:.2f}), "
        "which is unusual - treat TotalCharges with caution.",
    ]


def write_pdf_report(chart_paths, kpis, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(path) as pdf:
        fig = plt.figure(figsize=(11.69, 8.27))  # A4 landscape
        fig.text(0.06, 0.88, "Interactive Sales Dashboard - Report", fontsize=24, fontweight="bold")
        fig.text(0.06, 0.83, "Week 6: Data Visualization Mastery with Seaborn & Plotly", fontsize=13, color="#555")
        kpi_lines = [
            f"Total revenue: ₹{kpis['total_revenue']:,}",
            f"Orders: {kpis['orders']}   ·   Average order value: ₹{kpis['avg_order_value']:,.0f}",
            f"Customers: {kpis['customers']}   ·   Churn rate: {kpis['churn_rate']:.1f}%",
        ]
        for i, line in enumerate(kpi_lines):
            fig.text(0.06, 0.74 - i * 0.045, line, fontsize=13)
        fig.text(0.06, 0.56, "Key insights", fontsize=15, fontweight="bold")
        for i, line in enumerate(insights(kpis)):
            fig.text(0.07, 0.51 - i * 0.05, f"• {line}", fontsize=10.5, wrap=True)
        fig.text(0.06, 0.08, "Interactive versions: open visualizations/interactive/interactive_dashboard.html "
                 "in a web browser.", fontsize=10, color="#555")
        pdf.savefig(fig)
        plt.close(fig)

        for chart in chart_paths:
            img = plt.imread(chart)
            h, w = img.shape[:2]
            fig = plt.figure(figsize=(11.69, 8.27))
            ax = fig.add_axes([0.03, 0.03, 0.94, 0.94])
            ax.imshow(img)
            ax.set_aspect("equal")
            ax.axis("off")
            pdf.savefig(fig)
            plt.close(fig)
    log.info("Saved %s", path.name)
    return path


def make_demo_gif(chart_paths, path, width=1000, seconds_per_frame=1.5):
    """Slideshow GIF of the dashboard panels (static frames, not a screen recording)."""
    frames = []
    for chart in chart_paths:
        img = Image.open(chart).convert("RGB")
        img = img.resize((width, int(img.height * width / img.width)), Image.LANCZOS)
        canvas = Image.new("RGB", (width, 700), "white")
        img.thumbnail((width, 700), Image.LANCZOS)
        canvas.paste(img, ((width - img.width) // 2, (700 - img.height) // 2))
        frames.append(canvas.quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    frames[0].save(path, save_all=True, append_images=frames[1:],
                   duration=int(seconds_per_frame * 1000), loop=0, optimize=True)
    log.info("Saved %s (%d frames)", path.name, len(frames))
    return path


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #
def print_summary(kpis):
    print("\n" + "=" * 60)
    print("        INTERACTIVE SALES DASHBOARD - SUMMARY")
    print("=" * 60)
    print(f"Total revenue        : ₹{kpis['total_revenue']:,}")
    print(f"Orders               : {kpis['orders']}")
    print(f"Avg order value      : ₹{kpis['avg_order_value']:,.0f}")
    print(f"Customers            : {kpis['customers']}")
    print(f"Churn rate           : {kpis['churn_rate']:.1f}%")
    print("-" * 60)
    for i, line in enumerate(insights(kpis), 1):
        print(f" {i}. {line}")
    print("=" * 60 + "\n")


def run(sales_path, churn_path, out_root):
    out_root = Path(out_root)
    sales = load_sales(sales_path)
    churn = load_churn(churn_path)
    kpis = compute_kpis(sales, churn)
    static = create_static_charts(sales, churn, kpis, out_root / "visualizations")
    interactive = create_interactive_charts(sales, churn, kpis, out_root / "visualizations" / "interactive")
    report = write_pdf_report(static, kpis, out_root / "report" / "dashboard_report.pdf")
    gif = make_demo_gif(static, out_root / "dashboard_demo.gif")
    print_summary(kpis)
    return {"sales": sales, "churn": churn, "kpis": kpis, "static": static,
            "interactive": interactive, "report": report, "gif": gif}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build the interactive sales dashboard")
    p.add_argument("--sales", default=BASE_DIR / "data" / "sales_data.csv")
    p.add_argument("--churn", default=BASE_DIR / "data" / "customer_churn.csv")
    p.add_argument("--out", default=BASE_DIR, help="Folder for visualizations/, report/ and the GIF")
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    try:
        run(args.sales, args.churn, args.out)
    except (FileNotFoundError, DataValidationError) as err:
        log.error(err)
        sys.exit(1)
