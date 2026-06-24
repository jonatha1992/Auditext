import sys
import os

if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
    # Diarizer uses relative savedir="pretrained_models/..." — must run from exe dir.
    os.chdir(_base)
    try:
        import torch
        torch.hub.set_dir(os.path.join(_base, "torch_hub"))
    except Exception:
        pass
