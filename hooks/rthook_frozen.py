import sys
import os

if getattr(sys, "frozen", False):
    _base = os.path.dirname(sys.executable)
    # ctypes.util.find_library (used by cffi.dlopen) on Windows 3.11
    # only searches os.environ["PATH"] directories.  In a frozen build
    # with console=False, PATH may not include System32, preventing
    # resolution of standard system DLLs.
    _system32 = os.path.join(os.environ.get("SYSTEMROOT", "C:\\Windows"), "System32")
    os.environ["PATH"] = _system32 + os.pathsep + os.environ.get("PATH", "")
    # Diarizer uses relative savedir="pretrained_models/..." — must run from exe dir.
    os.chdir(_base)
    try:
        import torch
        torch.hub.set_dir(os.path.join(_base, "torch_hub"))
    except Exception:
        pass
