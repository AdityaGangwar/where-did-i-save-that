"""Offline text-to-image search using a quantized CLIP ONNX model."""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from tokenizers import Tokenizer

register_heif_opener()

MODEL_REPO = "Xenova/clip-vit-base-patch32"
MODEL_REVISION = "d15189d7028b43f1d3e65039190477f6af591c2a"
MODEL_BYTES = 153_695_702
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff", ".heic", ".heif"}
IMAGE_SIZE = 224
TOKEN_LIMIT = 77
PAD_TOKEN_ID = 49407
IMAGE_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
IMAGE_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


class ImageSearchEngine:
    def __init__(self, model_dir: Path, progress=None):
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.model_path = self.model_dir / "model_quantized.onnx"
        self.tokenizer_path = self.model_dir / "tokenizer.json"
        self._download_if_needed("onnx/model_quantized.onnx", self.model_path, MODEL_BYTES, progress)
        self._download_if_needed("tokenizer.json", self.tokenizer_path, 2_300_000, progress)
        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.tokenizer.enable_truncation(max_length=TOKEN_LIMIT)
        self.tokenizer.enable_padding(length=TOKEN_LIMIT, pad_id=PAD_TOKEN_ID, pad_token="<|endoftext|>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"])

    def _download_if_needed(self, filename: str, destination: Path, expected_size: int, progress) -> None:
        if destination.exists() and destination.stat().st_size > 1_000_000:
            return
        url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/{filename}"
        partial = destination.with_suffix(destination.suffix + ".part")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "WhereDidISaveThat/0.2"})
            with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
                total_header = response.headers.get("Content-Length")
                total = int(total_header) if total_header and total_header.isdigit() else expected_size
                done = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(filename, done, total)
            if partial.stat().st_size < 1_000_000:
                raise RuntimeError(f"The downloaded model file was unexpectedly small: {filename}")
            partial.replace(destination)
        except Exception:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _token_inputs(self, text: str) -> tuple[np.ndarray, np.ndarray]:
        encoded = self.tokenizer.encode(text.strip() or "photo")
        ids = np.asarray([encoded.ids], dtype=np.int64)
        mask = np.asarray([encoded.attention_mask], dtype=np.int64)
        return ids, mask

    def _run(self, ids: np.ndarray, mask: np.ndarray, pixels: np.ndarray, output: str) -> np.ndarray:
        result = self.session.run([output], {"input_ids": ids, "attention_mask": mask, "pixel_values": pixels})[0][0]
        vector = np.asarray(result, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        if norm == 0:
            return vector
        return vector / norm

    def embed_text(self, text: str) -> np.ndarray:
        ids, mask = self._token_inputs(text)
        blank_pixels = np.zeros((1, 3, IMAGE_SIZE, IMAGE_SIZE), dtype=np.float32)
        return self._run(ids, mask, blank_pixels, "text_embeds")

    @staticmethod
    def _prepare_image(path: Path) -> np.ndarray:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                rgba = image.convert("RGBA")
                background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
                image = Image.alpha_composite(background, rgba).convert("RGB")
            else:
                image = image.convert("RGB")
            width, height = image.size
            if width < 1 or height < 1:
                raise ValueError("Image has no pixels")
            scale = IMAGE_SIZE / min(width, height)
            new_size = (max(IMAGE_SIZE, round(width * scale)), max(IMAGE_SIZE, round(height * scale)))
            image = image.resize(new_size, Image.Resampling.BICUBIC)
            left = (image.width - IMAGE_SIZE) // 2
            top = (image.height - IMAGE_SIZE) // 2
            image = image.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))
            pixels = np.asarray(image, dtype=np.float32) / 255.0
            pixels = (pixels - IMAGE_MEAN) / IMAGE_STD
            return np.transpose(pixels, (2, 0, 1))[None, ...].astype(np.float32, copy=False)

    def embed_image(self, path: Path) -> np.ndarray:
        pixels = self._prepare_image(Path(path))
        ids, mask = self._token_inputs("a photograph")
        return self._run(ids, mask, pixels, "image_embeds")

