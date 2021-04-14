import sys
sys.path.append("gtsfm/densify/mvsnets/source/PatchmatchNet")
import argparse
import os
import torch
import torch.nn as nn
import torch.nn.parallel
import torch.backends.cudnn as cudnn
import torch.optim as optim
from torch.utils.data import DataLoader
from torch.autograd import Variable
import torch.nn.functional as F
import numpy as np
import time
from datasets import find_dataset_def
from models import *
from utils import *
from datasets.data_io import read_pfm, save_pfm
import cv2
from plyfile import PlyData, PlyElement
from PIL import Image
from matplotlib import pyplot as plt

cudnn.benchmark = True

# read intrinsics and extrinsics
def read_camera_parameters(filename):
    with open(filename) as f:
        lines = f.readlines()
        lines = [line.rstrip() for line in lines]
    # extrinsics: line [1,5), 4x4 matrix
    extrinsics = np.fromstring(' '.join(lines[1:5]), dtype=np.float32, sep=' ').reshape((4, 4))
    # intrinsics: line [7-10), 3x3 matrix
    intrinsics = np.fromstring(' '.join(lines[7:10]), dtype=np.float32, sep=' ').reshape((3, 3))
    
    return intrinsics, extrinsics


# read an image
def read_img(img, img_wh):
    # scale 0~255 to 0~1
    np_img = np.array(img, dtype=np.float32) / 255.
    np_img = cv2.resize(np_img, img_wh, interpolation=cv2.INTER_LINEAR)
    return np_img


# save a binary mask
def save_mask(filename, mask):
    assert mask.dtype == np.bool
    mask = mask.astype(np.uint8) * 255
    Image.fromarray(mask).save(filename)


def save_depth_img(filename, depth):
    # assert mask.dtype == np.bool
    d = depth.max() - depth.min()
    b = -depth.min()
    depth = 255 * (depth + b) / d
    transform = np.array([d / 255.0, -b])
    depth = depth.astype(np.uint8)
    
    # heatmap = cv2.applyColorMap(depth, cv2.COLORMAP_JET)
    # Image.fromarray(depth).save(filename)
    cv2.imwrite(filename, depth)
    np.save(filename+'.npy', transform)


def read_pair_file(pairs, nviews):

    num_viewpoint = pairs.shape[0]

    for i in range(num_viewpoint):
        pairs[i][i] = -np.inf 
    
    pair_idx = np.argsort(pairs, axis=0)[:, 1:nviews][:, ::-1]

    data = []
    for view_idx in range(num_viewpoint):
        ref_view = view_idx
        src_views = pair_idx[i].tolist()
        data.append((ref_view, src_views))
    return data
                

# project the reference point cloud into the source view, then project back
def reproject_with_depth(depth_ref, intrinsics_ref, extrinsics_ref, depth_src, intrinsics_src, extrinsics_src):
    width, height = depth_ref.shape[1], depth_ref.shape[0]
    ## step1. project reference pixels to the source view
    # reference view x, y
    x_ref, y_ref = np.meshgrid(np.arange(0, width), np.arange(0, height))
    x_ref, y_ref = x_ref.reshape([-1]), y_ref.reshape([-1])
    # reference 3D space
    xyz_ref = np.matmul(np.linalg.inv(intrinsics_ref),
                        np.vstack((x_ref, y_ref, np.ones_like(x_ref))) * depth_ref.reshape([-1]))
    # source 3D space
    xyz_src = np.matmul(np.matmul(extrinsics_src, np.linalg.inv(extrinsics_ref)),
                        np.vstack((xyz_ref, np.ones_like(x_ref))))[:3]
    # source view x, y
    K_xyz_src = np.matmul(intrinsics_src, xyz_src)
    xy_src = K_xyz_src[:2] / K_xyz_src[2:3]

    ## step2. reproject the source view points with source view depth estimation
    # find the depth estimation of the source view
    x_src = xy_src[0].reshape([height, width]).astype(np.float32)
    y_src = xy_src[1].reshape([height, width]).astype(np.float32)
    sampled_depth_src = cv2.remap(depth_src, x_src, y_src, interpolation=cv2.INTER_LINEAR)
    # mask = sampled_depth_src > 0

    # source 3D space
    # NOTE that we should use sampled source-view depth_here to project back
    xyz_src = np.matmul(np.linalg.inv(intrinsics_src),
                        np.vstack((xy_src, np.ones_like(x_ref))) * sampled_depth_src.reshape([-1]))
    # reference 3D space
    xyz_reprojected = np.matmul(np.matmul(extrinsics_ref, np.linalg.inv(extrinsics_src)),
                                np.vstack((xyz_src, np.ones_like(x_ref))))[:3]
    # source view x, y, depth
    depth_reprojected = xyz_reprojected[2].reshape([height, width]).astype(np.float32)
    K_xyz_reprojected = np.matmul(intrinsics_ref, xyz_reprojected)
    xy_reprojected = K_xyz_reprojected[:2] / K_xyz_reprojected[2:3]
    x_reprojected = xy_reprojected[0].reshape([height, width]).astype(np.float32)
    y_reprojected = xy_reprojected[1].reshape([height, width]).astype(np.float32)

    return depth_reprojected, x_reprojected, y_reprojected, x_src, y_src


def check_geometric_consistency(depth_ref, intrinsics_ref, extrinsics_ref, depth_src, intrinsics_src, extrinsics_src,
                                geo_pixel_thres, geo_depth_thres):
    width, height = depth_ref.shape[1], depth_ref.shape[0]
    x_ref, y_ref = np.meshgrid(np.arange(0, width), np.arange(0, height))
    depth_reprojected, x2d_reprojected, y2d_reprojected, x2d_src, y2d_src = reproject_with_depth(depth_ref, intrinsics_ref, extrinsics_ref,
                                                     depth_src, intrinsics_src, extrinsics_src)
    # print(depth_ref.shape)
    # print(depth_reprojected.shape)
    # check |p_reproj-p_1| < 1
    dist = np.sqrt((x2d_reprojected - x_ref) ** 2 + (y2d_reprojected - y_ref) ** 2)

    # check |d_reproj-d_1| / d_1 < 0.01
    # depth_ref = np.squeeze(depth_ref, 2)
    depth_diff = np.abs(depth_reprojected - depth_ref)
    relative_depth_diff = depth_diff / depth_ref

    mask = np.logical_and(dist < geo_pixel_thres, relative_depth_diff < geo_depth_thres)
    depth_reprojected[~mask] = 0

    return mask, depth_reprojected, x2d_src, y2d_src


def filter_depth(data, nviews, depth_list, confidence_list, geo_pixel_thres, geo_depth_thres, photo_thres, img_wh, out_folder, plyfilename, save_output=False):
    # for the final point cloud
    vertexs = []
    vertex_colors = []

    pair_data = read_pair_file(data['pairs'], nviews)

    # for each reference view and the corresponding source views
    for ref_view, src_views in pair_data:
        # load the camera parameters
        ref_intrinsics, ref_extrinsics = data['cameras'][ref_view]
        
        # load the reference image
        ref_img = read_img(data['images'][ref_view], img_wh)
        # load the estimated depth of the reference view
        ref_depth_est = depth_list[ref_view][0]
        # load the photometric mask of the reference view
        confidence = confidence_list[ref_view]
        
        if save_output:
            os.makedirs(os.path.join(out_folder, "depth_img"), exist_ok=True)
            save_depth_img(os.path.join(out_folder, 'depth_img/depth_{:0>8}.png'.format(ref_view)), ref_depth_est.astype(np.float32))
            save_depth_img(os.path.join(out_folder, 'depth_img/conf_{:0>8}.png'.format(ref_view)), confidence.astype(np.float32))

        photo_mask = confidence > photo_thres
        
        all_srcview_depth_ests = []

        # compute the geometric mask
        geo_mask_sum = 0
        for src_view in src_views:
            # camera parameters of the source view
            src_intrinsics, src_extrinsics = data['cameras'][src_view]

            # the estimated depth of the source view
            src_depth_est = depth_list[src_view][0]

            geo_mask, depth_reprojected, x2d_src, y2d_src = check_geometric_consistency(ref_depth_est, ref_intrinsics, ref_extrinsics,
                                                                      src_depth_est,
                                                                      src_intrinsics, src_extrinsics,
                                                                      geo_pixel_thres, geo_depth_thres)
            geo_mask_sum += geo_mask.astype(np.int32)
            all_srcview_depth_ests.append(depth_reprojected)

        depth_est_averaged = (sum(all_srcview_depth_ests) + ref_depth_est) / (geo_mask_sum + 1)
        # at least 3 source views matched
        # large threshold, high accuracy, low completeness
        geo_mask = geo_mask_sum >= 3
        final_mask = np.logical_and(photo_mask, geo_mask)
       
        if save_output:
            os.makedirs(os.path.join(out_folder, "mask"), exist_ok=True)
            save_mask(os.path.join(out_folder, "mask/{:0>8}_photo.png".format(ref_view)), photo_mask)
            save_mask(os.path.join(out_folder, "mask/{:0>8}_geo.png".format(ref_view)), geo_mask)
            save_mask(os.path.join(out_folder, "mask/{:0>8}_final.png".format(ref_view)), final_mask)

        print("processing ref-view{:0>2}, geo_mask:{:3f} photo_mask:{:3f} final_mask: {:3f}".format(ref_view,
                                                                geo_mask.mean(), photo_mask.mean(), final_mask.mean()))

        height, width = depth_est_averaged.shape[:2]
        x, y = np.meshgrid(np.arange(0, width), np.arange(0, height))
        
        valid_points = final_mask
        # print("valid_points", valid_points.mean())
        x, y, depth = x[valid_points], y[valid_points], depth_est_averaged[valid_points]
        
        color = ref_img[valid_points]
        xyz_ref = np.matmul(    np.linalg.inv(ref_intrinsics),
                            np.vstack((x, y, np.ones_like(x))) * depth)
        xyz_world = np.matmul(np.linalg.inv(ref_extrinsics),
                              np.vstack((xyz_ref, np.ones_like(x))))[:3]
        vertexs.append(xyz_world.transpose((1, 0)))
        vertex_colors.append((color * 255).astype(np.uint8))

    vertexs_raw = np.concatenate(vertexs, axis=0)

    if save_output:
        vertex_colors = np.concatenate(vertex_colors, axis=0)
        vertexs = np.array([tuple(v) for v in vertexs_raw], dtype=[('x', 'f4'), ('y', 'f4'), ('z', 'f4')])
        vertex_colors = np.array([tuple(v) for v in vertex_colors], dtype=[('red', 'u1'), ('green', 'u1'), ('blue', 'u1')])

        vertex_all = np.empty(len(vertexs), vertexs.dtype.descr + vertex_colors.dtype.descr)
        for prop in vertexs.dtype.names:
            vertex_all[prop] = vertexs[prop]
        for prop in vertex_colors.dtype.names:
            vertex_all[prop] = vertex_colors[prop]

        el = PlyElement.describe(vertex_all, 'vertex')
        PlyData([el]).write(plyfilename)
        print("saving the final model to", plyfilename)

    return vertexs_raw


def eval_function(gtargs):
    data = gtargs['mvsnetsData']
    save_output = gtargs['save_output']
    """
        data.images
        data.cameras [[i1, e1], [i2, e2], ..., [in, en]]
        data.pairs   
        data.depthRange
    """
    MVSDataset = find_dataset_def("gtsfm")
    test_dataset = MVSDataset(data, gtargs["n_views"], img_wh=gtargs["img_wh"])
    TestImgLoader = DataLoader(test_dataset, 1, shuffle=False, num_workers=4, drop_last=False)
    # model
    model = PatchmatchNet(patchmatch_interval_scale=[0.005, 0.0125, 0.025],
                propagation_range = [6, 4, 2], patchmatch_iteration=[1, 2, 2], 
                patchmatch_num_sample = [8, 8, 16], 
                propagate_neighbors=[0, 8, 16], evaluate_neighbors=[9, 9, 9])
    model = nn.DataParallel(model)
    model.cuda()

    # load checkpoint file specified by args.loadckpt
    print("loading model {}".format(gtargs["loadckpt"]))
    state_dict = torch.load(gtargs["loadckpt"])
    model.load_state_dict(state_dict['model'])
    model.eval()
    depth_est_list = {}
    confidence_est_list = {}
    with torch.no_grad():
        for batch_idx, sample in enumerate(TestImgLoader):
            start_time = time.time()
            sample_cuda = tocuda(sample)
            outputs = model(sample_cuda["imgs"], sample_cuda["proj_matrices"], 
                            sample_cuda["depth_min"], sample_cuda["depth_max"])
            
            outputs = tensor2numpy(outputs)
            del sample_cuda
            print('Iter {}/{}, time = {:.3f}'.format(batch_idx, len(TestImgLoader), time.time() - start_time))
            filenames = sample["filename"]
            ids = sample["idx"]
            
            # save depth maps and confidence maps
            for idx, filename, depth_est, photometric_confidence in zip(ids, filenames, outputs["refined_depth"]['stage_0'],
                                                                outputs["photometric_confidence"]):
                
                idx = idx.cpu().numpy().tolist()
                depth_est_list[idx] = depth_est.copy()
                confidence_est_list[idx] = photometric_confidence.copy()

                if save_output:
                    depth_filename = os.path.join(gtargs["outdir"], filename.format('depth_est', '.pfm'))
                    confidence_filename = os.path.join(gtargs["outdir"], filename.format('confidence', '.pfm'))
                    
                    os.makedirs(depth_filename.rsplit('/', 1)[0], exist_ok=True)
                    os.makedirs(confidence_filename.rsplit('/', 1)[0], exist_ok=True)
                    # save depth maps
                    depth_est = np.squeeze(depth_est, 0)
                    save_pfm(depth_filename, depth_est)
                    # save confidence maps
                    save_pfm(confidence_filename, photometric_confidence)
                             
    out_folder = gtargs["outdir"]
    
    # step2. filter saved depth maps with geometric constraints
    return filter_depth(data, gtargs["n_views"], depth_est_list, confidence_est_list,
                gtargs["thres"][0], gtargs["thres"][1], gtargs["thres"][2], gtargs["img_wh"],
                out_folder, os.path.join(gtargs["outdir"], 'patchmatchnet_l3.ply'), save_output)
 