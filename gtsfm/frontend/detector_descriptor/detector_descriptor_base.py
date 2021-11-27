"""Joint detector and descriptor for the front end.

Authors: Ayush Baid
"""

import abc
from typing import Tuple

import dask
import numpy as np
from dask.delayed import Delayed

import gtsfm.utils.logger as logger_utils
from gtsfm.common.image import Image
from gtsfm.common.keypoints import Keypoints

logger = logger_utils.get_logger()


class DetectorDescriptorBase(metaclass=abc.ABCMeta):
    """Base class for all methods which provide a joint detector-descriptor to work on an image.

    This class serves as a combination of individual detector and descriptor.
    """

    def __init__(self, max_keypoints: int = 5000):
        """Initialize the detector-descriptor.

        Args:
            max_keypoints: Maximum number of keypoints to detect. Defaults to 5000.
        """
        self.max_keypoints = max_keypoints

    @abc.abstractmethod
    def detect_and_describe(self, image: Image) -> Tuple[Keypoints, np.ndarray]:
        """Perform feature detection as well as their description.

        Refer to detect() in DetectorBase and describe() in DescriptorBase for
        details about the output format.

        Args:
            image: the input image.

        Returns:
            Detected keypoints, with length N <= max_keypoints.
            Corr. descriptors, of shape (N, D) where D is the dimension of each descriptor.
        """

    def filter_by_response(self, keypoints: Keypoints, descriptors: np.ndarray) -> Tuple[Keypoints, np.ndarray]:
        """Filter features according to their responses.

        Args:
            keypoints: detected keypoints with length M.
            descriptors: (M, D) array of descriptors D is the dimension of each descriptor.

        Returns:
            The top N (<= `self.max_keypoints`) keypoints, and their corresponding desciptors as an (N, D) array, with
                respect to their responses.
        """
        if keypoints.responses is None:
            return keypoints, descriptors

        # Sort by responses.
        sort_idxs = np.argsort(-keypoints.responses)
        sort_idxs = sort_idxs[: self.max_keypoints]

        return keypoints.extract_indices(sort_idxs), descriptors[sort_idxs]

    @staticmethod
    def filter_by_mask(mask: np.ndarray, keypoints: Keypoints, descriptors: np.ndarray) -> Tuple[Keypoints, np.ndarray]:
        """Filter features with respect to a binary mask of the image.

        Args:
            mask: (H, W) array of 0's and 1's corresponding to valid portions of the original image.
            keypoints: detected keypoints with length M.
            descriptors: (M, D) array of descriptors D is the dimension of each descriptor.

        Returns:
            N <= M keypoints, and their corresponding desciptors as an (N, D) array, such that their (rounded)
                coordinates corresponded to a 1 in the input mask array.
        """
        rounded_coordinates = np.round(keypoints.coordinates).astype(int)
        valid_idxs = np.flatnonzero(mask[rounded_coordinates[:, 1], rounded_coordinates[:, 0]] == 1)

        return keypoints.extract_indices(valid_idxs), descriptors[valid_idxs]

    def create_computation_graph(self, image_graph: Delayed) -> Tuple[Delayed, Delayed]:
        """Generates the computation graph for detections and their descriptors.

        Args:
            image_graph: computation graph for a single image (from a loader).

        Returns:
            Delayed tasks for detections.
            Delayed task for corr. descriptors.
        """
        keypoints_graph, descriptor_graph = dask.delayed(self.detect_and_describe, nout=2)(image_graph)

        return keypoints_graph, descriptor_graph
