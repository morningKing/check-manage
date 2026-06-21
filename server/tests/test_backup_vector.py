"""Vector-store (mem0/Chroma) backup + restore — packs MEM0_STORE_ROOT into the
backup ZIP and restores it as a full directory overwrite."""
import sys, os, zipfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import utils.backup as bk


def test_vector_store_pack_and_restore(tmp_path, monkeypatch):
    store = tmp_path / 'mem0'
    (store / 'sub').mkdir(parents=True)
    (store / 'chroma.sqlite3').write_bytes(b'VECTORDB')
    (store / 'sub' / 'data.bin').write_bytes(b'\x00\x01\x02')
    monkeypatch.setattr(bk, 'MEM0_STORE_ROOT', str(store))

    zp = tmp_path / 'b.zip'
    with zipfile.ZipFile(zp, 'w') as zf:
        assert bk._add_vector_store_to_zip(zf) is True
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
    assert 'vector_store/chroma.sqlite3' in names
    assert 'vector_store/sub/data.bin' in names

    # wipe then restore (full overwrite)
    shutil.rmtree(store)
    assert not store.exists()
    assert bk._restore_vector_store(str(zp)) is True
    assert (store / 'chroma.sqlite3').read_bytes() == b'VECTORDB'
    assert (store / 'sub' / 'data.bin').read_bytes() == b'\x00\x01\x02'


def test_pack_noop_when_store_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(bk, 'MEM0_STORE_ROOT', str(tmp_path / 'does-not-exist'))
    zp = tmp_path / 'empty.zip'
    with zipfile.ZipFile(zp, 'w') as zf:
        assert bk._add_vector_store_to_zip(zf) is False


def test_restore_noop_when_no_vector_store_in_zip(tmp_path):
    zp = tmp_path / 'novs.zip'
    with zipfile.ZipFile(zp, 'w') as zf:
        zf.writestr('manifest.json', '{}')
    assert bk._restore_vector_store(str(zp)) is False
