import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, random_split
from models.image_net import ImagePolicyModel
from dataloader.dataset import SampleData
from omegaconf import DictConfig, OmegaConf
import hydra
from tqdm import tqdm
import wandb
import random
import numpy as np
import datetime

# os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)

def train_epoch(loader, model, loss_fn, optimizer, device, epoch, log_to_wandb):
    model.train()
    total_loss = 0.0

    loop = tqdm(loader, desc=f"Epoch {epoch+1} [Train]", leave=False)
    for batch_idx, (obs, act) in enumerate(loop):
        obs = [x.to(device) for x in obs]
        act = [x.to(device) for x in act]

        pred = model(*obs)
        loss = loss_fn(pred, *act)

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        total_loss += loss.item()
        loop.set_postfix(loss=loss.item())

        if log_to_wandb:
            wandb.log({"train/loss": loss.item()}, step=epoch * len(loader) + batch_idx)

    return total_loss / len(loader)

def validate_epoch(loader, model, loss_fn, device, epoch, log_to_wandb):
    model.eval()
    total_loss = 0.0

    with torch.inference_mode():
        loop = tqdm(loader, desc=f"Epoch {epoch+1} [Val]", leave=False)
        for batch_idx, (obs, act) in enumerate(loop):
            obs = [x.to(device) for x in obs]
            act = [x.to(device) for x in act]

            pred = model(*obs)
            loss = loss_fn(pred, *act)
            total_loss += loss.item()
            loop.set_postfix(loss=loss.item())

    return total_loss / len(loader)

@hydra.main(config_path="../configs", config_name="train_config", version_base="1.3")
def main(cfg: DictConfig):
    set_seed(cfg.seed)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if cfg.wandb.log:
        wandb.init(project=cfg.wandb.project, name=cfg.wandb.name, config=OmegaConf.to_container(cfg))

    dataset = SampleData(
        file_path=cfg.data.file_path,
        obs_horizon=cfg.data.obs_horizon,
        act_horizon=cfg.data.act_horizon,
        gap=cfg.data.gap,
        obs_stride=cfg.data.obs_stride,
        act_stride=cfg.data.act_stride,
        obs_keys=cfg.data.obs_keys,
        act_keys=cfg.data.act_keys
    )

    val_size = int(len(dataset) * cfg.data.val_ratio)
    train_size = len(dataset) - val_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])

    train_loader = DataLoader(train_dataset, batch_size=cfg.data.batch_size, shuffle=True,
                              num_workers=cfg.data.num_workers, pin_memory=True)
    val_loader = DataLoader(val_dataset, batch_size=cfg.data.batch_size, shuffle=False,
                            num_workers=cfg.data.num_workers, pin_memory=True)

    model = ImagePolicyModel(backbone=cfg.model.backbone, pretrained=cfg.model.pretrained, steps=cfg.model.steps, commands=cfg.model.commands).to(device)
    if cfg.train.use_compile:
        model = torch.compile(model)

    loss_fn = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=cfg.train.lr)

    for epoch in range(cfg.train.epochs):
        print(f"\n Epoch {epoch+1}/{cfg.train.epochs}")
        train_loss = train_epoch(train_loader, model, loss_fn, optimizer, device, epoch, cfg.wandb.log)
        val_loss = validate_epoch(val_loader, model, loss_fn, device, epoch, cfg.wandb.log)

        print(f" Train Loss: {train_loss:.6f} | Val Loss: {val_loss:.6f}")

        if cfg.train.epoch_save:
            os.makedirs(os.path.dirname(f'checkpoints/{datetime.datetime.now().strftime("%m%d_%H%M")}_epoch{epoch+1}.pth'), exist_ok=True)
            torch.save(model.state_dict(), f'checkpoints/{datetime.datetime.now().strftime("%m%d_%H%M")}_epoch{epoch+1}.pth')
            print(f"\nCheckpoint saved successfully.")

        if cfg.wandb.log:
            wandb.log({
                "epoch": epoch + 1,
                "train/avg_loss": train_loss,
                "val/avg_loss": val_loss
            })

    print("Training complete.")

    if cfg.train.save:
        os.makedirs(os.path.dirname(f'checkpoints/{datetime.datetime.now().strftime("%m%d_%H%M")}_model.pth'), exist_ok=True)
        torch.save(model.state_dict(), f'checkpoints/{datetime.datetime.now().strftime("%m%d_%H%M")}_model.pth')
        print(f"\nModel saved successfully.")

    if cfg.wandb.log:
        wandb.finish()


if __name__ == "__main__":
    print("PyTorch Version:", torch.__version__)
    print("CUDA Available:", torch.cuda.is_available())
    print("CUDA Version:", torch.version.cuda)
    print("cuDNN Version:", torch.backends.cudnn.version())
    print("Device Count:", torch.cuda.device_count())
    main()
