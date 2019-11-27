# Package Includes
from __future__ import division

import itertools
import os
import socket
import timeit
from datetime import datetime

import imageio
import numpy as np
import sacred
import torch
from meta_optim.utils import dict_to_html
from pytorch_tools.ingredients import (get_device, set_random_seeds,
                                       torch_ingredient)
from pytorch_tools.vis import LineVis, TextVis
from tensorboardX import SummaryWriter
from util import visualize as viz
from util.helper_func import (data_loaders, early_stopping, eval_davis,
                              eval_loader, init_parent_model, train_val)

torch_ingredient.add_config('cfgs/torch.yaml')
ex = sacred.Experiment('osvos-online', ingredients=[torch_ingredient])

ex.add_config('cfgs/online.yaml')
ex.add_named_config('VGG', 'cfgs/online_vgg.yaml')

train_val = ex.capture(train_val)
early_stopping = ex.capture(early_stopping, prefix='train_early_stopping')
data_loaders = ex.capture(data_loaders, prefix='data_cfg')


@ex.capture
def init_vis(db_train, validate_inter, env_suffix, _config, _run, torch_cfg):
    vis_dict = {}
    run_name = f"{_run.experiment_info['name']}_{env_suffix}"

    opts = dict(title="CONFIG and RESULTS", width=300, height=1000)
    vis_dict['config_vis'] = TextVis(opts, env=run_name, **torch_cfg['vis'])
    vis_dict['config_vis'].plot(dict_to_html(_config))

    opts = dict(
        title=f"OSVOS ONLINE",
        xlabel=f"VALIDATIONS (* {validate_inter} = NUM EPOCHS)",
        width=750,
        height=300,
        legend=['LOSS', 'J'])
    vis_dict['val_metrics'] = LineVis(opts, env=run_name, **torch_cfg['vis'])

    return vis_dict


@ex.capture
def init_optim(model, num_epochs, parent_model, optim_cfg):
    device = get_device()

    lr_scheduler = None
    if optim_cfg['file_dir'] is not None:
        optim = torch.load(optim_cfg['file_dir'],
                           map_location=lambda storage, loc: storage)
        optim.to(device)
        optim.set_model(model)
        optim.set_meta_model(model)
        optim.reset()
        optim.eval()
    else:
        lr = optim_cfg['lr']
        if 'VGG' in parent_model['base_path']:
            wd = optim_cfg['wd']
            mom = optim_cfg['mom']
            optim = torch.optim.SGD([
                {'params': [pr[1] for pr in model.stages.named_parameters(
                ) if 'weight' in pr[0]], 'weight_decay': wd},
                {'params': [pr[1] for pr in model.stages.named_parameters(
                ) if 'bias' in pr[0]], 'lr': lr * 2},
                {'params': [pr[1] for pr in model.side_prep.named_parameters(
                ) if 'weight' in pr[0]], 'weight_decay': wd},
                {'params': [pr[1] for pr in model.side_prep.named_parameters(
                ) if 'bias' in pr[0]], 'lr': lr*2},
                {'params': [pr[1] for pr in model.upscale.named_parameters(
                ) if 'weight' in pr[0]], 'lr': 0},
                {'params': [pr[1] for pr in model.upscale_.named_parameters(
                ) if 'weight' in pr[0]], 'lr': 0},
                {'params': model.fuse.weight, 'lr': lr/100, 'weight_decay': wd},
                {'params': model.fuse.bias, 'lr': 2*lr/100},
            ], lr=lr, momentum=mom)
        else:
            optim = torch.optim.Adam(model.parameters(), lr=lr)

        if optim_cfg['exp_lr_decay_final_lr'] is not None:
            gamma = (optim_cfg['exp_lr_decay_final_lr'] / lr) ** (1 / num_epochs)
            lr_scheduler = torch.optim.lr_scheduler.ExponentialLR(optim, gamma)

    return optim, lr_scheduler


@ex.automain
def main(seed: int, validate_inter: int, vis_interval: int, _log, _config: dict,
         num_epochs: int, dataset: str, loss_func, parent_model: dict):
    device = get_device()
    set_random_seeds(seed)

    model, parent_states = init_parent_model(**parent_model)
    model.to(device)
    train_loader, test_loader = data_loaders(dataset)  # pylint: disable=E1120
    if vis_interval is not None:
        vis_dict = init_vis(train_loader.dataset)  # pylint: disable=E1120

    val_loader = None
    if validate_inter:
        val_loader = test_loader

    metrics_names = ['train_loss_hist', 'val_loss_hist', 'val_J_hist', 'val_acc_hist',
                     'val_F_hist', 'test_loss', 'init_test_loss', 'init_test_J',
                     'init_test_F', 'init_test_acc', 'test_J', 'test_F', 'test_acc']
    metrics = {n: [] for n in metrics_names}

    for seq_name in train_loader.dataset.seqs_names:
        _log.info(f"Train Online: {seq_name}")
        # img_save_dir = os.path.join(results_dir, seq_name)
        # if not os.path.exists(img_save_dir):
        #     os.makedirs(img_save_dir)

        optim, lr_scheduler = init_optim(model)  # pylint: disable=E1120

        train_loader.dataset.set_seq(seq_name)
        test_loader.dataset.set_seq(seq_name)

        for state, split in zip(parent_states['val']['states'], parent_states['val']['splits']):
            if seq_name in split:
                model.load_state_dict(state)
                break

        model.to(device)
        model.zero_grad()

        with torch.no_grad():
            test_loss_batches, test_acc_batches, test_J, test_F = eval_loader(model, test_loader, loss_func=loss_func)
        metrics['init_test_loss'].append(test_loss_batches.mean())
        metrics['init_test_J'].extend(test_J)
        metrics['init_test_F'].extend(test_F)
        metrics['init_test_acc'].append(test_acc_batches.mean())

        train_loss, val_loss, val_acc, val_J, val_F = train_val(  # pylint: disable=E1120
            model, train_loader, val_loader, optim,
            early_stopping_func=early_stopping,
            validate_inter=validate_inter,
            loss_func=loss_func,
            lr_scheduler=lr_scheduler)
        metrics['train_loss_hist'].append(train_loss)
        metrics['val_loss_hist'].append(val_loss)
        metrics['val_acc_hist'].append(val_acc)
        metrics['val_J_hist'].extend(val_J)
        metrics['val_F_hist'].extend(val_F)

        with torch.no_grad():
            test_loss_batches, test_acc_batches, test_J, test_F = eval_loader(model, test_loader, loss_func=loss_func)
        metrics['test_loss'].append(test_loss_batches.mean())
        metrics['test_J'].extend(test_J)
        metrics['test_F'].extend(test_F)
        metrics['test_acc'].append(test_acc_batches.mean())

    metrics = {n: m if 'hist' in n else torch.tensor(m)
               for n, m in metrics.items()}

    train_loss = torch.tensor([m.mean() for m in metrics['train_loss_hist']])
    results_str = (
        "<p>METRICS:<br>\n"
        f"&nbsp;&nbsp;INIT TEST MEAN J/F/ACC: {metrics['init_test_J'].mean():.2f}/{metrics['init_test_F'].mean():.2f}/{metrics['init_test_acc'].mean():.2f}<br>\n"
        f"&nbsp;&nbsp;TEST MEAN J/F/ACC: {metrics['test_J'].mean():.2f}/{metrics['test_F'].mean():.2f}/{metrics['test_acc'].mean():.2f}<br>\n"
        "<br>\n"
        "RUN TRAIN loss:<br>\n"
        f"&nbsp;&nbsp;MIN seq: {train_loss.min():.2f}<br>\n"
        f"&nbsp;&nbsp;MAX seq: {train_loss.max():.2f}<br>\n"
        f"&nbsp;&nbsp;MEAN: {train_loss.mean():.2f}<br>\n"
        "<br>\n"
        "INIT TEST loss:<br>\n"
        f"&nbsp;&nbsp;MIN seq: {metrics['init_test_loss'].min():.2f}<br>\n"
        f"&nbsp;&nbsp;MAX seq: {metrics['init_test_loss'].max():.2f}<br>\n"
        f"&nbsp;&nbsp;MEAN: {metrics['init_test_loss'].mean():.2f}<br>\n"
        "<br>\n"
        "LAST TEST loss:<br>\n"
        f"&nbsp;&nbsp;MIN seq: {metrics['test_loss'].min():.2f}<br>\n"
        f"&nbsp;&nbsp;MAX seq: {metrics['test_loss'].max():.2f}<br>\n"
        f"&nbsp;&nbsp;MEAN: {metrics['test_loss'].mean():.2f}</p>\n"
        )

    if validate_inter:
        for i, (loss, J) in enumerate(zip(torch.stack(metrics['val_loss_hist']).mean(dim=0), torch.stack(metrics['val_J_hist']).mean(dim=0))):
            vis_dict['val_metrics'].plot([loss, J], i + 1)

        # patience_metrics = {n: [] for n in ['loss', 'acc', 'J', 'F']}
        # for patience in range(1, num_epochs // validate_inter):
        #     stopped_metrics = {n: [] for n in ['loss', 'acc', 'J', 'F']}

        #     for t_l_h, v_l_h, v_a_h, v_J_h, v_F_h in zip(metrics['train_loss_hist'], metrics['val_loss_hist'], metrics['val_acc_hist'], metrics['val_J_hist'], metrics['val_F_hist']):
        #         for epoch in range(patience, num_epochs // validate_inter):
        #             current_t_l_h = t_l_h[:epoch + 1]
        #             best_loss = current_t_l_h.min()
        #             prev_best_loss = current_t_l_h[:-patience].min()
        #             if not torch.gt(best_loss.sub(prev_best_loss).abs(), 0.0):
        #                 break

        #         stopped_metrics['loss'].append(v_l_h[epoch])
        #         stopped_metrics['acc'].append(v_a_h[epoch])
        #         stopped_metrics['J'].append(v_J_h[epoch])
        #         stopped_metrics['F'].append(v_F_h[epoch])

        #     for n, m in stopped_metrics.items():
        #         patience_metrics[n].append(torch.tensor(m).mean())

        # patience_metrics = {n: torch.tensor(m)
        #                     for n, m in patience_metrics.items()}

        # results_str += (
        #     f"<p>BEST VAL PATIENCE:<br>\n"
        #     f"&nbsp;&nbsp;LOSS: {(patience_metrics['loss'].argmin() + 1) * validate_inter} ({patience_metrics['loss'].min():.2f})<br>\n"
        #     f"&nbsp;&nbsp;ACC: {(patience_metrics['acc'].argmax() + 1) * validate_inter} ({patience_metrics['acc'].max():.2f})<br>\n"
        #     f"&nbsp;&nbsp;J: {(patience_metrics['J'].argmax() + 1) * validate_inter} ({patience_metrics['J'].max():.2f})<br>\n"
        #     f"&nbsp;&nbsp;F: {(patience_metrics['F'].argmax() + 1) * validate_inter} ({patience_metrics['F'].max():.2f})</p>\n")

        val_metrics = {n: m for n, m in metrics.items() if 'val' in n}
        for n, m in val_metrics.items():
            min_epoch = min([len(mm) for mm in m])
            m = torch.tensor([mm[:min_epoch].numpy() for mm in m])

            if 'loss' in n:
                best_mean_m_epoch = m.mean(dim=0).argmin()
            else:
                best_mean_m_epoch = m.mean(dim=0).argmax()

            results_str += (
                f"<p>BEST VAL {n.split('_')[1]}:<br>\n"
                f"&nbsp;&nbsp;EPOCH: {(best_mean_m_epoch + 1) * validate_inter}<br>\n"
                f"&nbsp;&nbsp;MIN seq: {m[..., best_mean_m_epoch].min():.2f}<br>\n"
                f"&nbsp;&nbsp;MAX seq: {m[..., best_mean_m_epoch].max():.2f}<br>\n"
                f"&nbsp;&nbsp;MEAN: {m[..., best_mean_m_epoch].mean():.2f}</p>\n")

    results_str += "<p>SEQUENCES:<br>\n"
    for seq_name, t_l_h, i_t_l, t_l, t_J, t_F, t_acc in zip(train_loader.dataset.seqs_names, metrics['train_loss_hist'], metrics['init_test_loss'], metrics['test_loss'], metrics['test_J'], metrics['test_F'], metrics['test_acc']):
        results_str += (f"{seq_name}<br>\n"
                        f"LOSS RUN TRAIN/INIT TEST/TEST: {t_l_h.mean():.2f}/{i_t_l:.2f}/{t_l:.2f}<br>\n"
                        f"TEST J/F/ACC: {t_J:.2f}/{t_F:.2f}/{t_acc:.2f}</p>\n")

    if vis_interval is None:
        print(results_str)
    else:
        vis_dict['config_vis'].plot(dict_to_html(_config) + results_str)
