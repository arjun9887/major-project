import json
import os
import random
import re
from collections import OrderedDict

import cv2
import numpy as np
import torch
from davis import cfg as eval_cfg

from .helpers import *
from .vos_dataset import VOSDataset


class YouTube(VOSDataset):
    """YouTube-VOS dataset. https://youtube-vos.org/"""

    mean_val = (104.00699, 116.66877, 122.67892)

    def __init__(self, *args, deepcopy=False, **kwargs):
        super(YouTube, self).__init__(*args, **kwargs)

        if self._full_resolution:
            raise NotImplementedError

        seqs = OrderedDict()
        imgs = []
        labels = []

        # seqs_key either loads file with sequences or specific sequence
        seqs_file = os.path.join(self.root_dir, f"{self.seqs_key}.txt")
        if os.path.exists(seqs_file):
            with open(seqs_file) as f:
                seqs_keys = [seq.strip() for seq in f.readlines()]
        else:
            raise NotImplementedError

        # if not os.path.exists(seqs_dir):
        #     raise NotImplementedError
        #     # seqs_keys = [self.seqs_key]

        # # Initialize the per sequence images for online training

        self._split = self.seqs_key.split('_')[0]
        seqs_dir = os.path.join(self.root_dir, self._split)

        if self._split in ['valid', 'test']:
            self.test_mode = True

        self._meta_data = None
        self.seq_key = None
        self.seqs = None
        self.imgs = None
        self.labels = None

        if not deepcopy:
            meta_file_path = os.path.join(seqs_dir, 'meta.json')
            with open(meta_file_path, 'r') as f:
                self._meta_data = json.load(f)

            # # seq_names = listdir_nohidden(os.path.join(seqs_dir, 'JPEGImages'))
            for seq_name in seqs_keys:

                img_names = np.sort(listdir_nohidden(
                    os.path.join(seqs_dir, 'JPEGImages', seq_name)))
                img_paths = list(map(lambda x: os.path.join(
                    seqs_dir, 'JPEGImages', seq_name, x), img_names))

                label_names = np.sort(listdir_nohidden(
                    os.path.join(seqs_dir, 'Annotations', seq_name)))
                label_paths = list(map(lambda x: os.path.join(
                    seqs_dir, 'Annotations', seq_name, x), label_names))

                if not self.test_mode and len(label_names) != len(img_names):
                    print(f'failure in: {self.seqs_key}/{seq_name}')

                seqs[seq_name] = {}
                seqs[seq_name]['imgs'] = img_paths
                seqs[seq_name]['labels'] = label_paths

                imgs.extend(img_paths)
                labels.extend(label_paths)

            self.seqs = seqs
            self.imgs = imgs
            self.labels = labels

            self.setup_davis_eval()

    @property
    def num_objects(self):
        """
        Retrieve number of objects from first frame ground truth which always
        contains all objects.
        """
        if self.seq_key is None:
            raise NotImplementedError
        if not self.multi_object:
            return 1

        return len(self._meta_data['videos'][self.seq_key]['objects'])

    # def set_seq(self, seq_name):
    #     self.imgs = self.seqs[seq_name]['imgs']
    #     self.labels = self.seqs[seq_name]['labels']
    #     self.seq_key = seq_name

    def set_seq(self, seq_name):
        # seqs_dir = os.path.join(self.root_dir, self._split)
        # img_names = np.sort(listdir_nohidden(
        #     os.path.join(seqs_dir, 'JPEGImages', seq_name)))
        # img_paths = list(map(lambda x: os.path.join(
        #     seqs_dir, 'JPEGImages', seq_name, x), img_names))

        # label_names = np.sort(listdir_nohidden(
        #     os.path.join(seqs_dir, 'Annotations', seq_name)))
        # label_paths = list(map(lambda x: os.path.join(
        #     seqs_dir, 'Annotations', seq_name, x), label_names))

        # self.seqs[seq_name]['imgs'] = img_paths
        # self.seqs[seq_name]['labels'] = label_paths

        # if not self.test_mode and len(label_names) != len(img_names):
        #     print(f'failure in: {self.seqs_key}/{seq_name}')

        super(YouTube, self).set_seq(seq_name)
        self._multi_object_id_to_label = [
            int(k) for k in sorted(self._meta_data['videos'][self.seq_key]['objects'].keys())]

    def set_gt_frame_id(self):
        objects_info = self._meta_data['videos'][self.seq_key]['objects']
        objects_info = [v for k, v in sorted(objects_info.items())]

        if 'test' in self.seqs_key:
            first_gt_image_name = objects_info[self.multi_object_id][0]
        else:
            first_gt_image_name = objects_info[self.multi_object_id]["frames"][0]

        self.frame_id = [path.find(first_gt_image_name) != -1 for path in self.imgs].index(True)
        self._label_id = [path.find(
            first_gt_image_name) != -1 for path in self.labels].index(True)

        # print(self.seq_key, self.multi_object_id, self.frame_id, self._label_id)

    # def get_random_frame_id_with_label(self):
    #     objects_info = self._meta_data['videos'][self.seq_key]['objects']
    #     objects_info = [v for k, v in sorted(objects_info.items())]
    #     objects_info = objects_info[self.multi_object_id]["frames"]

    #     random_frame_object = objects_info[torch.randint(len(objects_info), (1,)).item()]

    #     frame_ids = [i for i, path in enumerate(self.imgs) if random_frame_object in path]
    #     assert len(frame_ids) == 1

    #     # print(random_frame_object)
    #     # print(objects_info)
    #     # print(self.imgs, len(self.imgs))
    #     # print(frame_ids[0])
    #     # exit()

    #     return frame_ids[0]

    def setup_davis_eval(self):
        eval_cfg.MULTIOBJECT = bool(self.multi_object)
        # if self._full_resolution:
        #     eval_cfg.RESOLUTION = '1080p'
        # if self.test_mode:
        #     eval_cfg.PHASE = 'test-dev'
        eval_cfg.YEAR = 2017
        eval_cfg.PATH.ROOT = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '../..'))
        eval_cfg.PATH.DATA = os.path.abspath(
            os.path.join(eval_cfg.PATH.ROOT, self.root_dir, self._split))
        eval_cfg.PATH.SEQUENCES = os.path.join(
            eval_cfg.PATH.DATA, "JPEGImages")
        eval_cfg.PATH.ANNOTATIONS = os.path.join(
            eval_cfg.PATH.DATA, "Annotations")
        # eval_cfg.PATH.PALETTE = os.path.abspath(
        #     os.path.join(eval_cfg.PATH.ROOT, 'data/palette.txt'))

        eval_cfg.SEQUENCES = {n: {'name': n, 'attributes': [], 'set': 'train', 'eval_t': False, 'year': 2017, 'num_frames': len(v['imgs'])}
                              for n, v in self.seqs.items()}

    def __deepcopy__(self, memo):
        copy_obj = type(self)(self.seqs_key, self.root_dir, deepcopy=True)

        import copy
        for key in self.__dict__:
            copy_obj.__dict__[key] = copy.copy(self.__dict__[key])

        memo[id(self)] = copy_obj

        return copy_obj
