from typing import List

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor
from torch.nn import Conv1d
from torch.nn.utils import weight_norm
from torch.nn.utils import spectral_norm

from .pqmf import PQMF
from .utils import get_padding


class CoMBDBlock(torch.nn.Module):
    def __init__(
        self,
        h_u: List[int],
        d_k: List[int],
        d_s: List[int],
        d_d: List[int],
        d_g: List[int],
        d_p: List[int],
        op_f: int,
        op_k: int,
        op_g: int,
        use_spectral_norm=False
    ):
        super(CoMBDBlock, self).__init__()
        norm_f = weight_norm if use_spectral_norm is False else spectral_norm

        self.convs = nn.ModuleList()
        filters = [[1, h_u[0]]]
        for i in range(len(h_u) - 1):
            filters.append([h_u[i], h_u[i + 1]])
        for _f, _k, _s, _d, _g, _p in zip(filters, d_k, d_s, d_d, d_g, d_p):
            self.convs.append(norm_f(
                Conv1d(
                    in_channels=_f[0],
                    out_channels=_f[1],
                    kernel_size=_k,
                    stride=_s,
                    dilation=_d,
                    groups=_g,
                    padding=_p
                )
            ))
        self.projection_conv = norm_f(
            Conv1d(
                in_channels=filters[-1][1],
                out_channels=op_f,
                kernel_size=op_k,
                groups=op_g
            )
        )

    def forward(self, x):
        fmap = []
        for block in self.convs:
            x = block(x)
            x = F.leaky_relu(x, 0.2)
            fmap.append(x)
        x = self.projection_conv(x)
        return x, fmap


class CoMBD(torch.nn.Module):
    def __init__(self, h, use_spectral_norm=False):
        super(CoMBD, self).__init__()
        self.h = h
        self.pqmf = nn.ModuleList([
            PQMF(*h.pqmf_config["lv2"]),
            PQMF(*h.pqmf_config["lv1"])
        ])

        self.blocks = nn.ModuleList()
        for _h_u, _d_k, _d_s, _d_d, _d_g, _d_p, _op_f, _op_k, _op_g in zip(
            h.combd_h_u,
            h.combd_d_k,
            h.combd_d_s,
            h.combd_d_d,
            h.combd_d_g,
            h.combd_d_p,
            h.combd_op_f,
            h.combd_op_k,
            h.combd_op_g,
        ):
            self.blocks.append(CoMBDBlock(
                _h_u,
                _d_k,
                _d_s,
                _d_d,
                _d_g,
                _d_p,
                _op_f,
                _op_k,
                _op_g,
            ))

    def _block_forward(self, input, blocks, outs, f_maps):
        for x, block in zip(input, blocks):
            out, f_map = block(x)
            outs.append(out)
            f_maps.extend(f_map)
        return outs, f_maps

    def forward(self, ys):
        # preprocess for multi_scale forward
        multi_scale_inputs = []
        for pqmf in self.pqmf:
            multi_scale_inputs.append(
                pqmf.analysis(ys[-1])[:, :1, :]
            )

        # for hierarchical forward
        outs, f_maps = self._block_forward(
            ys, self.blocks, [], [])
        # for multi_scale forward
        outs, f_maps = self._block_forward(
            multi_scale_inputs, self.blocks[:-1], outs, f_maps)

        return outs, f_maps
