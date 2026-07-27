"""Tests for hardware detection and tier selection."""

from __future__ import annotations

from unittest.mock import patch

from app.core.hardware import HardwareProfile, detect


class TestDetectCPU:
    def test_no_gpu_returns_cpu_tier(self):
        """No NVIDIA GPU → cpu tier with bge-m3 defaults."""
        with patch("app.core.hardware._detect_nvidia_gpu", return_value=("", 0)):
            with patch("app.core.hardware._try_torch_cuda", return_value=("", 0)):
                hw = detect()

        assert hw.tier == "cpu"
        assert hw.vram_mb == 0
        assert hw.embedding_model == "bge-m3"
        assert hw.reranker_model == "BAAI/bge-reranker-base"
        assert hw.vector_dim == 1024


class TestDetectGPU:
    def test_8gb_gpu_returns_gpu_8g_tier(self):
        """8GB GPU → gpu-8g tier with Qwen3-4B."""
        with patch("app.core.hardware._detect_nvidia_gpu", return_value=("RTX 4060", 8000)):
            hw = detect()

        assert hw.tier == "gpu-8g"
        assert hw.vram_mb == 8000
        assert "Qwen3-Embedding-4B" in hw.embedding_model
        assert "Qwen3-Reranker-4B" in hw.reranker_model
        assert hw.vector_dim == 2048

    def test_16gb_gpu_returns_gpu_16g_tier(self):
        """16GB GPU → gpu-16g tier with Qwen3-8B."""
        with patch("app.core.hardware._detect_nvidia_gpu", return_value=("RTX 4090", 16000)):
            hw = detect()

        assert hw.tier == "gpu-16g"
        assert hw.vram_mb == 16000
        assert "Qwen3-Embedding-8B" in hw.embedding_model
        assert "Qwen3-Reranker-8B" in hw.reranker_model
        assert hw.vector_dim == 4096

    def test_24gb_gpu_returns_gpu_16g_tier(self):
        """24GB GPU → still gpu-16g (highest tier)."""
        with patch("app.core.hardware._detect_nvidia_gpu", return_value=("A6000", 48000)):
            hw = detect()

        assert hw.tier == "gpu-16g"
        assert "Qwen3-Embedding-8B" in hw.embedding_model


class TestDetectEdgeCases:
    def test_torch_cuda_fallback(self):
        """When nvidia-smi fails, try PyTorch CUDA."""
        with patch("app.core.hardware._detect_nvidia_gpu", return_value=("", 0)):
            with patch("app.core.hardware._try_torch_cuda", return_value=("RTX A5000", 24000)):
                hw = detect()

        assert hw.tier == "gpu-16g"
        assert hw.vram_mb == 24000

    def test_both_fail_returns_cpu(self):
        """Both detection methods fail → cpu tier."""
        with patch("app.core.hardware._detect_nvidia_gpu", return_value=("", 0)):
            with patch("app.core.hardware._try_torch_cuda", return_value=("", 0)):
                hw = detect()

        assert hw.tier == "cpu"


class TestHardwareProfile:
    def test_dataclass_fields(self):
        hw = HardwareProfile(
            tier="cpu", gpu_name="", vram_mb=0,
            embedding_model="bge-m3", reranker_model="BGE-base", vector_dim=1024,
        )
        assert hw.tier == "cpu"
        assert hw.embedding_model == "bge-m3"
