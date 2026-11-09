import struct
import tempfile
import unittest
import uuid
import zlib
import hashlib
from pathlib import Path
from avdlite.assemble import assemble
from avdlite.layout import partitions


def image_fixture(path):
    size, base, maximum = 8 * 1024 * 1024, 2 * 1024 * 1024, 65536
    geometry = bytearray(4096)
    struct.pack_into('<II', geometry, 0, 0x616c4467, 52)
    struct.pack_into('<III', geometry, 40, maximum, 2, 4096)
    geometry[8:40] = hashlib.sha256(geometry[:52]).digest()
    table = bytearray(52 + 24 + 48 + 64)
    struct.pack_into('<36sIIII', table, 0, b'system', 1, 0, 1, 0)
    struct.pack_into('<QIQI', table, 52, 2048, 0, 2048, 0)
    struct.pack_into('<36sIQ', table, 76, b'default', 0, 0)
    struct.pack_into('<QIIQ36sI', table, 124, 2048, 1048576, 0, 4 * 1024 * 1024, b'super', 0)
    metadata = bytearray(maximum)
    struct.pack_into('<IHHI', metadata, 0, 0x414c5030, 10, 0, 128)
    struct.pack_into('<I', metadata, 44, len(table))
    metadata[48:80] = hashlib.sha256(table).digest()
    for offset, values in zip((80, 92, 104, 116), ((0, 1, 52), (52, 1, 24), (76, 1, 48), (124, 1, 64))):
        struct.pack_into('<III', metadata, offset, *values)
    metadata[12:44] = hashlib.sha256(metadata[:128]).digest()
    metadata[128:128 + len(table)] = table
    entries = bytearray(128 * 128)
    for index, (name, start, end) in enumerate((('vbmeta', 2048, 4095), ('super', 4096, 12287))):
        offset = index * 128
        entries[offset:offset + 16] = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
        entries[offset + 16:offset + 32] = uuid.uuid4().bytes_le
        struct.pack_into('<QQ', entries, offset + 32, start, end)
        encoded = name.encode('utf-16-le')
        entries[offset + 56:offset + 56 + len(encoded)] = encoded
    header = bytearray(512)
    header[:8] = b'EFI PART'
    struct.pack_into('<II', header, 8, 0x10000, 92)
    struct.pack_into('<QQQQ', header, 24, 1, size // 512 - 1, 34, size // 512 - 34)
    header[56:72] = uuid.uuid4().bytes_le
    struct.pack_into('<QIII', header, 72, 2, 128, 128, zlib.crc32(entries))
    struct.pack_into('<I', header, 16, zlib.crc32(header[:92]))
    with path.open('wb') as out:
        out.truncate(size)
        out.seek(510); out.write(b'\x55\xaa')
        out.seek(512); out.write(header)
        out.seek(1024); out.write(entries)
        for offset in (4096, 8192):
            out.seek(base + offset); out.write(geometry)
        for slot in range(4):
            out.seek(base + 12288 + slot * maximum); out.write(metadata)
        out.seek(base + 1048576); out.write(b'A' * 1048576)


class AssemblyTests(unittest.TestCase):
    def test_rebuild_updates_both_gpt_headers_and_lp_checksums(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / 'source.img', root / 'result.img'
            image_fixture(source)
            part, vbmeta = root / 'system.img', root / 'vbmeta.img'
            part.write_bytes(b'B' * 8192)
            vbmeta.write_bytes(b'AVB0' + bytes(4092))
            assemble(source, {'system': part}, target, vbmeta)
            self.assertLess(target.stat().st_size, source.stat().st_size)
            result = partitions(target)
            self.assertEqual(result['partitions'][0]['size'], 8192)
            with target.open('rb') as stream:
                stream.seek(result['partitions'][0]['extents'][0]['offset'])
                self.assertEqual(stream.read(8192), part.read_bytes())
                stream.seek(-512, 2)
                backup = bytearray(stream.read())
            length, expected = struct.unpack_from('<II', backup, 12)
            struct.pack_into('<I', backup, 16, 0)
            self.assertEqual(zlib.crc32(backup[:length]), expected)
            self.assertEqual(struct.unpack_from('<Q', backup, 24)[0], target.stat().st_size // 512 - 1)

    def test_rejects_corrupted_partition_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.img'
            image_fixture(source)
            with source.open('r+b') as stream:
                stream.seek(2 * 1024 * 1024 + 12288 + 128)
                stream.write(b'X')
            with self.assertRaisesRegex(ValueError, 'SHA-256'):
                partitions(source)


if __name__ == '__main__':
    unittest.main()
