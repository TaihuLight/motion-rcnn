# --------------------------------------------------------
# Motion R-CNN
# Licensed under The MIT License [see LICENSE for details]
# Written by Simon Meister
# --------------------------------------------------------
import numpy as np

from object_detection.utils import np_box_list
from object_detection.utils import np_box_list_ops


def _pixels_to_3d(x, y, d, camera_intrinsics):
    f, x0, y0 = camera_intrinsics
    factor = d / f
    X = (x - x0) * factor
    Y = (y - y0) * factor
    Z = d
    return X, Y, Z


def _3d_to_pixels(points, camera_intrinsics):
    f, x0, y0 = camera_intrinsics
    X = points[:, :, 0]
    Y = points[:, :, 1]
    Z = points[:, :, 2]
    x = f * X / Z + x0
    y = f * Y / Z + y0
    return x, y


def dense_flow_from_motion(depth, motions, masks, camera_motion,
                           camera_intrinsics):
  """Compute optical flow map from depth and motion data.

  Args:
    depth: array with shape [height, width, 1].
    motions: array with shape [num_detections, 15].
    masks: array with shape [num_detections, height, width]
    camera_motion: array with shape [12].
    camera_intrinsics: array with shape [3].

  Returns:
    Array with shape [height, width, 2] representing the optical flow
    in x and y directions.
  """
  h, w = depth.shape[:2]
  depth = depth[:, :, 0]
  x_range = np.linspace(0, w - 1, w)
  y_range = np.linspace(0, h - 1, h)

  x, y = np.meshgrid(x_range, y_range)
  X, Y, Z = _pixels_to_3d(x, y, depth, camera_intrinsics)
  points = np.stack([x, y], axis=2)
  P = np.stack([X, Y, Z], axis=2)

  for i in range(motions.shape[0]):
    rot = np.reshape(motions[i, :9], [3, 3])
    trans = np.reshape(motions[i, 9:12], [3])
    pivot = np.reshape(motions[i, 12:], [3])
    mask = np.expand_dims(masks[i, :, :], 2)
    P += mask * ((P - pivot).dot(rot.T) + pivot + trans - P)

  rot_cam = np.reshape(camera_motion[:9], [3, 3])
  trans_cam = np.reshape(camera_motion[9:], [-1])
  P = P.dot(rot_cam.T) + trans_cam

  x_t, y_t = _3d_to_pixels(P, camera_intrinsics)
  points_t = np.stack([x_t, y_t], axis=2)

  flow = points_t - points
  return flow.astype(np.float32)


def euler_to_rot(x, y, z):
  rot_x = np.array([[1, 0, 0],
                    [0, np.cos(x), -np.sin(x)],
                    [0, np.sin(x), np.cos(x)]],
                   dtype=np.float32)
  rot_z = np.array([[np.cos(z), -np.sin(z), 0],
                    [np.sin(z), np.cos(z), 0],
                    [0, 0, 1]],
                   dtype=np.float32)
  rot_y = np.array([[np.cos(y), 0, np.sin(y)],
                    [0, 1, 0],
                    [-np.sin(y), 0, np.cos(y)]],
                   dtype=np.float32)
  return rot_z @ rot_x @ rot_y


def _motion_errors(pred, target):
  """
  Args:
    pred: array of shape [num_predictions, 15] containing predicted
      rotation matrix (flat), translation and pivot
    target: array of shape [num_predictions, 15] containing
      target rotation matrix (flat), translation and pivot.
  Returns:
    error_dict: dictionary of floats representing the mean
      rotation, translation, pivot, relative rotation and relative translation
      errors
  """
  def _rotation_angle(mat):
    return np.acos(np.clip((np.trace(mat, axis1=1, axis2=2) - 1) / 2, -1, 1))

  rot = np.reshape(pred[:, 0:9], [-1, 3, 3])
  trans = pred[:, 9:12]
  pivot = pred[:, 12:15]

  gt_rot = np.reshape(target[:, 0:9], [-1, 3, 3])
  gt_trans = target[:, 9:12]
  gt_pivot = target[:, 12:15]

  rot_T = np.transpose(rot, [0, 2, 1])
  d_rot = rot_T @ gt_rot
  d_trans = np.squeeze(rot_T @ np.reshape(gt_trans - trans, [-1, 3, 1]))
  d_pivot = gt_pivot - pivot

  err_angle = _rotation_angle(d_rot)
  err_trans = np.norm(d_trans, axis=1)
  err_pivot = np.norm(d_pivot, axis=1)

  err_rel_trans = err_trans / np.norm(gt_trans, axis=1)
  err_rel_angle = err_angle / _rotation_angle(gt_rot)

  mean_angle = np.mean(err_angle)
  mean_trans = np.mean(err_trans)
  mean_pivot = np.mean(err_pivot)
  mean_rel_angle = np.mean(rel_angle)
  mean_rel_trans = np.mean(rel_trans)

  error_dict = {
      'mRot': mean_angle,
      'mTrans': mean_trans,
      'mPivot': mean_pivot,
      'mRelRot': mean_rel_angle,
      'mRelTrans': mean_rel_trans}

  return {k, np.asscalar(v) for (k, v) in error_dict.items()}


def evaluate(gt_boxes, gt_motions, detected_boxes, detected_motions,
             matching_iou_threshold=.5):
  gt_boxlist = np_box_list.BoxList(gt_boxes)
  detected_boxlist = np_box_list.BoxList(detected_boxes)

  iou = np_box_list_ops.iou(detected_boxlist, gt_boxlist)
  max_overlap_gt_ids = np.argmax(iou, axis=1)

  pred_list = []
  target_list = []
  for i in range(detected_boxlist.num_boxes()):
    gt_id = max_overlap_gt_ids[i]
    if iou[i, gt_id] >= matching_iou_threshold:
      pred_list.append(detected_motions[i, :])
      target_list.append(gt_boxes[gt_id, :])
  pred = np.stack(pred_list, axis=0)
  target = np.stack(target_list, axis=0)
  return _motion_errors(pred, target)
