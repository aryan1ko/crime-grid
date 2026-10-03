"""Vehicle appearance embeddings for cross-camera re-identification.

Re-ID is the hard part of this project: deciding that a car seen at camera B is
the *same* car selected at camera A, despite a different angle, lighting and
resolution. We combine two signals:

  1. A deep appearance embedding (cosine similarity). By default this is a
     torchvision ResNet-50 backbone (ImageNet-pretrained, classifier removed).
     If `torchreid` is installed, its OSNet vehicle/person re-ID model is used
     instead — it is trained specifically for re-ID and matches noticeably
     better. Set RELAY_REID_BACKEND=torchreid to force it.

  2. An HSV colour histogram, as a cheap, lighting-robust tie-breaker.

Everything degrades gracefully: if torch is unavailable the module still loads
and `available` is False, so the rest of the app runs (just without re-ID).
"""

from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger("relay.reid")

_HIST_BINS = (8, 8, 8)  # H, S, V


class ReID:
    def __init__(self) -> None:
        self._backend = os.environ.get("RELAY_REID_BACKEND", "auto")
        self._model = None
        self._transform = None
        self._torch = None
        self.kind = "none"
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        try:
            import torch
            self._torch = torch
        except Exception as exc:  # torch not installed
            log.warning("re-ID disabled (torch unavailable): %s", exc)
            return

        if self._backend in ("auto", "torchreid"):
            if self._try_torchreid():
                return
            if self._backend == "torchreid":
                log.warning("torchreid requested but unavailable; using resnet")
        self._load_resnet()

    def _try_torchreid(self) -> bool:
        try:
            import torchreid  # type: ignore
            from torchreid.utils import FeatureExtractor  # type: ignore
        except Exception:
            return False
        try:
            self._extractor = FeatureExtractor(
                model_name="osnet_x1_0", device="cpu"
            )
            self.kind = "torchreid-osnet"
            log.info("re-ID backend: torchreid OSNet")
            return True
        except Exception as exc:
            log.warning("torchreid load failed: %s", exc)
            return False

    def _load_resnet(self) -> None:
        import torch
        from torchvision import transforms
        from torchvision.models import ResNet50_Weights, resnet50

        weights = ResNet50_Weights.IMAGENET1K_V2
        net = resnet50(weights=weights)
        net.fc = torch.nn.Identity()  # expose the 2048-d pooled features
        net.eval()
        self._model = net
        self._transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406],
                                  std=[0.229, 0.224, 0.225]),
        ])
        self.kind = "resnet50"
        log.info("re-ID backend: torchvision ResNet-50")

    @property
    def available(self) -> bool:
        return self.kind != "none"

    # ------------------------------------------------------------------ #
    def embed(self, crop_bgr: np.ndarray) -> list[float] | None:
        """Return an L2-normalized appearance vector for a BGR crop."""
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        if self.kind == "torchreid-osnet":
            try:
                import cv2
                rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
                feat = self._extractor(rgb)  # (1, D) tensor
                vec = feat.cpu().numpy().ravel().astype(np.float32)
                return _l2(vec).tolist()
            except Exception as exc:
                log.debug("torchreid embed failed: %s", exc)
                return None
        if self._model is not None:
            try:
                import cv2
                torch = self._torch
                rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
                x = self._transform(rgb).unsqueeze(0)
                with torch.no_grad():
                    feat = self._model(x).numpy().ravel().astype(np.float32)
                return _l2(feat).tolist()
            except Exception as exc:
                log.debug("resnet embed failed: %s", exc)
                return None
        return None

    def color_hist(self, crop_bgr: np.ndarray) -> list[float] | None:
        if crop_bgr is None or crop_bgr.size == 0:
            return None
        try:
            import cv2
            hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1, 2], None, _HIST_BINS,
                                [0, 180, 0, 256, 0, 256])
            hist = hist.astype(np.float32).ravel()
            s = hist.sum()
            if s > 0:
                hist /= s
            return hist.tolist()
        except Exception:
            return None


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def similarity(
    target_embed: list[float] | None,
    target_hist: list[float] | None,
    cand_embed: list[float] | None,
    cand_hist: list[float] | None,
    embed_weight: float,
) -> float:
    """Combined similarity in [0, 1]. Falls back to whichever signal exists."""
    sims: list[tuple[float, float]] = []  # (weight, score)
    if target_embed and cand_embed:
        a, b = np.asarray(target_embed), np.asarray(cand_embed)
        cos = float(np.dot(a, b))  # both are L2-normalized
        sims.append((embed_weight, max(0.0, cos)))
    if target_hist and cand_hist:
        a, b = np.asarray(target_hist), np.asarray(cand_hist)
        # Bhattacharyya-style overlap: sum of elementwise sqrt products.
        overlap = float(np.sum(np.sqrt(np.clip(a * b, 0, None))))
        sims.append((1.0 - embed_weight, overlap))
    if not sims:
        return 0.0
    wsum = sum(w for w, _ in sims)
    return sum(w * s for w, s in sims) / wsum if wsum > 0 else 0.0
