"""Rebuild GPT and LP metadata from resized filesystems while retaining partition identities."""
import hashlib
import struct
import zlib
from pathlib import Path
from .layout import SECTOR, gpt, partitions, read_at

ALIGNMENT = 1024 * 1024


def align(value, unit=ALIGNMENT):
    return (value + unit - 1) // unit * unit


def copy_file(source, destination, offset):
    destination.seek(offset)
    with Path(source).open('rb') as stream:
        while block := stream.read(8 * 1024 * 1024):
            destination.write(block)


def assemble(source, files, destination, vbmeta):
    source, destination = Path(source), Path(destination)
    layout = partitions(source)
    physical = layout['gpt']
    if {p['name'] for p in physical} != {'super', 'vbmeta'}:
        raise ValueError('Building requires an emulator GPT layout with super and vbmeta')
    super_entry = next(p for p in physical if p['name'] == 'super')
    base = super_entry['offset']
    geometry = read_at(source, base + 4096, 4096)
    maximum, slots, block_size = struct.unpack_from('<III', geometry, 40)
    raw = bytearray(read_at(source, base + 12288, maximum))
    header_size = struct.unpack_from('<I', raw, 8)[0]
    table_size = struct.unpack_from('<I', raw, 44)[0]
    table = bytearray(raw[header_size:header_size + table_size])
    poff, pcount, psize = struct.unpack_from('<III', raw, 80)
    eoff, ecount, esize = struct.unpack_from('<III', raw, 92)
    doff, dcount, dsize = struct.unpack_from('<III', raw, 116)
    if ecount != pcount or dcount != 1 or psize != 52 or esize != 24:
        raise ValueError('Rebuilding requires one extent per partition')
    first_sector = struct.unpack_from('<Q', table, doff)[0]
    cursor = align(max(first_sector * SECTOR, 12288 + 2 * maximum * slots))
    segments = []
    for index in range(pcount):
        offset = poff + index * psize
        name, attrs, first, count, group = struct.unpack_from('<36sIIII', table, offset)
        name = name.rstrip(b'\0').decode('ascii')
        if count != 1 or first >= ecount:
            raise ValueError('Partition extent exceeds the supported range')
        file = files[name]
        size = align(Path(file).stat().st_size, block_size)
        if Path(file).stat().st_size % SECTOR:
            raise ValueError('Partition files must be sector-aligned')
        struct.pack_into('<QIQI', table, eoff + first * esize, size // SECTOR, 0, cursor // SECTOR, 0)
        segments.append({'name': name, 'offset': base + cursor, 'size': size, 'file': file})
        cursor = align(cursor + size)
    super_size = cursor
    struct.pack_into('<Q', table, doff + 16, super_size)
    raw[header_size:header_size + table_size] = table
    raw[48:80] = hashlib.sha256(table).digest()
    raw[12:44] = bytes(32)
    raw[12:44] = hashlib.sha256(raw[:header_size]).digest()
    header = bytearray(read_at(source, SECTOR, SECTOR))
    table_lba, count, entry_size, _ = struct.unpack_from('<QIII', header, 72)
    entries = bytearray(read_at(source, table_lba * SECTOR, count * entry_size))
    table_sectors = align(len(entries), SECTOR) // SECTOR
    total_size = align(base + super_size + (table_sectors + 1) * SECTOR)
    last_lba = total_size // SECTOR - 1
    last_usable = last_lba - table_sectors - 1
    for index in range(count):
        offset = index * entry_size
        name = entries[offset + 56:offset + 128].decode('utf-16-le').rstrip('\0')
        if name == 'super':
            struct.pack_into('<Q', entries, offset + 40, (base + super_size) // SECTOR - 1)
    entries_crc = zlib.crc32(entries)
    struct.pack_into('<Q', header, 32, last_lba)
    struct.pack_into('<Q', header, 48, last_usable)
    struct.pack_into('<I', header, 88, entries_crc)
    length = struct.unpack_from('<I', header, 12)[0]
    def checksum(data):
        struct.pack_into('<I', data, 16, 0)
        struct.pack_into('<I', data, 16, zlib.crc32(data[:length]))
    checksum(header)
    backup = bytearray(header)
    struct.pack_into('<Q', backup, 24, last_lba)
    struct.pack_into('<Q', backup, 32, 1)
    struct.pack_into('<Q', backup, 72, last_lba - table_sectors)
    checksum(backup)
    mbr = bytearray(read_at(source, 0, SECTOR))
    struct.pack_into('<I', mbr, 446 + 12, min(last_lba, 0xffffffff))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('wb') as output:
        output.truncate(total_size)
        output.write(mbr)
        output.seek(SECTOR); output.write(header)
        output.seek(table_lba * SECTOR); output.write(entries)
        output.seek(base + 4096); output.write(geometry)
        output.seek(base + 8192); output.write(geometry)
        for index in range(slots * 2):
            output.seek(base + 12288 + index * maximum)
            output.write(raw)
        vbmeta_entry = next(item for item in physical if item['name'] == 'vbmeta')
        if Path(vbmeta).stat().st_size > vbmeta_entry['size']:
            raise ValueError('vbmeta exceeds its partition')
        copy_file(vbmeta, output, vbmeta_entry['offset'])
        for item in segments:
            copy_file(item['file'], output, item['offset'])
        output.seek((last_lba - table_sectors) * SECTOR); output.write(entries)
        output.seek(last_lba * SECTOR); output.write(backup)
    checked = partitions(destination)
    if [p['name'] for p in checked['partitions']] != [p['name'] for p in layout['partitions']]:
        raise ValueError('Partition name verification failed after rebuilding')
    return {'bytes': total_size, 'partitions': [{k: v for k, v in p.items() if k != 'file'} for p in segments]}
