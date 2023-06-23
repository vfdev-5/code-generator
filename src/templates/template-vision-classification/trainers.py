from typing import Any, Union

import ignite.distributed as idist
import torch

#::: if(it.deterministic) { :::#
from ignite.engine import DeterministicEngine, Engine, Events  # usort: skip

#::: } else { :::#
from ignite.engine import Engine, Events

#::: } :::#
from torch.cuda.amp import autocast, GradScaler
from torch.nn import Module
from torch.optim import Optimizer
from torch.utils.data import DistributedSampler, Sampler


def setup_trainer(
    config: Any,
    model: Module,
    optimizer: Optimizer,
    loss_fn: Module,
    device: Union[str, torch.device],
    train_sampler: Sampler,
) -> Union[Engine, DeterministicEngine]:
    scaler = GradScaler(enabled=config.use_amp)

    def train_function(engine: Union[Engine, DeterministicEngine], batch: Any):
        model.train()

        x = batch[0].to(device, non_blocking=True)
        y = batch[1].to(device, non_blocking=True)

        with autocast(config.use_amp):
            y_pred = model(x)
            loss = loss_fn(y_pred, y) / config.accumulation_steps

        scaler.scale(loss).backward()
        if engine.state.iteration % config.accumulation_steps == 0:
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad()

        train_loss = loss.item()
        engine.state.metrics = {
            "epoch": engine.state.epoch,
            "train_loss": train_loss,
        }
        return {"train_loss": train_loss}

    #
    #::: if(it.deterministic) { :::#
    trainer = DeterministicEngine(train_function)
    #::: } else { :::#
    trainer = Engine(train_function)
    #::: } :::#

    # set epoch for distributed sampler
    @trainer.on(Events.EPOCH_STARTED)
    def set_epoch():
        if idist.get_world_size() > 1 and isinstance(train_sampler, DistributedSampler):
            train_sampler.set_epoch(trainer.state.epoch - 1)

    return trainer


def setup_evaluator(
    config: Any,
    model: Module,
    device: Union[str, torch.device],
) -> Engine:
    @torch.no_grad()
    def eval_function(engine: Engine, batch: Any):
        model.eval()

        x = batch[0].to(device, non_blocking=True)
        y = batch[1].to(device, non_blocking=True)

        with autocast(config.use_amp):
            y_pred = model(x)

        return y_pred, y

    return Engine(eval_function)
