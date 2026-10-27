"""Windows OLE Package storage for inert PDF/TXT/HTML report attachments.

CFB v3 (512-byte sectors, MiniFAT and DIFAT) and an Ole10Native stream.
There is no executable command, absolute source path, linked object or macro.
See MS-CFB / MS-OLEDS and Apache POI's Ole10Native parsed record format.
"""
import math
import struct
import uuid
from pathlib import PurePosixPath

FREE, END, FAT, DIF = 0xffffffff, 0xfffffffe, 0xfffffffd, 0xfffffffc
PACKAGE = uuid.UUID('0003000c-0000-0000-c000-000000000046').bytes_le


def u32(value):
    return struct.pack('<I', value)


def package_file(filename, content):
    """Preserve original bytes and Unicode basename; never accept an OLE command/path."""
    if (filename != PurePosixPath(filename).name or '\\' in filename or
            any(ord(c) < 32 for c in filename) or
            PurePosixPath(filename).suffix.lower() not in {'.pdf', '.txt', '.html'}):
        raise ValueError('PPT 첨부 파일 이름 또는 형식이 올바르지 않습니다.')
    # Korean Windows' legacy ANSI fallback plus the Package2 Unicode extension.
    ansi = filename.encode('cp949', errors='replace').replace(b'?', b'_') + b'\0'
    native = struct.pack('<H', 2) + ansi + ansi + struct.pack('<HH', 0, 3)
    native += u32(len(ansi)) + ansi + u32(len(content)) + content
    wide = filename.encode('utf-16le')
    native += (u32(len(wide) // 2) + wide) * 3  # command, label, filename
    streams = [('\1Ole', bytes.fromhex('0100000200000000000000000000000000000000')),
               ('\1Ole10Native', u32(len(native)) + native)]
    sectors, chains = [], []

    def allocate(data):
        start = len(sectors)
        pieces = [data[i:i+512].ljust(512, b'\0') for i in range(0, len(data), 512)]
        sectors.extend(pieces)
        if pieces:
            chains.append((start, len(pieces)))
        return start if pieces else END

    mini, minifat, entries = bytearray(), [], []
    for name, data in streams:
        if len(data) < 4096:
            start = len(minifat)
            count = math.ceil(len(data) / 64)
            mini.extend(data.ljust(count * 64, b'\0'))
            minifat.extend([*range(start+1, start+count), END])
        else:
            start = allocate(data)
        entries.append((name, start, len(data)))
    root_start = allocate(bytes(mini))
    mini_start = allocate(b''.join(u32(v) for v in minifat).ljust(math.ceil(len(minifat)/128)*512, b'\xff'))

    def directory(name, kind, start, size, *, child=FREE, right=FREE, color=1):
        data = bytearray(128)
        encoded = (name + '\0').encode('utf-16le')
        data[:len(encoded)] = encoded
        struct.pack_into('<HBBIII', data, 64, len(encoded), kind, color, FREE, right, child)
        if kind == 5:
            data[80:96] = PACKAGE
        struct.pack_into('<IQ', data, 116, start, size)
        return data

    directory_start = allocate(
        directory('Root Entry', 5, root_start, len(mini), child=1) +
        directory(entries[0][0], 2, entries[0][1], entries[0][2], right=2) +
        directory(entries[1][0], 2, entries[1][1], entries[1][2], color=0) + bytes(128))
    base = len(sectors)
    fat_count, dif_count = 0, 0
    while True:
        count = math.ceil((base + fat_count + dif_count) / 128)
        dif = math.ceil(max(0, count - 109) / 127)
        if (count, dif) == (fat_count, dif_count):
            break
        fat_count, dif_count = count, dif
    fat_ids = list(range(base, base + fat_count))
    dif_ids = list(range(base + fat_count, base + fat_count + dif_count))
    fat = [FREE] * (fat_count * 128)
    for start, count in chains:
        fat[start:start+count] = [*range(start+1, start+count), END]
    for sid in fat_ids: fat[sid] = FAT
    for sid in dif_ids: fat[sid] = DIF
    sectors.extend(struct.pack('<128I', *fat[i:i+128]) for i in range(0, len(fat), 128))
    for index, sid in enumerate(dif_ids):
        ids = fat_ids[109+127*index:109+127*(index+1)]
        sectors.append(struct.pack('<128I', *(ids + [FREE]*(127-len(ids)) +
                       [dif_ids[index+1] if index+1 < dif_count else END])))
    header = bytearray(512)
    header[:8] = bytes.fromhex('d0cf11e0a1b11ae1')
    struct.pack_into('<HHHHH', header, 24, 0x3e, 3, 0xfffe, 9, 6)
    struct.pack_into('<9I', header, 40, 0, fat_count, directory_start, 0, 4096,
                     mini_start, math.ceil(len(minifat)/128), dif_ids[0] if dif_ids else END, dif_count)
    struct.pack_into('<109I', header, 76, *(fat_ids[:109] + [FREE]*max(0, 109-fat_count)))
    return bytes(header) + b''.join(sectors)
