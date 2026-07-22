"""Run a local WD SwinV2 Danbooru image tagger through ONNX Runtime."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import httpx
from PIL import Image

TAGGER_REVISION = "627aef95638667ddcaa3ac8ae625e88ea5b02f51"
TAGGER_REPOSITORY = "SmilingWolf/wd-swinv2-tagger-v3"
TAGGER_BASE_URL = (
    f"https://huggingface.co/{TAGGER_REPOSITORY}/resolve/{TAGGER_REVISION}"
)
TAGGER_MODEL_URL = f"{TAGGER_BASE_URL}/model.onnx"
TAGGER_LABELS_URL = f"{TAGGER_BASE_URL}/selected_tags.csv"
MIN_MODEL_BYTES = 100 * 1024 * 1024
MAX_MODEL_BYTES = 600 * 1024 * 1024
MIN_LABEL_BYTES = 10_000
MAX_LABEL_BYTES = 2 * 1024 * 1024
MIN_LABEL_COUNT = 5_000
TAGGER_INTRA_OP_THREADS = 4
TAGGER_INTER_OP_THREADS = 1


@dataclass(frozen=True)
class ImageTaggerInfo:
    """Describe one installed local WD tagger model."""

    model_path: Path
    labels_path: Path
    model_bytes: int
    label_count: int


def read_tagger_info(data_dir: Path) -> ImageTaggerInfo | None:
    """Validate the two files required by the local tagger.

    Args:
        data_dir: Plugin-local tagger model directory.

    Returns:
        Installed model metadata, or ``None`` when files are incomplete.
    """
    model_path = data_dir / "model.onnx"
    labels_path = data_dir / "selected_tags.csv"
    try:
        model_bytes = model_path.stat().st_size
        label_bytes = labels_path.stat().st_size
        if not MIN_MODEL_BYTES <= model_bytes <= MAX_MODEL_BYTES:
            return None
        if not MIN_LABEL_BYTES <= label_bytes <= MAX_LABEL_BYTES:
            return None
        with labels_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = csv.DictReader(file)
            label_count = sum(
                1 for row in rows if row.get("name") and row.get("category") is not None
            )
        if label_count < MIN_LABEL_COUNT:
            return None
        return ImageTaggerInfo(
            model_path=model_path,
            labels_path=labels_path,
            model_bytes=model_bytes,
            label_count=label_count,
        )
    except (OSError, UnicodeDecodeError, csv.Error):
        return None


async def install_tagger_model(
    data_dir: Path,
    client: httpx.AsyncClient | None = None,
) -> ImageTaggerInfo:
    """Download the pinned WD tagger model and labels atomically.

    Args:
        data_dir: Plugin-local destination directory.
        client: Optional injected HTTP client for tests.

    Returns:
        Metadata for the installed model.

    Raises:
        RuntimeError: If a downloaded file has an unsafe size or format.
        httpx.HTTPError: If Hugging Face cannot be reached.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    owns_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(600.0),
        follow_redirects=True,
    )
    downloads = (
        (TAGGER_LABELS_URL, data_dir / "selected_tags.csv", MAX_LABEL_BYTES),
        (TAGGER_MODEL_URL, data_dir / "model.onnx", MAX_MODEL_BYTES),
    )
    temporary_paths = [
        destination.with_suffix(destination.suffix + ".tmp")
        for _, destination, _ in downloads
    ]
    try:
        for url, destination, maximum_bytes in downloads:
            temporary_path = destination.with_suffix(destination.suffix + ".tmp")
            temporary_path.unlink(missing_ok=True)
            received = 0
            try:
                async with http_client.stream(
                    "GET",
                    url,
                    headers={
                        "User-Agent": "AstrBot-NovelAI/3.2 (local WD tagger updater)"
                    },
                ) as response:
                    response.raise_for_status()
                    with temporary_path.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            received += len(chunk)
                            if received > maximum_bytes:
                                raise RuntimeError("Tagger 下载文件超过安全大小上限。")
                            output.write(chunk)
            except Exception:
                raise

        labels_temporary_path, model_temporary_path = temporary_paths
        if (
            not MIN_MODEL_BYTES
            <= model_temporary_path.stat().st_size
            <= (MAX_MODEL_BYTES)
        ):
            raise RuntimeError("Tagger ONNX 文件大小异常。")
        if (
            not MIN_LABEL_BYTES
            <= labels_temporary_path.stat().st_size
            <= (MAX_LABEL_BYTES)
        ):
            raise RuntimeError("Tagger 标签文件大小异常。")
        with labels_temporary_path.open("r", encoding="utf-8-sig", newline="") as file:
            rows = csv.DictReader(file)
            label_count = sum(
                1 for row in rows if row.get("name") and row.get("category") is not None
            )
        if label_count < MIN_LABEL_COUNT:
            raise RuntimeError("Tagger 标签数量异常。")
        labels_temporary_path.replace(data_dir / "selected_tags.csv")
        model_temporary_path.replace(data_dir / "model.onnx")
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
        if owns_client:
            await http_client.aclose()

    info = read_tagger_info(data_dir)
    if info is None:
        raise RuntimeError("Tagger 模型安装后完整性检查失败。")
    return info


class LocalImageTagger:
    """Keep one reusable ONNX session for Danbooru tag inference."""

    def __init__(self, info: ImageTaggerInfo) -> None:
        """Load model metadata without starting inference yet.

        Args:
            info: Validated local model file information.
        """
        self.info = info
        self._session = None
        self._input_name = ""
        self._output_name = ""
        self._target_size = 0
        self._labels: list[tuple[str, int]] = []

    def tag(
        self,
        image_path: Path,
        general_threshold: float = 0.30,
        character_threshold: float = 0.85,
        max_tags: int = 80,
    ) -> tuple[str, ...]:
        """Infer ordered general and character Danbooru tags for one image.

        Args:
            image_path: Local image path resolved by AstrBot.
            general_threshold: Minimum probability for category-0 tags.
            character_threshold: Minimum probability for category-4 tags.
            max_tags: Maximum number of returned tags.

        Returns:
            Highest-confidence tags with underscores converted to spaces.

        Raises:
            RuntimeError: If ONNX Runtime is missing or model output is invalid.
            ValueError: If thresholds or tag limits are outside safe bounds.
        """
        if not 0.05 <= general_threshold <= 0.95:
            raise ValueError("general_threshold must be between 0.05 and 0.95")
        if not 0.05 <= character_threshold <= 0.99:
            raise ValueError("character_threshold must be between 0.05 and 0.99")
        if not 1 <= max_tags <= 200:
            raise ValueError("max_tags must be between 1 and 200")

        try:
            import numpy as np
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("本地 Tagger 缺少 onnxruntime 或 numpy 依赖。") from exc

        if self._session is None:
            session_options = ort.SessionOptions()
            session_options.intra_op_num_threads = TAGGER_INTRA_OP_THREADS
            session_options.inter_op_num_threads = TAGGER_INTER_OP_THREADS
            session_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            self._session = ort.InferenceSession(
                str(self.info.model_path),
                sess_options=session_options,
                providers=["CPUExecutionProvider"],
            )
            model_input = self._session.get_inputs()[0]
            model_output = self._session.get_outputs()[0]
            try:
                self._target_size = int(model_input.shape[1])
            except (TypeError, ValueError, IndexError) as exc:
                raise RuntimeError("Tagger ONNX 输入尺寸无效。") from exc
            self._input_name = model_input.name
            self._output_name = model_output.name
            try:
                with self.info.labels_path.open(
                    "r", encoding="utf-8-sig", newline=""
                ) as file:
                    rows = csv.DictReader(file)
                    self._labels = [
                        (str(row["name"]), int(row["category"])) for row in rows
                    ]
            except (
                KeyError,
                OSError,
                UnicodeDecodeError,
                ValueError,
                csv.Error,
            ) as exc:
                raise RuntimeError("Tagger 标签文件无法读取。") from exc

        with Image.open(image_path) as source:
            source.load()
            rgba_image = source.convert("RGBA")
        canvas = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
        canvas.alpha_composite(rgba_image)
        image = canvas.convert("RGB")
        max_dimension = max(image.size)
        padded = Image.new("RGB", (max_dimension, max_dimension), (255, 255, 255))
        padded.paste(
            image,
            (
                (max_dimension - image.width) // 2,
                (max_dimension - image.height) // 2,
            ),
        )
        if max_dimension != self._target_size:
            padded = padded.resize(
                (self._target_size, self._target_size),
                Image.Resampling.BICUBIC,
            )
        image_array = np.asarray(padded, dtype=np.float32)[:, :, ::-1]
        predictions = self._session.run(
            [self._output_name],
            {self._input_name: np.expand_dims(image_array, axis=0)},
        )[0]
        if len(predictions) != 1 or len(predictions[0]) != len(self._labels):
            raise RuntimeError("Tagger ONNX 输出与标签数量不一致。")

        scored: list[tuple[float, str]] = []
        for (name, category), probability in zip(
            self._labels,
            predictions[0].astype(float),
            strict=True,
        ):
            threshold = (
                general_threshold
                if category == 0
                else character_threshold
                if category == 4
                else None
            )
            if threshold is not None and probability >= threshold:
                scored.append((float(probability), name.replace("_", " ")))
        scored.sort(reverse=True)
        return tuple(name for _, name in scored[:max_tags])
