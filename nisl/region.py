"""
Regions of interest extraction and handling.
"""
# Author: Philippe Gervais
# License: simplified BSD

# Vocabulary:
# region array: 4D array, (x, y, z, region number) values are
#               weights (nifti-like)
# region list: list of 3D arrays [(x, y, z)] values are weights
# region labels: 3D array, (x, y, z) values are labels.

# masked regions: 2D array, (region number, voxel number) values are weights.
# apply_mask/unmask to convert to 4D array

import collections

import numpy as np
from scipy import linalg, ndimage

import nibabel

from . import utils
from . import masking


def apply_regions(voxel_signals, regions, normalize_regions=False):
    """Compute timeseries for regions of interest.

    This function takes timeseries as parameters (masked data).

    This function solves the inverse problem of finding the
    matrix region_signals such that:

    voxel_signals = np.dot(region_signals, regions)

    The direct problem is handled by unapply_regions().

    Parameters
    ==========
    voxel_signals (2D numpy array)
        Masked data (e.g. output of apply_mask())
        shape: (instant number, voxel number)

    regions (2D numpy array)
        shape: (region number, voxel number)
        Region definitions. One row of this array defines one
        region, given by its weight on each voxel. Voxel numbering
        must match that of `timeseries`.

    normalize_regions (boolean)
        If True, normalize output by
        (regions ** 2).sum(axis=0) / regions.sum(axis=0)
        This factor ensures that if all input timeseries are identical
        in a region, then the corresponding regions-timeseries is exactly the
        same, independent of the region weighting.

    Returns
    =======
    region_signals (2D numpy array)
        Computed signals for each region.
        shape: (instant number, region number)
    """
    region_signals = linalg.lstsq(regions.T, voxel_signals.T)[0].T
    if normalize_regions:
        region_signals /= regions.sum(axis=1) / (regions ** 2).sum(axis=1)
    return region_signals


def unapply_regions(region_signals, regions):
    """Recover voxel signals from regions signals.

    Parameters
    ==========
    region_signals (array-like)
        signals for regions. Shape: (instants number, region number)
    regions (array-like)
        regions definition. Shape: (region number, voxel number)

    Returns
    =======
    voxel_series (numpy.ndarray)
        Signals for voxels, masked.
        shape: (instants number, voxel number)

    Notes
    =====
    See also apply_regions().
    """
    # FIXME: turn second argument into niimg
    return np.dot(region_signals, regions)


def signals_from_labels(niimgs, labels_img, mask_img=None,
                        background_label=0, order="F"):
    """Extract region signals from fmri data.

    This function is applicable to regions defined by labels.

    labels, niimgs and mask shapes and affines must fit. This function
    performs no resampling.

    Parameters
    ==========
    niimgs (niimg)
        input images.

    labels_img (niimg)
        regions definition as labels. By default, the label zero is used to
        denote an absence of region. Use background_label to change it.

    mask_img (niimg)
        mask to apply to labels before extracting signals. Every point
        outside the mask is considered as background (i.e. no region).

    background_label (number)
        number representing background in labels_img.

    order (str)
        ordering of output array ("C" or "F"). Defaults to "F".

    Returns
    =======
    signals (numpy.ndarray)
        Signals extracted from each region. One output signal is the mean
        of all input signals in a given region.
        Shape is: (scan number, number of regions intersecting mask)
    labels (list)
        corresponding labels for each signal. signal[:, n] was extracted from
        the region with label labels[n].

    See also
    ========
    nisl.region.labels_from_signals
    """

    labels_img = utils.check_niimg(labels_img)

    # TODO: Make a special case for list of strings (load one image at a
    # time).
    niimgs = utils.check_niimgs(niimgs)
    target_affine = niimgs.get_affine()
    target_shape = utils._get_shape(niimgs)[:3]

    # Check shapes and affines.
    if utils._get_shape(labels_img) != target_shape:
        raise ValueError("labels_img and niimgs shapes must be identical.")
    if abs(labels_img.get_affine() - target_affine).max() > 1e-9:
        raise ValueError("labels_img and niimgs affines must be identical")

    if mask_img is not None:
        mask_img = utils.check_niimg(mask_img)
        if utils._get_shape(mask_img) != target_shape:
            raise ValueError("mask_img and niimgs shapes must be identical.")
        if abs(mask_img.get_affine() - target_affine).max() > 1e-9:
            raise ValueError("mask_img and niimgs affines must be identical")

    # Perform computations
    labels_data = labels_img.get_data()
    if mask_img is not None:
        mask_data = mask_img.get_data()
        labels_data = labels_data.copy()
        labels_data[np.logical_not(mask_data)] = background_label

    labels = list(np.unique(labels_data))
    if background_label in labels:
        labels.remove(background_label)

    data = niimgs.get_data()
    signals = np.ndarray((data.shape[-1], len(labels)), order=order)
    for n, img in enumerate(np.rollaxis(data, -1)):
        signals[n] = np.asarray(ndimage.measurements.mean(img,
                                                          labels=labels_data,
                                                          index=labels))
    return signals, labels


def img_from_labels(signals, labels_img, mask_img=None,
                    background_label=0, order="F"):
    """Create image from region signals.

    The same region signal is used for each voxel of the corresponding 3D
    volume.

    labels_img, mask_img must have the same shapes and affines.

    Parameters
    ==========

    labels_img (niimg)
        Region definitions using labels.

    mask_img (niimg, optional)
        Boolean array giving voxels to process. integer arrays also accepted,
        zero meaning False.

    background_label (number)
        label to use for "no region".

    order (str)
        ordering of output array ("C" or "F"). Defaults to "F".

    Returns
    =======
    img (Nifti1Image)
        Reconstructed image. dtype is that of "signals", affine and shape are
        those of labels_img.

    See also
    ========
    nisl.region.signals_from_labels
    """

    labels_img = utils.check_niimg(labels_img)

    signals = np.asarray(signals)
    target_affine = labels_img.get_affine()
    target_shape = utils._get_shape(labels_img)[:3]

    if mask_img is not None:
        mask_img = utils.check_niimg(mask_img)
        if utils._get_shape(mask_img) != target_shape:
            raise ValueError("mask_img and labels_img shapes "
                             "must be identical.")
        if abs(mask_img.get_affine() - target_affine).max() > 1e-9:
            raise ValueError("mask_img and labels_img affines "
                             "must be identical")

    data = np.zeros(target_shape + (signals.shape[0],),
                    dtype=signals.dtype, order=order)
    labels_data = labels_img.get_data()
    if mask_img is not None:
        mask_data = mask_img.get_data()
        labels_data = labels_data.copy()
        labels_data[np.logical_not(mask_data)] = background_label

    labels = list(np.unique(labels_data))
    if background_label in labels:
        labels.remove(background_label)

    for n, label in enumerate(labels):
        data[labels_data == label, :] = signals[:, n]

    return nibabel.Nifti1Image(data, target_affine)


def signals_from_maps(niimgs, maps_img, mask_img=None):
    """Extract region signals from fmri data.

    This function is applicable to regions defined by maps.

    Parameters
    ==========
    niimgs (niimg)
        input images.

    maps_img (niimg)
        regions definition as maps (array of weights).
        shape: niimgs.shape + (region number, )

    mask_img (niimg)
        mask to apply to regions before extracting signals. Every point
        outside the mask is considered as background (i.e. outside of any
        region).

    order (str)
        ordering of output array ("C" or "F"). Defaults to "F".

    Returns
    =======
    signals (numpy.ndarray)
        Signals extracted from each region.
        Shape is: (scans number, number of regions intersecting mask)

    See also
    ========
    nisl.region.signals_from_labels
    """

    maps_img = utils.check_niimg(maps_img)
    niimgs = utils.check_niimgs(niimgs)
    affine = niimgs.get_affine()
    shape = utils._get_shape(niimgs)[:3]

    # Check shapes and affines.
    if utils._get_shape(maps_img)[:3] != shape:
        raise ValueError("maps_img and niimgs shapes must be identical.")
    if abs(maps_img.get_affine() - affine).max() > 1e-9:
        raise ValueError("maps_img and niimgs affines must be identical")

    maps_data = maps_img.get_data()

    if mask_img is not None:
        mask_img = utils.check_niimg(mask_img)
        if utils._get_shape(mask_img) != shape:
            raise ValueError("mask_img and niimgs shapes must be identical.")
        if abs(mask_img.get_affine() - affine).max() > 1e-9:
            raise ValueError("mask_img and niimgs affines must be identical")
        maps_data, maps_mask, _ = _trim_maps(maps_data, mask_img.get_data())
        maps_mask = utils.as_ndarray(maps_mask, dtype=np.bool)
    else:
        maps_mask = np.ones(maps_data.shape[:3], dtype=np.bool)

    data = niimgs.get_data()
    signals = linalg.lstsq(maps_data[maps_mask, :],
                           data[maps_mask, :])[0].T

    return signals


def img_from_maps(signals, maps_img, mask_img=None):
    """
    See also
    ========
    nisl.region.img_from_labels
    """

    maps_img = utils.check_niimg(maps_img)
    maps_data = maps_img.get_data()
    shape = utils._get_shape(maps_img)[:3]
    affine = maps_img.get_affine()

    if mask_img is not None:
        mask_img = utils.check_niimg(mask_img)
        if utils._get_shape(mask_img) != shape:
            raise ValueError("mask_img and maps_img shapes must be identical.")
        if abs(mask_img.get_affine() - affine).max() > 1e-9:
            raise ValueError("mask_img and maps_img affines must be "
                             "identical.")
        maps_data, maps_mask, _ = _trim_maps(maps_data, mask_img.get_data())
        maps_mask = utils.as_ndarray(maps_mask, dtype=np.bool)
    else:
        maps_mask = np.ones(maps_data.shape[:3], dtype=np.bool)

    assert(maps_mask.shape == maps_data.shape[:3])
    data = np.dot(signals, maps_data[maps_mask, :].T)

    # FIXME: data = masking.unmask(data, maps_mask)
    return masking.unmask(data, nibabel.Nifti1Image(
        utils.as_ndarray(maps_mask, dtype=np.int8), affine)
                          )


def _trim_maps(maps, mask, order="F"):
    """Keep maps inside a mask.

    No consistency check is performed (esp. on affine). Every required check
    must be performed before calling this function.

    Parameters
    ==========
    maps (numpy.ndarray)
        Set of maps, defining some regions.

    mask (numpy.ndarray)
        Definition of a mask. The shape must match that of a single map.

    Returns
    =======
    trimmed_maps (numpy.ndarray)
        New set of maps, computed as intersection of each input map
        and mask. Empty maps are discarded, thus the number of output
        maps is not necessarily the same as the number of input maps.
        shape: mask.shape + (output maps number,)

    maps_mask (numpy.ndarray)
        Union of all output maps supports. One non-zero value in this
        array guarantees that there is at least one output map that is
        non-zero at this voxel.
        shape: mask.shape

    indices (numpy.ndarray)
        indices of regions that have an non-empty intersection with the
        given mask. len(indices) == trimmed_maps.shape[-1]
    """

    maps = maps.copy()
    sums = abs(maps[utils.as_ndarray(mask, dtype=np.bool), :]).sum(axis=0)

    n_regions = (sums > 0).sum()
    trimmed_maps = np.zeros(maps.shape[:3] + (n_regions, ),
                            dtype=maps.dtype, order=order)
    # use int8 instead of np.bool for Nifti1Image
    maps_mask = np.zeros(mask.shape, dtype=np.int8)

    # iterate on maps
    p = 0
    mask = utils.as_ndarray(mask, dtype=np.bool)
    for n, m in enumerate(np.rollaxis(maps, -1)):
        if sums[n] == 0:
            continue
        trimmed_maps[mask, p] = maps[mask, n]
        maps_mask[trimmed_maps[..., p] > 0] = 1
        p += 1

    return trimmed_maps, maps_mask, np.where(sums > 0)[0]


def _regions_are_overlapping_masked(regions_masked):
    """Predicate telling if any two regions are overlapping.

    Parameters
    ==========
    regions_masked (numpy.ndarray)
        shape (region number, voxel number). Values are weights.

    """
    count = np.where(regions_masked != 0, 1, 0).sum(axis=0)
    return np.any(count > 1)


def _regions_are_overlapping_array(regions_array):
    regions = np.where(regions_array != 0, 1, 0)
    return regions.sum(axis=-1).max() > 1


def _regions_are_overlapping_list(regions_list):
    """Predicate telling if any two regions are overlapping.

    Parameters
    ==========
    regions_list (list)
        Region definition as a list of 3D arrays

    Returns
    =======
    predicate (boolean)
        True if two regions are overlapping, False otherwise.
    """

    count = np.zeros(regions_list[0].shape)
    for region in regions_list:
        count += region != 0

    return count.max() > 1


def regions_are_overlapping(regions):
    """Predicate telling if any two regions are overlapping."""

    if isinstance(regions, list):
        predicate = _regions_are_overlapping_list(regions)
    elif isinstance(regions, np.ndarray):
        if regions.ndim == 2:
            predicate = _regions_are_overlapping_masked(regions)
        elif regions.ndim == 3:  # labeled array
            predicate = False
        elif regions.ndim == 4:  # arrays of 3D-arrays
            predicate = _regions_are_overlapping_array(regions)
        else:
            raise TypeError("input array may have 2 to 4 dimensions.")

    else:
        raise TypeError("type not understood")

    return predicate


def regions_to_mask(regions_img, threshold=0., background=0,
                    target_img=None, dtype=np.int8):
    """Merge all regions to give a binary mask.

    A non-zero value in the output means that this point is inside at
    least one region.

    This function can process regions defined as weights or as labels.
    A label image must always be a single image with 3 dimensions. Passing
    a list with only one 3D array does not qualify: it will be considered
    as a single fuzzy region.

    Parameters
    ==========
    regions_img (niimg or list of niimg)
        Regions definition as niimg, in one of the handled formats:
        (4D image, list of 3D images, or 3D label image). All images given
        therein must have the same shape and affine, no resampling is
        performed.

    threshold (float)
        absolute values of weights defining a region must be above this
        threshold to be considered as "inside". Used for fuzzy regions
        definition only (4D and list of 3D arrays). Defaults to zero, as
        it can be exactly represented in floating-point arithmetic.

    background (integer)
        value considered as background for the labeled array case (one 3D
        array) defaults to zero.

    target_img (niimg)
        Image which gives shape and affine to which output must be resampled.
        If None, affine and shape of regions are left unchanged. Resampling is
        performed after mask computation.
        Not implemented yet.

    dtype (numpy.dtype)
        dtype of the output image. This dtype must be storable in a Nifti file.
            (i.e. np.bool is not allowed).

    Returns
    =======
    mask (nibabel.Nifti1Image)
        union of all the regions (binary image)

    See also
    ========
    nisl.masking.intersect_masks
    """

    if isinstance(regions_img, collections.Iterable):
        first = utils.check_niimg(regions_img.__iter__().next())
        affine = first.get_affine()
        shape = utils._get_shape(first)
        if len(shape) != 3:
            raise ValueError("List must contain 3D arrays, {0:d}D "
                             + "array was provided".format(len(shape)))
        output = np.zeros(shape, dtype=dtype)
        del first
        for r in regions_img:  # Load one image at a time to save memory
            niimg = utils.check_niimg(r)
            if utils._get_shape(niimg) != output.shape:
                raise ValueError("Inconsistent shape in input list")
            output[abs(niimg.get_data()) > threshold] = True

    elif isinstance(regions_img, str) or utils.is_a_niimg(regions_img):
        niimg = utils.check_niimg(regions_img)
        shape = utils._get_shape(niimg)
        affine = niimg.get_affine()
        if len(shape) == 4:
            output = np.zeros(shape[:3], dtype=dtype)
            data = niimg.get_data()
            for n in xrange(shape[3]):
                output[abs(data[..., n]) > threshold] = True

        elif len(shape) == 3:  # labels
            output = (niimg.get_data() != background).astype(dtype)

        else:
            raise ValueError(
                "Invalid shape for input array: {0}".format(str(shape)))

    else:
        raise TypeError(
            "Unhandled data type: {0}".format(regions_img.__class__))

    # FIXME: resample if needed
    return nibabel.Nifti1Image(output, affine)
