#!/usr/bin/env python3
"""
Edge inference loader — minimal runtime for Raspberry Pi 5 (ARM64).

Takes a single observation vector, applies normalization, runs the
exported TorchScript or ONNX policy, and returns the safe action.

NOTE: The exported WrappedPolicy already bakes normalisation into the
forward pass.  Pass ``--skip-norm`` (default) to avoid double-normalising.
Only use ``--norm`` when loading a *raw* (un-wrapped) checkpoint.

Usage:
    python edge_inference.py --model policy.torchscript --obs "[0.5,4,1.2,...]"
    python edge_inference.py --model raw_policy.onnx --norm norm_params.npz --obs "[0.5,4,1.2,...]"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def load_torchscript(path: str):
    import torch
    model = torch.jit.load(path, map_location="cpu")
    model.eval()
    return model, "torchscript"


def load_onnx(path: str):
    import onnxruntime as ort
    sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    return sess, "onnx"


def load_norm(path: str):
    d = np.load(path)
    return d["means"].astype(np.float32), np.clip(d["stds"].astype(np.float32), 1e-8, None)


def safety_clip(action_kw: float, soc: float, soc_cap: float,
                soc_min_frac=0.10, soc_max_frac=0.95,
                max_charge=3.0, max_discharge=3.0) -> float:
    """Minimal safety clip."""
    capped = np.clip(action_kw, -max_discharge, max_charge)
    dt = 5.0 / 60.0
    new_soc = soc + capped * dt
    if new_soc < soc_min_frac * soc_cap:
        capped = (soc_min_frac * soc_cap - soc) / dt
    elif new_soc > soc_max_frac * soc_cap:
        capped = (soc_max_frac * soc_cap - soc) / dt
    return float(capped)


DISCRETE_ACTIONS = {
    0: ("charge_small",    +1.0),
    1: ("charge_large",    +3.0),
    2: ("idle",             0.0),
    3: ("discharge_small", -1.0),
    4: ("discharge_large", -3.0),
    5: ("offer_sell",      -1.5),
    6: ("offer_hold",       0.0),
}


def infer(model, model_type: str, obs: np.ndarray) -> dict:
    """Run inference and return action dict."""
    if model_type == "torchscript":
        import torch
        with torch.no_grad():
            t = torch.as_tensor(obs, dtype=torch.float32).unsqueeze(0)
            logits = model(t).squeeze(0).numpy()
    else:
        logits = model.run(None, {"observation": obs.reshape(1, -1)})[0].squeeze(0)

    action_idx = int(np.argmax(logits))
    name, kw = DISCRETE_ACTIONS.get(action_idx, ("idle", 0.0))
    return {
        "action_index": action_idx,
        "action_name": name,
        "action_kw": kw,
        "logits": logits.tolist(),
    }


def main():
    parser = argparse.ArgumentParser(description="Grid-Guardian edge inference")
    parser.add_argument("--model", required=True, help="Path to .torchscript or .onnx model")
    parser.add_argument("--norm", default=None,
                        help="Path to norm_params.npz (only for un-wrapped models)")
    parser.add_argument("--skip-norm", action="store_true", default=True,
                        help="Skip manual normalisation (model has baked-in norm). Default True.")
    parser.add_argument("--apply-norm", dest="skip_norm", action="store_false",
                        help="Force manual normalisation from --norm file.")
    parser.add_argument("--obs", default=None, help='JSON array of observation values')
    parser.add_argument("--soc", type=float, default=2.0)
    parser.add_argument("--soc_cap", type=float, default=4.0)
    parser.add_argument("--safety", action="store_true", help="Apply safety clip")
    args = parser.parse_args()

    # load model
    path = args.model
    if path.endswith(".onnx"):
        model, mtype = load_onnx(path)
    else:
        model, mtype = load_torchscript(path)

    # observation
    if args.obs:
        obs = np.array(json.loads(args.obs), dtype=np.float32)
    else:
        # demo: random observation
        obs = np.random.randn(18).astype(np.float32)

    # normalize — skip by default because WrappedPolicy includes normalisation
    if not args.skip_norm and args.norm and Path(args.norm).exists():
        means, stds = load_norm(args.norm)
        obs = (obs - means) / stds

    result = infer(model, mtype, obs)

    # safety clip
    if args.safety:
        result["action_kw"] = safety_clip(
            result["action_kw"], args.soc, args.soc_cap)

    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    main()
