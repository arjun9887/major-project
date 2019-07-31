from __future__ import division

import os
import random
import numpy as np
import cv2
from scipy.misc import imresize
from collections import OrderedDict

from dataloaders.helpers import *
from torch.utils.data import Dataset


def listdir_nohidden(path):
    return [f for f in os.listdir(path) if not f.startswith('.')]


class DAVIS2016(Dataset):
    """DAVIS 2016 dataset constructed using the PyTorch built-in functionalities"""

    def __init__(self,
                 seqs='train_seqs',  # ['train_seqs', 'test_seqs', 'blackswan', ...]
                 frame_id=None,
                 input_res=None,
                 db_root_dir='./data/DAVIS-2016',
                 transform=None,
                 meanval=(104.00699, 116.66877, 122.67892)):
        """Loads image to label pairs for tool pose estimation
        db_root_dir: dataset directory with subfolders "JPEGImages" and "Annotations"
        """
        self.input_res = input_res
        self.db_root_dir = db_root_dir
        self.transform = transform
        self.meanval = meanval
        self.seqs = seqs
        self.frame_id = frame_id

        seqs_dict = OrderedDict()
        img_list = []
        labels = []

        if '_' in seqs:
            with open(os.path.join(db_root_dir, f'{seqs}.txt')) as f:
                for seq in f.readlines():
                    seq = seq.strip()
                    seqs_dict[seq] = {}

                    images = np.sort(listdir_nohidden(os.path.join(db_root_dir, 'JPEGImages/480p/', seq)))
                    img_list_seq = list(map(lambda x: os.path.join('JPEGImages/480p/', seq, x), images))

                    lab = np.sort(listdir_nohidden(os.path.join(db_root_dir, 'Annotations/480p/', seq)))
                    labels_seq = list(map(lambda x: os.path.join('Annotations/480p/', seq, x), lab))

                    assert (len(labels_seq) == len(img_list_seq)), f'failure in: {seq}'

                    seqs_dict[seq]['img_list'] = img_list_seq
                    seqs_dict[seq]['labels'] = labels_seq

                    img_list.extend(img_list_seq)
                    labels.extend(labels_seq)
        else:
            # Initialize the per sequence images for online training
            names_img = np.sort(listdir_nohidden(os.path.join(db_root_dir, 'JPEGImages/480p/', seqs)))
            img_list_seq = list(map(lambda x: os.path.join('JPEGImages/480p/', seqs, x), names_img))

            names_label = np.sort(listdir_nohidden(os.path.join(db_root_dir, 'Annotations/480p/', seqs)))
            labels_seq = list(map(lambda x: os.path.join('Annotations/480p/', seqs, x), names_label))

            assert (len(labels_seq) == len(img_list_seq)), f'failure in: {seqs}'

            seqs_dict[seqs] = {}
            seqs_dict[seqs]['img_list'] = img_list_seq
            seqs_dict[seqs]['labels'] = labels_seq

            img_list.extend(img_list_seq)
            labels.extend(labels_seq)

        self.seqs_dict = seqs_dict
        self.img_list = img_list
        self.labels = labels

    def set_random_seq(self):
        rnd_key_idx = random.randint(0, len(self.seqs_dict.keys()) - 1)
        rnd_seq_name = list(self.seqs_dict.keys())[rnd_key_idx]

        self.set_seq(rnd_seq_name)

    def set_random_frame_id(self):
        self.frame_id = random.randint(0, len(self.img_list) - 1)

    def get_seq_id(self):
        return list(self.seqs_dict.keys()).index(self.seqs)

    def set_next_seq(self):
        if '_' in self.seqs:
            key_idx = 0
        else:
            key_idx = self.get_seq_id() + 1
            if key_idx == len(self.seqs_dict.keys()):
                key_idx = 0

        seq_name = list(self.seqs_dict.keys())[key_idx]

        self.set_seq(seq_name)

    def set_seq(self, seq_name):
        img_list = self.seqs_dict[seq_name]['img_list']
        labels = self.seqs_dict[seq_name]['labels']

        self.img_list = img_list
        self.labels = labels
        self.seqs = seq_name

    def __len__(self):
        if self.frame_id is not None:
            return 1
        return len(self.img_list)

    def __getitem__(self, idx):
        if self.frame_id is not None:
            if self.frame_id == 'middle':
                idx = len(self.img_list) // 2
            elif self.frame_id == 'random':
                idx = random.randint(0, len(self.img_list) - 1)
            else:
                idx = self.frame_id
        img, gt = self.make_img_gt_pair(idx)

        sample = {'image': img, 'gt': gt}

        if '_' not in self.seqs:
            sample['fname'] = os.path.join(self.seqs, "%05d" % idx)

        if self.transform is not None:
            sample = self.transform(sample)

        return sample

    def make_img_gt_pair(self, idx):
        """
        Make the image-ground-truth pair
        """
        img = cv2.imread(os.path.join(self.db_root_dir, self.img_list[idx]))

        if self.labels[idx] is not None:
            label = cv2.imread(os.path.join(self.db_root_dir, self.labels[idx]), 0)
        else:
            gt = np.zeros(img.shape[:-1], dtype=np.uint8)

        if self.input_res is not None:
            img = imresize(img, self.input_res)
            if self.labels[idx] is not None:
                label = imresize(label, self.input_res, interp='nearest')

        img = np.array(img, dtype=np.float32)
        img = np.subtract(img, np.array(self.meanval, dtype=np.float32))

        if self.labels[idx] is not None:
            gt = np.array(label, dtype=np.float32)
            gt = gt/np.max([gt.max(), 1e-8])

        return img, gt

    def get_img_size(self):
        img = cv2.imread(os.path.join(self.db_root_dir, self.img_list[0]))

        return list(img.shape[:2])


# if __name__ == '__main__':
#     import custom_transforms as tr
#     import torch
#     from torchvision import transforms
#     from matplotlib import pyplot as plt

#     transforms = transforms.Compose([tr.RandomHorizontalFlip(), tr.Resize(scales=[0.5, 0.8, 1]), tr.ToTensor()])

#     dataset = DAVIS2016(db_root_dir='/media/eec/external/Databases/Segmentation/DAVIS-2016',
#                         train=True, transform=transforms)
#     dataloader = torch.utils.data.DataLoader(dataset, batch_size=1, shuffle=True, num_workers=1)

#     for i, data in enumerate(dataloader):
#         plt.figure()
#         plt.imshow(overlay_mask(im_normalize(tens2image(data['image'])), tens2image(data['gt'])))
#         if i == 10:
#             break

#     plt.show(block=True)