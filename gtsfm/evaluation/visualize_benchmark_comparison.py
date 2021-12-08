"""
Use Plotly to create a dashboard that compares the metrics across all the benchmarks from the CI.

The dashboard is a heatmap representing a 2d table, with text annotations added to it.

Authors: John Lambert, Neha Upadhyay
"""

import argparse
import yaml
from collections import defaultdict
from pathlib import Path
from typing import List

import numpy as np
import plotly.graph_objects as go
from matplotlib import colors
from matplotlib.colors import LinearSegmentedColormap
from plotly.graph_objs.layout import Annotation, Font, Margin, XAxis, YAxis

import gtsfm.evaluation.merge_reports as report_utils
import gtsfm.evaluation.metrics_report as metrics_report
import gtsfm.utils.metrics as metrics_utils


HEATMAP_WIDTH = 1500
HEATMAP_HEIGHT = 900
NUM_COLORS_COLORMAP = 100
MAX_NUM_CHARS_ARTIFACT_FNAME = 35

MIN_RENDERABLE_PERCENT_CHANGE = -20
MAX_RENDERABLE_PERCENT_CHANGE = 20

DASHBOARD_HTML_SAVE_FPATH = Path(__file__).parent.parent.parent / "visual_comparison_dashboard.html"
BENCHMARK_YAML_FPATH = Path(__file__).parent.parent.parent / ".github" / "workflows" / "benchmark.yml"


TABLE_NAMES = [
    "Verifier Summary",
    "Inlier Support Processor Summary",
    "Rotation Cycle Consistency Metrics",
    "Cycle Consistent Frontend Summary",
    "Averaging Metrics",
    "Data Association Metrics",
    "Bundle Adjustment Metrics",
]

RED_HEX = "#df0101"
PALE_YELLOW_HEX = "#f5f6ce"
GREEN_HEX = "#31b404"


def colorscale_from_list(colorlist: List[str]) -> List[str]:
    """Create hex colorscale to interpolate between requested colors.

    Args:
        colorlist: requested colors.

    Returns:
        colorscale: list of length (NUM_COLORS_COLORMAP+1) representing a list of colors.
    """
    cmap = LinearSegmentedColormap.from_list(name="dummy_name", colors=colorlist)
    colorscale = [colors.rgb2hex(cmap(k * 1 / NUM_COLORS_COLORMAP)) for k in range(NUM_COLORS_COLORMAP + 1)]
    return colorscale


def plot_colored_table(new_values: np.ndarray, old_values: np.ndarray, row_labels: List[str], col_labels: List[str], tab_data: np.ndarray) -> str:
    """Create an annotated heatmap.

    Args:
        row_labels: labels for each column (column names) in the "x" direction.
        col_labels: labels for each row (row names) in the "y" direction.
        tab_data: 2d matrix, representing table data. Entries of the table represent percentage changes
            from a value for a metric on the master branch. Values can be considered in the "z" direction.

    Returns:
        string representing HTML code for the generated Plotly table.
    """
    if tab_data.size == 0:
        return ''
    # Clip "Z" to -20% and +20%. The clipping is only for the color -- the text will still display the correct numbers.
    tab_data_clipped = np.clip(tab_data, a_min=MIN_RENDERABLE_PERCENT_CHANGE, a_max=MAX_RENDERABLE_PERCENT_CHANGE)
    hovertext = list()
    for yi, yy in enumerate(col_labels):
        hovertext.append(list())
        for xi, xx in enumerate(row_labels):
            hovertext[-1].append('Master_val: {}<br />Branch_val: {}<br />Percentage: {}'.format(old_values[yi][xi],new_values[yi][xi], tab_data[yi][xi]))

    redgreen = [RED_HEX, PALE_YELLOW_HEX, GREEN_HEX]
    colorscale = colorscale_from_list(redgreen)
    trace = go.Heatmap(
        z=tab_data_clipped,
        x=row_labels,
        y=col_labels,
        colorscale=colorscale,
        hoverinfo='text',
        text=hovertext,
        zmin=-MIN_RENDERABLE_PERCENT_CHANGE,
        zmax=MAX_RENDERABLE_PERCENT_CHANGE,
    )

    layout = go.Layout(
        title="Percentage Change",
        font=Font(family="Balto, sans-serif", size=12, color="rgb(68,68,68)"),
        showlegend=False,
        xaxis=XAxis(title="", showgrid=True, side="top", tickangle=-45),
        yaxis=YAxis(
            title="",
            autorange="reversed",
            showgrid=True,
        ),
        autosize=False,
        height=HEATMAP_HEIGHT,
        width=HEATMAP_WIDTH,
        margin=Margin(l=135, r=40, b=85, t=170),
    )

    fig = go.Figure(data=go.Data([trace]), layout=layout)

    annotations = go.Annotations()

    num_rows, num_cols = tab_data.shape
    for i in range(num_rows):
        for j in range(num_cols):
            annotations.append(
                Annotation(
                    text=str(np.round(tab_data[i, j], 1)) + "%",
                    x=row_labels[j],
                    y=col_labels[i],
                    xref="x1",
                    yref="y1",
                    font=dict(color="rgb(25,25,25)"),
                    showarrow=False,
                )
            )
    fig["layout"].update(annotations=annotations)
    return fig.to_html(full_html=False, include_plotlyjs="cdn")


def generate_artifact_fnames_from_workflow(workflow_yaml_fpath: str) -> List[str]:
    """Auto-generate the expected filenames of CI artifact based on `benchmark.yaml' entries.
    
    The zip artifact names are auto-generated during CI runs from the YAML file, and by auto-generating
    them here, we can add additional benchmarks without needing to edit a hard-coded list. 

    Returns:
        artifact_fnames: file names of CI artifacts.
    """
    with open(workflow_yaml_fpath, "r") as stream:
        try:
            yaml_data = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)
            raise RuntimeError("YAML file could not be parsed safely.")

    benchmark_entries = yaml_data["jobs"]["benchmark"]["strategy"]["matrix"]["config_dataset_info"]

    # Note: CI converts "True" to "true", so we must force lower-case on the last string entry.
    artifact_fnames = [
        f"{e[0]}-{e[1]}-{e[2]}-{e[3]}-{e[4]}-{e[5]}-{e[6]}-{str(e[7]).lower()}.zip" for e in benchmark_entries
    ]
    return artifact_fnames


def generate_dashboard(curr_master_dirpath: str, new_branch_dirpath: str) -> None:
    """Generate a dashboard showing a visual representation of the diff against master on all benchmarks.

    This script expects to find the metrics in CI artifact files and saves to the main repo directory.
    TODO(johnwlambert): read metrics from JSON, instead of from the HTML report.

    Args:
        curr_master_dirpath: path to directory containing benchmark artifacts for the master branch.
        new_branch_dirpath: path to directory containing benchmark artifacts for a new branch.
    """
    zip_artifact_fnames = generate_artifact_fnames_from_workflow(workflow_yaml_fpath=BENCHMARK_YAML_FPATH)

    f = open(DASHBOARD_HTML_SAVE_FPATH, mode="w")

    # Write HTML headers.
    f.write("<!DOCTYPE html>" "<html>")
    f.write(metrics_report.get_html_header())

    # Loop over each table in the HTML report.
    for table_name in TABLE_NAMES:

        X = []
        Y = []

        benchmark_table_vals = defaultdict(dict)

        # Loop over each benchmark result (columns of table).
        for zip_fname in zip_artifact_fnames:
            # use just the first 35 chars
            X.append(zip_fname[:MAX_NUM_CHARS_ARTIFACT_FNAME])

            report1_fpath = f"{curr_master_dirpath}/results-{zip_fname}/result_metrics/gtsfm_metrics_report.html"
            tables_dict1 = report_utils.extract_tables_from_report(report1_fpath)

            report2_fpath = f"{new_branch_dirpath}/results-{zip_fname}/result_metrics/gtsfm_metrics_report.html"
            tables_dict2 = report_utils.extract_tables_from_report(report2_fpath)
            merged_tables_dict = report_utils.merge_tables(tables_dict1, tables_dict2)

            # Loop over each metric within this table (rows of table).
            for i, (metric_name, master_val, branch_val) in enumerate(merged_tables_dict[table_name]):

                if branch_val is None:
                    percentage_change = np.nan
                else:
                    percentage_change = metrics_utils.compute_percentage_change(float(master_val), float(branch_val))

                if "error" in metric_name and "outlier" not in metric_name:
                    # smaller is better, so this will flip the color to green for reduced values, instead of red
                    # exception are outlier errors, which we want to get larger.
                    percentage_change *= -1
                benchmark_table_vals[metric_name][zip_fname] = (round(float(master_val), 4), round(float(branch_val), 4),round(percentage_change,4))

        Z_rows = []
        new_values = []
        old_values = []
        for metric_name, benchmark_vals_dict in benchmark_table_vals.items():
            Z_row = []
            new_value = []
            old_value = []
            for zip_fname in zip_artifact_fnames:
                if benchmark_vals_dict.get(zip_fname, np.nan) is not np.nan:
                    Z_row.append(benchmark_vals_dict.get(zip_fname, np.nan)[2])
                    new_value.append(benchmark_vals_dict.get(zip_fname, np.nan)[1])
                    old_value.append(benchmark_vals_dict.get(zip_fname, np.nan)[0])
                else:
                    Z_row.append(benchmark_vals_dict.get(zip_fname, np.nan)) # default was unchanged if missing
                    new_value.append(benchmark_vals_dict.get(zip_fname, np.nan))
                    old_value.append(benchmark_vals_dict.get(zip_fname, np.nan))
            Z_rows.append(Z_row)
            new_values.append(new_value)
            old_values.append(old_value)
            Y.append(metric_name)

        Z = np.array(Z_rows)
        new_values = np.array(new_values)
        old_values = np.array(old_values)
        table_html = plot_colored_table(new_values, old_values, row_labels=X, col_labels=Y, tab_data=Z)

        # Write name of the metric group in human readable form.
        f.write(metrics_report.get_html_metric_heading(table_name))
        f.write(table_html)

        # Close HTML tags.
        f.write("</html>")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--curr_master_dirpath",
        required=True,
        help="Path to directory containing benchmark artifacts for the master branch.",
    )
    parser.add_argument(
        "--new_branch_dirpath",
        required=True,
        help="Path to directory containing benchmark artifacts for a new branch.",
    )
    args = parser.parse_args()
    generate_dashboard(
        curr_master_dirpath=args.curr_master_dirpath,
        new_branch_dirpath=args.new_branch_dirpath,
    )
