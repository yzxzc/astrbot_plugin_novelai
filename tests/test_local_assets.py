"""Tests for local Danbooru vocabulary and WD tagger asset installation."""

import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from PIL import Image

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

import image_tagger  # noqa: E402
import tag_cache  # noqa: E402


@pytest.mark.asyncio
async def test_tag_cache_update_builds_queryable_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Convert one downloaded CSV snapshot into an exact local lookup DB."""
    monkeypatch.setattr(tag_cache, "MIN_SNAPSHOT_TAGS", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        if "/api/datasets/" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {"path": "danbooru-2026-07-21.csv"},
                    {"path": "danbooru-2026-07-22.csv"},
                ],
            )
        return httpx.Response(
            200,
            content=(b"1girl,0,1000000,one_girl\nsolo,0,900000,solo_character\n"),
        )

    cache_path = tmp_path / "danbooru-tags.sqlite3"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        info = await tag_cache.update_danbooru_cache(cache_path, client)

    resolved, metadata = tag_cache.lookup_local_tags(
        {"one_girl", "solo"},
        cache_path,
    )
    assert info.snapshot_date == "2026-07-22"
    assert resolved == {"one_girl": "1girl", "solo": "solo"}
    assert metadata["1girl"].post_count == 1_000_000


@pytest.mark.asyncio
async def test_tagger_install_is_explicit_pinned_and_atomic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Install only the pinned ONNX and labels files into plugin data."""
    monkeypatch.setattr(image_tagger, "MIN_MODEL_BYTES", 4)
    monkeypatch.setattr(image_tagger, "MIN_LABEL_BYTES", 4)
    monkeypatch.setattr(image_tagger, "MIN_LABEL_COUNT", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("selected_tags.csv"):
            return httpx.Response(
                200,
                content=(
                    b"tag_id,name,category,count\n0,1girl,0,1000000\n1,solo,0,900000\n"
                ),
            )
        if request.url.path.endswith("model.onnx"):
            return httpx.Response(200, content=b"fake-onnx-model")
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        info = await image_tagger.install_tagger_model(tmp_path, client)

    assert image_tagger.TAGGER_REVISION in image_tagger.TAGGER_MODEL_URL
    assert info.model_path.read_bytes() == b"fake-onnx-model"
    assert info.label_count == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_tagger_uses_bounded_sequential_cpu_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep local inference thread use bounded and deterministic."""
    captured: dict[str, object] = {}

    class FakeSessionOptions:
        """Store the ONNX options configured by the tagger."""

        intra_op_num_threads = 0
        inter_op_num_threads = 0
        execution_mode = None

    class FakeInferenceSession:
        """Return deterministic predictions without loading an ONNX model."""

        def __init__(
            self,
            model_path: str,
            sess_options: FakeSessionOptions,
            providers: list[str],
        ) -> None:
            captured["model_path"] = model_path
            captured["options"] = sess_options
            captured["providers"] = providers

        def get_inputs(self) -> list[SimpleNamespace]:
            """Return the NHWC input metadata expected by the tagger."""
            return [SimpleNamespace(name="input", shape=[1, 4, 4, 3])]

        def get_outputs(self) -> list[SimpleNamespace]:
            """Return one output tensor description."""
            return [SimpleNamespace(name="output")]

        def run(
            self,
            output_names: list[str],
            inputs: dict[str, np.ndarray],
        ) -> list[np.ndarray]:
            """Return one general and one character prediction.

            Args:
                output_names: Requested model output names.
                inputs: Prepared model input tensors.

            Returns:
                One batch containing two tag probabilities.
            """
            assert output_names == ["output"]
            assert inputs["input"].shape == (1, 4, 4, 3)
            return [np.asarray([[0.31, 0.90]], dtype=np.float32)]

    sequential_mode = object()
    fake_ort = SimpleNamespace(
        SessionOptions=FakeSessionOptions,
        ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL=sequential_mode),
        InferenceSession=FakeInferenceSession,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", fake_ort)

    model_path = tmp_path / "model.onnx"
    labels_path = tmp_path / "selected_tags.csv"
    image_path = tmp_path / "reference.png"
    model_path.write_bytes(b"test-model")
    labels_path.write_text(
        "tag_id,name,category,count\n0,chibi,0,1\n1,test_character,4,1\n",
        encoding="utf-8",
    )
    Image.new("RGB", (8, 4), "white").save(image_path)
    tagger = image_tagger.LocalImageTagger(
        image_tagger.ImageTaggerInfo(
            model_path=model_path,
            labels_path=labels_path,
            model_bytes=model_path.stat().st_size,
            label_count=2,
        )
    )

    tags = tagger.tag(image_path)

    options = captured["options"]
    assert isinstance(options, FakeSessionOptions)
    assert options.intra_op_num_threads == image_tagger.TAGGER_INTRA_OP_THREADS
    assert options.inter_op_num_threads == image_tagger.TAGGER_INTER_OP_THREADS
    assert options.execution_mode is sequential_mode
    assert captured["providers"] == ["CPUExecutionProvider"]
    assert tags == ("test character", "chibi")
