"""Tests for extract_skill_zip: zip layout detection, name parsing, zip-slip,
size/file caps, conflict, and atomic install."""

import io
import os
import zipfile
import pytest
from werkzeug.datastructures import FileStorage


def _make_zip(entries: dict) -> FileStorage:
    """Build a zip from {relpath: bytes} and wrap in a FileStorage('foo.zip')."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    buf.seek(0)
    return FileStorage(stream=buf, filename='foo.zip', content_type='application/zip')


def test_root_skill_md_uses_frontmatter_name(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'SKILL.md': b'---\nname: alpha\ndescription: d\n---\n# alpha', 'helper.py': b'x=1'})
    res = extract_skill_zip(str(tmp_path), z)
    assert res == {'name': 'alpha', 'path': '.opencode/skills/alpha'}
    assert (tmp_path / '.opencode' / 'skills' / 'alpha' / 'SKILL.md').exists()
    assert (tmp_path / '.opencode' / 'skills' / 'alpha' / 'helper.py').read_bytes() == b'x=1'


def test_single_top_dir_layout_strips_prefix(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'beta/SKILL.md': b'---\nname: beta\n---\n', 'beta/notes.txt': b'hi'})
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'beta'
    assert (tmp_path / '.opencode' / 'skills' / 'beta' / 'SKILL.md').exists()
    assert (tmp_path / '.opencode' / 'skills' / 'beta' / 'notes.txt').read_bytes() == b'hi'


def test_missing_frontmatter_name_falls_back_to_zip_filename(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_zip({'SKILL.md': b'no frontmatter here'})
    z.filename = 'my-skill.zip'
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'my-skill'


def test_zip_slip_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: ok\n---\n', '../evil.txt': b'pwn'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_ZIP_UNSAFE'


def test_missing_skill_md_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'README.md': b'no skill md'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'INVALID_SKILL_ZIP'


def test_name_conflict_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    # pre-create the target dir
    (tmp_path / '.opencode' / 'skills' / 'dup').mkdir(parents=True)
    z = _make_zip({'SKILL.md': b'---\nname: dup\n---\n'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_EXISTS'


def test_non_zip_filename_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: ok\n---\n'})
    z.filename = 'not-a-zip.txt'
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'BAD_FILE'


def test_invalid_name_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    z = _make_zip({'SKILL.md': b'---\nname: bad name with spaces\n---\n'})
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'INVALID_SKILL_NAME'


def test_too_large_zip_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError, MAX_ZIP_BYTES
    payload = io.BytesIO(b'\0' * (MAX_ZIP_BYTES + 1))
    fs = FileStorage(stream=payload, filename='big.zip', content_type='application/zip')
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), fs)
    assert ei.value.code == 'SKILL_ZIP_TOO_LARGE'


def test_too_many_files_zip_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError, MAX_ZIP_ENTRIES
    entries = {'SKILL.md': b'---\nname: ok\n---\n'}
    for i in range(MAX_ZIP_ENTRIES + 1):
        entries[f'f{i}.txt'] = b'x'
    z = _make_zip(entries)
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), z)
    assert ei.value.code == 'SKILL_ZIP_TOO_MANY_FILES'


def test_corrupted_zip_rejected(tmp_path):
    from utils.skill_upload import extract_skill_zip, SkillUploadError
    fs = FileStorage(stream=io.BytesIO(b'not a zip at all'), filename='broken.zip',
                     content_type='application/zip')
    with pytest.raises(SkillUploadError) as ei:
        extract_skill_zip(str(tmp_path), fs)
    assert ei.value.code == 'SKILL_ZIP_INVALID'


class _LegacyNameInfo(zipfile.ZipInfo):
    """ZipInfo whose stored name is raw non-UTF8 bytes (no 0x800 flag),
    mimicking zips produced by Chinese Windows tools (GBK filenames)."""

    def __init__(self, raw: bytes, date_time=(2026, 1, 1, 0, 0, 0)):
        super().__init__(date_time=date_time)
        self._raw = raw
        self.filename = raw.decode('cp437')
        self.flag_bits = 0
        self.compress_type = zipfile.ZIP_DEFLATED
        self.external_attr = 0o600 << 16

    def _encodeFilenameFlags(self):
        return self._raw, self.flag_bits


def _make_gbk_zip(entries: dict) -> FileStorage:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(_LegacyNameInfo(path.encode('gbk')), data)
    buf.seek(0)
    return FileStorage(stream=buf, filename='foo.zip', content_type='application/zip')


def test_decoded_zip_name_utf8_flag_passthrough():
    from utils.zip_unicode import decoded_zip_name
    info = zipfile.ZipInfo('数据.txt')
    info.flag_bits |= 0x800
    assert decoded_zip_name(info) == '数据.txt'


def test_decoded_zip_name_gbk_recovery():
    from utils.zip_unicode import decoded_zip_name
    info = _LegacyNameInfo('数据/工具.txt'.encode('gbk'))
    assert decoded_zip_name(info) == '数据/工具.txt'


def test_gbk_chinese_filenames_survive_skill_upload(tmp_path):
    from utils.skill_upload import extract_skill_zip
    z = _make_gbk_zip({
        'SKILL.md': '---\nname: cn-skill\n---\n'.encode('utf-8'),
        '脚本/工具.py': b'x=1',
        '数据.txt': '你好'.encode('utf-8'),
    })
    res = extract_skill_zip(str(tmp_path), z)
    assert res['name'] == 'cn-skill'
    base = tmp_path / '.opencode' / 'skills' / 'cn-skill'
    assert (base / 'SKILL.md').exists()
    assert (base / '脚本' / '工具.py').read_bytes() == b'x=1'
    assert (base / '数据.txt').read_bytes() == '你好'.encode('utf-8')


def test_safe_extract_decoded_handles_gbk(tmp_path):
    from utils.zip_unicode import safe_extract_decoded
    z = _make_gbk_zip({'目录/文件.txt': '内容'.encode('utf-8'), 'top.txt': b'ok'})
    with zipfile.ZipFile(z.stream) as zf:
        safe_extract_decoded(zf, str(tmp_path))
    assert (tmp_path / '目录' / '文件.txt').read_bytes() == '内容'.encode('utf-8')
    assert (tmp_path / 'top.txt').read_bytes() == b'ok'


def test_safe_extract_decoded_blocks_traversal(tmp_path):
    from utils.zip_unicode import safe_extract_decoded
    z = _make_gbk_zip({'../evil.txt': b'pwn', 'SKILL.md': b'---\nname: ok\n---\n'})
    with zipfile.ZipFile(z.stream) as zf, pytest.raises(ValueError):
        safe_extract_decoded(zf, str(tmp_path))
