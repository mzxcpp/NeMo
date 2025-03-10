# coding=utf-8
# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import logging
from dataclasses import dataclass
from typing import Optional

import torch.nn as nn

from nemo.collections.common.parts.adapter_modules import AbstractAdapterModule
from nemo.collections.common.parts.utils import activation_registry
from nemo.collections.nlp.modules.common.megatron.transformer import ColumnLinear
from nemo.collections.nlp.modules.common.megatron.utils import init_method_const, init_method_normal
from nemo.core.classes.mixins import adapter_mixin_strategies

try:
    from apex.transformer.tensor_parallel import RowParallelLinear
    from apex.normalization.fused_layer_norm import MixedFusedLayerNorm

    HAVE_APEX = True

except (ImportError, ModuleNotFoundError):

    HAVE_APEX = False


class ParallelLinearAdapter(AbstractAdapterModule):
    def __init__(
        self,
        in_features: int,
        dim: int,
        activation: str = 'swish',
        norm_position: str = 'post',
        dropout: float = 0.0,
        adapter_strategy: adapter_mixin_strategies.ResidualAddAdapterStrategyConfig = None,
    ):
        super().__init__()
        if not HAVE_APEX:
            logging.info("Apex is required to use ParallelLinearAdapters.")
            raise RuntimeError("ParallelLinearAdapter can not run without Apex.")
        self.activation = activation_registry[activation]()
        self.norm_position = norm_position

        self.linear_in = ColumnLinear(in_features, dim, bias=False, init_method=init_method_normal(0.2))
        self.linear_out = RowParallelLinear(dim, in_features, bias=False, init_method=init_method_const(0.0))
        self.layer_norm = MixedFusedLayerNorm(in_features, 1e-5, sequence_parallel_enbaled=False)

        if dropout > 0.0:
            self.dropout = nn.Dropout(dropout)
        else:
            self.dropout = None

        # Setup adapter strategy
        self.setup_adapter_strategy(adapter_strategy)

    def forward(self, x):

        if self.norm_position == 'pre':
            x = self.layer_norm(x)

        x, _ = self.linear_in(x)  # (@adithyare) ColumnLinear returns output and bias, we are ignoring the bias term.
        x = self.activation(x)
        x, _ = self.linear_out(x)

        if self.norm_position == 'post':
            x = self.layer_norm(x)

        # Add dropout if available
        if self.dropout is not None:
            x = self.dropout(x)

        return x


@dataclass
class ParallelLinearAdapterConfig:
    in_features: int
    dim: int
    activation: str = 'swish'
    norm_position: str = 'post'
    dropout: float = 0.0
    adapter_strategy: Optional[dict] = adapter_mixin_strategies.ResidualAddAdapterStrategyConfig()
    _target_: str = "{0}.{1}".format(ParallelLinearAdapter.__module__, ParallelLinearAdapter.__name__)
