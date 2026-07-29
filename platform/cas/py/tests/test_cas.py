"""Tests for publisher_cas."""

import tempfile
from pathlib import Path
from publisher_cas import ContentAddressedStore, CasConfig, Sha256, MediaType, ArtifactRef


def test_sha256_from_bytes():
    h = Sha256.from_bytes(b"hello world")
    assert str(h) == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_sha256_invalid_rejected():
    import pytest
    with pytest.raises(ValueError):
        Sha256("not-a-hex-string")
    with pytest.raises(ValueError):
        Sha256("abc")  # too short


def test_sha256_equality():
    h1 = Sha256.from_bytes(b"test")
    h2 = Sha256.from_bytes(b"test")
    assert h1 == h2
    assert h1 == str(h2)


def test_media_type_valid():
    mt = MediaType("application/pdf")
    assert mt.value == "application/pdf"

    mt2 = MediaType(MediaType.TEXT_HTML)
    assert mt2.value == "text/html"


def test_media_type_invalid():
    import pytest
    with pytest.raises(ValueError):
        MediaType("")
    with pytest.raises(ValueError):
        MediaType("not-a-media-type")


def test_artifact_ref():
    h = Sha256.from_bytes(b"data")
    ref = ArtifactRef(hash=h, media_type=MediaType("application/json"), size=42)
    assert ref.hash == h
    assert ref.size == 42


def test_put_and_get():
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        data = b"hello world, this is a test blob"

        ref = store.put(data, media_type=MediaType("text/plain"))
        assert ref.hash == Sha256.from_bytes(data)
        assert ref.size == len(data)

        retrieved = store.get(ref)
        assert retrieved == data


def test_put_deduplicates():
    """Putting the same data twice should not create a duplicate file."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        data = b"dedup-test-data"

        ref1 = store.put(data)
        ref2 = store.put(data)

        assert ref1.hash == ref2.hash
        assert store.exists(ref1)
        assert store.exists(ref2)


def test_put_rejects_empty():
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        with pytest.raises(ValueError):
            store.put(b"")


def test_get_nonexistent():
    import pytest
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        h = Sha256.from_bytes(b"nonexistent")
        ref = ArtifactRef(hash=h, media_type=MediaType("application/octet-stream"), size=0)
        with pytest.raises(FileNotFoundError):
            store.get(ref)


def test_get_path_zero_copy():
    """get_path should return a path to the blob for zero-copy handoff."""
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        data = b"zero-copy test"
        ref = store.put(data)
        path = store.get_path(ref)
        assert path.exists()
        assert path.read_bytes() == data


def test_delete():
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        ref = store.put(b"delete-me")
        assert store.exists(ref)
        store.delete(ref)
        assert not store.exists(ref)


def test_sharded_path():
    """Local paths should be sharded by the first 4 hex chars."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = ContentAddressedStore(CasConfig(local_cache_root=root))
        data = b"sharding-test"
        ref = store.put(data)

        # Path should be root / aa / bb / full_hash
        hex_str = str(ref.hash)
        expected = root / hex_str[:2] / hex_str[2:4] / hex_str
        assert store.get_path(ref) == expected


def test_put_stream():
    """Putting via stream should produce the same result as put()."""
    import io
    with tempfile.TemporaryDirectory() as tmp:
        store = ContentAddressedStore(CasConfig(local_cache_root=Path(tmp)))
        data = b"stream test data " * 1000

        ref_direct = store.put(data)
        ref_stream = store.put_stream(io.BytesIO(data))

        assert ref_direct.hash == ref_stream.hash
        assert ref_direct.size == ref_stream.size
