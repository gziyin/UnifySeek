"""回归单测：embedding 离线缓存识别与离线判定（防 run 卡在在线连 HF Hub）。

覆盖 #run-stuck 根因：缓存目录名 org 前缀不匹配导致 has_local_cache=False，
进而走在线 SentenceTransformer(model_name) 连 huggingface.co，WinError 10060
5 次指数退避重试阻塞 run 启动。本测试固化缓存识别逻辑，防止复发。
"""

from __future__ import annotations

import pytest

from ai_dev_researcher.storage.embedding_provider import (
    DEFAULT_EMBEDDING_MODEL,
    SentenceTransformersProvider,
    _model_cache_dir_names,
)

ORG_QUALIFIED = "sentence-transformers/all-MiniLM-L6-v2"
ORG_DIR = "models--sentence-transformers--all-MiniLM-L6-v2"
BARE_DIR = "models--all-MiniLM-L6-v2"


def test_model_cache_dir_names_matches_org_qualified_cache_dir():
    """org 限定名精确映射为磁盘上带 org 的 models-- 缓存目录形态。"""
    candidates = _model_cache_dir_names(ORG_QUALIFIED)
    assert ORG_DIR in candidates


def test_model_cache_dir_names_matches_bare_name_too():
    """裸名（无 org）也能匹配 models--<name> 形态。"""
    candidates = _model_cache_dir_names(DEFAULT_EMBEDDING_MODEL)
    assert BARE_DIR in candidates


def test_model_cache_dir_exists_detects_org_dir(tmp_path):
    """给定缓存根下存在 models--sentence-transformers--all-MiniLM-L6-v2 时，
    _model_cache_dir_exists 无论传入 org 限定名还是裸名都应识别为存在。"""
    snapshots = tmp_path / ORG_DIR / "snapshots" / "abc123"
    snapshots.mkdir(parents=True)

    provider = SentenceTransformersProvider(model_name=ORG_QUALIFIED)
    assert provider._model_cache_dir_exists(str(tmp_path), ORG_QUALIFIED) is True
    # 裸名传入也能命中 org 限定缓存目录（org 前缀不匹配的兜底）。
    assert provider._model_cache_dir_exists(str(tmp_path), DEFAULT_EMBEDDING_MODEL) is True


def test_model_cache_dir_exists_false_when_missing(tmp_path):
    """缓存目录缺失时返回 False，触发在线/报错路径。"""
    provider = SentenceTransformersProvider(model_name=ORG_QUALIFIED)
    assert provider._model_cache_dir_exists(str(tmp_path / "nope"), ORG_QUALIFIED) is False


def test_resolve_hf_cache_dir_prefers_explicit_value(tmp_path, monkeypatch):
    """显式 hf_hub_cache 优先于环境变量与默认用户缓存。"""
    monkeypatch.setenv("HF_HUB_CACHE", "/env/cache")
    provider = SentenceTransformersProvider(
        model_name=ORG_QUALIFIED, hf_hub_cache=str(tmp_path)
    )
    assert provider._resolve_hf_cache_dir() == str(tmp_path)


def test_resolve_offline_true_when_flag_or_env(tmp_path, monkeypatch):
    """embedding_offline=True 或 HF_HUB_OFFLINE=1 均应判定为离线。"""
    # flag 触发
    assert SentenceTransformersProvider(embedding_offline=True)._resolve_offline() is True
    # env 触发
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    assert SentenceTransformersProvider()._resolve_offline() is True
    # 默认（无 flag 无 env）为在线
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    assert SentenceTransformersProvider()._resolve_offline() is False
