"""读取并校验 Android 模拟器 GPT 与动态分区的线性映射。"""
import hashlib
import struct
import uuid
import zlib
from pathlib import Path

SECTOR = 512


def read_at(path, offset, size):
    with Path(path).open('rb') as stream:
        stream.seek(offset)
        data = stream.read(size)
    if len(data) != size:
        raise ValueError('镜像数据截断')
    return data


def gpt(path):
    header = bytearray(read_at(path, SECTOR, SECTOR))
    if header[:8] != b'EFI PART':
        raise ValueError('输入须为带 GPT 的原始模拟器 system.img')
    length, checksum = struct.unpack_from('<II', header, 12)
    struct.pack_into('<I', header, 16, 0)
    if zlib.crc32(header[:length]) != checksum:
        raise ValueError('GPT header CRC 校验失败')
    table_lba, count, entry_size, expected_crc = struct.unpack_from('<QIII', header, 72)
    if count > 1024 or entry_size < 128:
        raise ValueError('GPT 分区表尺寸超出支持范围')
    table = read_at(path, table_lba * SECTOR, count * entry_size)
    if zlib.crc32(table) != expected_crc:
        raise ValueError('GPT 分区表 CRC 校验失败')
    result = []
    for index in range(count):
        entry = table[index * entry_size:(index + 1) * entry_size]
        if entry[:16] == bytes(16):
            continue
        start, end = struct.unpack_from('<QQ', entry, 32)
        result.append({'name': entry[56:128].decode('utf-16-le').rstrip('\0'), 'offset': start * SECTOR,
                       'size': (end - start + 1) * SECTOR, 'type': str(uuid.UUID(bytes_le=entry[:16]))})
    return result


def partitions(path):
    physical = gpt(path)
    super_entry = next(item for item in physical if item['name'] == 'super')
    base = super_entry['offset']
    geometry = bytearray(read_at(path, base + 4096, 4096))
    magic, size = struct.unpack_from('<II', geometry)
    if magic != 0x616c4467 or size != 52:
        raise ValueError('LP geometry 格式超出支持范围')
    digest = bytes(geometry[8:40]); geometry[8:40] = bytes(32)
    if hashlib.sha256(geometry[:size]).digest() != digest:
        raise ValueError('LP geometry SHA-256 校验失败')
    maximum, slots, block_size = struct.unpack_from('<III', geometry, 40)
    if slots not in (1, 2, 3) or maximum > 16 * 1024 * 1024:
        raise ValueError('LP metadata 槽布局超出支持范围')
    raw = read_at(path, base + 12288, maximum)
    magic, major, minor, header_size = struct.unpack_from('<IHHI', raw)
    if magic != 0x414c5030 or major != 10 or header_size not in (128, 256):
        raise ValueError('LP header 格式超出支持范围')
    header = bytearray(raw[:header_size]); digest = bytes(header[12:44]); header[12:44] = bytes(32)
    if hashlib.sha256(header).digest() != digest:
        raise ValueError('LP header SHA-256 校验失败')
    table_size = struct.unpack_from('<I', raw, 44)[0]
    table = raw[header_size:header_size + table_size]
    if len(table) != table_size or hashlib.sha256(table).digest() != raw[48:80]:
        raise ValueError('LP tables SHA-256 校验失败')
    descriptors = [struct.unpack_from('<III', raw, offset) for offset in (80, 92, 104, 116)]
    def entries(number, expected):
        offset, count, size = descriptors[number]
        if size != expected or offset + count * size > len(table):
            raise ValueError('LP 表项越界')
        return [table[offset + i * size:offset + (i + 1) * size] for i in range(count)]
    devices = entries(3, 64)
    if len(devices) != 1:
        raise ValueError('解析要求单一 super 设备')
    extents = [struct.unpack('<QIQI', item) for item in entries(1, 24)]
    result = []
    occupied = []
    for item in entries(0, 52):
        name, attrs, first, count, group = struct.unpack('<36sIIII', item)
        name = name.rstrip(b'\0').decode('ascii')
        segments = []
        if first + count > len(extents):
            raise ValueError('LP 分区映射越界')
        for sectors, kind, address, source in extents[first:first + count]:
            if kind != 0 or source != 0:
                raise ValueError('解析要求线性 LP extent')
            offset, length = base + address * SECTOR, sectors * SECTOR
            if offset < base or offset + length > base + super_entry['size']:
                raise ValueError('LP extent 超出 super')
            segments.append({'offset': offset, 'size': length})
            occupied.append((offset, offset + length))
        result.append({'name': name, 'size': sum(x['size'] for x in segments), 'extents': segments})
    occupied.sort()
    if any(a[1] > b[0] for a, b in zip(occupied, occupied[1:])):
        raise ValueError('LP 分区范围重叠')
    return {'gpt': physical, 'partitions': result, 'block_size': block_size, 'metadata_slots': slots}


def transfer(source, destination, segments, into_image=False):
    with Path(source).open('rb') as src, Path(destination).open('r+b' if into_image else 'wb') as dst:
        for segment in segments:
            (dst if into_image else src).seek(segment['offset'])
            remaining = segment['size']
            while remaining:
                data = src.read(min(remaining, 8 * 1024 * 1024))
                if not data:
                    raise ValueError('复制分区时遇到意外 EOF')
                dst.write(data)
                remaining -= len(data)
