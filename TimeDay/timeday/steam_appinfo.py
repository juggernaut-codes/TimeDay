"""Read names and icon hashes from Steam's local binary app catalog."""

import struct
from io import BytesIO


def read_string(stream):
    value = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise ValueError("Incomplete Steam string")
        if byte == b"\0":
            return value.decode("utf-8", errors="replace")
        value.extend(byte)


def read_number(stream, format="<I"):
    size = struct.calcsize(format)
    data = stream.read(size)
    if len(data) != size:
        raise ValueError("Incomplete Steam record")
    return struct.unpack(format, data)[0]


def common_fields(data, strings):
    stream = BytesIO(data)
    path, fields = [], {}
    fixed_sizes = {2: 4, 3: 4, 4: 4, 6: 4, 7: 8, 10: 8}
    while stream.tell() < len(data):
        kind = read_number(stream, "<B")
        if kind == 8:
            if not path:
                return fields
            path.pop()
            continue
        key = strings[read_number(stream)] if strings is not None else read_string(stream)
        if kind == 0:
            path.append(key)
        elif kind == 1:
            value = read_string(stream)
            if path and path[-1] == "common" and key in ("name", "icon", "clienticon"):
                fields[key] = value
        elif kind in fixed_sizes:
            size = fixed_sizes[kind]
            if len(stream.read(size)) != size:
                raise ValueError("Incomplete Steam value")
        else:
            raise ValueError("Unsupported Steam value type")
    return fields


def read_appinfo(path):
    # Record layouts for versions 39–41 are documented in the reference reader.
    # https://github.com/SteamDatabase/SteamAppInfo/blob/master/SteamAppInfoParser/AppInfo.cs
    try:
        data = path.read_bytes()
        stream = BytesIO(data)
        magic = read_number(stream)
        if magic not in (0x07564427, 0x07564428, 0x07564429):
            return {}
        read_number(stream)  # Steam universe.
        strings = None
        record_start, record_end = 8, len(data)
        if magic == 0x07564429:
            table_offset = read_number(stream, "<Q")
            record_start = stream.tell()
            if not record_start <= table_offset < len(data):
                return {}
            record_end = table_offset
            stream.seek(table_offset)
            count = read_number(stream)
            if count > len(data) - stream.tell():
                return {}
            strings = [read_string(stream) for _ in range(count)]
        stream.seek(record_start)
        result = {}
        metadata_size = 60 if magic >= 0x07564428 else 40
        while stream.tell() + 4 <= record_end:
            appid = read_number(stream)
            if appid == 0:
                break
            size = read_number(stream)
            end = stream.tell() + size
            if size < metadata_size or end > record_end:
                break
            stream.seek(metadata_size, 1)
            blob = stream.read(size - metadata_size)
            try:
                fields = common_fields(blob, strings)
            except (ValueError, IndexError):
                continue
            if fields.get("name"):
                result[str(appid)] = fields
        return result
    except (OSError, ValueError):
        return {}
