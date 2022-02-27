"""Script to generate a report of metrics with tables and plots 
using the metrics that have been logged as JSON in a previous run of the pipeline. 

Authors: Akshay Krishnan
"""
import os
import argparse
from pathlib import Path


from gtsfm.evaluation.metrics import GtsfmMetricsGroup
import gtsfm.evaluation.metrics_report as metrics_report
import gtsfm.evaluation.compare_metrics as compare_metrics
import gtsfm.utils.logger as logger_utils

logger = logger_utils.get_logger()

GTSFM_MODULE_METRICS_FNAMES = [
    "frontend_summary.json",
    "rotation_cycle_consistency_metrics.json",
    "rotation_averaging_metrics.json",
    "translation_averaging_metrics.json",
    "data_association_metrics.json",
    "bundle_adjustment_metrics.json",
]

def save_other_metrics(other_pipeline_files_dirpath: str, other_pipeline_json_path: str):
    """Saves other metrics as GTSfM Metrics Groups in json files.

    Args:
        colmap_files_dirpath: The path to a directory containing colmap output as txt files.
        colmap_json_path: The path to the directory where colmap output will be saved in json files.

    """
    if Path(other_pipeline_files_dirpath).exists():
        txt_metric_paths = {
            os.path.basename(other_pipeline_json_path): other_pipeline_files_dirpath,
        }
        json_path = os.path.dirname(other_pipeline_json_path)
        compare_metrics.save_other_pipelines_metrics(txt_metric_paths, json_path, GTSFM_MODULE_METRICS_FNAMES)
    else:
        logger.info("%s does not exist", other_pipeline_files_dirpath)


def create_metrics_plots_html(json_path: str, output_dir: str, colmap_json_path: str, openmvg_json_path: str) -> None:
    #TODO (Jon): Make varargs for other pipelines
    """Creates a HTML report of metrics from frontend, averaging, data association and bundle adjustment.

    Reads the metrics from JSON files in a previous run.

    Args:
        json_path: Path to folder that contains GTSfM metrics as json files.
        colmap_json_path: The path to the directory of colmap outputs in json files.
        output_dir: directory to save the report, uses json_path if empty.
    """
    metrics_groups = []
    # The provided JSON path must contain these files which contain metrics from the respective modules.


    metric_paths = []
    for filename in GTSFM_MODULE_METRICS_FNAMES:
        logger.info("Adding metrics from %s", filename)
        metric_path = os.path.join(json_path, filename)
        metric_paths.append(metric_path)
        metrics_groups.append(GtsfmMetricsGroup.parse_from_json(metric_path))
    if len(output_dir) == 0:
        output_dir = json_path
    output_file = os.path.join(output_dir, "gtsfm_metrics_report.html")
    other_pipeline_metrics_groups = {}

    colmap_metrics_groups = []
    if colmap_json_path is not None:
        for i, metrics_group in enumerate(metrics_groups):
            metric_path = metric_paths[i]
            colmap_metric_path = os.path.join(colmap_json_path, os.path.basename(metric_path))
            colmap_metrics_groups.append(GtsfmMetricsGroup.parse_from_json(colmap_metric_path))
        other_pipeline_metrics_groups["colmap"] = colmap_metrics_groups

    openmvg_metrics_groups = []
    if openmvg_json_path is not None:
        for i, metrics_group in enumerate(metrics_groups):
            metric_path = metric_paths[i]
            openmvg_metric_path = os.path.join(openmvg_json_path, os.path.basename(metric_path))
            openmvg_metrics_groups.append(GtsfmMetricsGroup.parse_from_json(openmvg_metric_path))
        other_pipeline_metrics_groups["openmvg"] = openmvg_metrics_groups

    metrics_report.generate_metrics_report_html(metrics_groups, output_file, other_pipeline_metrics_groups)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics_dir", default="result_metrics", help="Directory containing the metrics json files.")
    parser.add_argument("--colmap_files_dirpath", default=None, type=str, help="Directory containing COLMAP output .")
    parser.add_argument("--openmvg_files_dirpath", default=None, type=str, help="Directory containing OpenMVG output .")
    parser.add_argument("--output_dir", default="", help="Directory to save plots to. Same as metrics_dir by default.")
    args = parser.parse_args()
    if args.colmap_files_dirpath is not None:
        colmap_json_path = os.path.join(args.metrics_dir, "colmap")
        save_other_metrics(args.colmap_files_dirpath, colmap_json_path)  # saves metrics to the json path
    else:
        colmap_json_path = None
    if args.openmvg_files_dirpath is not None:
        openmvg_json_path = os.path.join(args.metrics_dir, "openmvg")
        save_other_metrics(args.openmvg_files_dirpath, openmvg_json_path)  # saves metrics to the json path
    else:
        openmvg_json_path = None


    create_metrics_plots_html(args.metrics_dir, args.output_dir, colmap_json_path, openmvg_json_path)