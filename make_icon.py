"""Draws icon.ico (a magnifying glass over a bug) with the standard library only."""
import math
import struct
import zlib


def render(size):
    px = [[(0, 0, 0, 0)] * size for _ in range(size)]
    s = size / 256.0
    ss = 4  # supersampling for smooth edges

    def inside(x, y):
        # rounded square background
        r = 52 * s
        cx, cy = min(max(x, r), size - r), min(max(y, r), size - r)
        if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
            return None
        gx, gy = 104 * s, 104 * s
        d = math.hypot(x - gx, y - gy)
        # handle of the magnifier
        hx, hy = x - 168 * s, y - 168 * s
        along = (hx + hy) / math.sqrt(2)
        across = abs(hx - hy) / math.sqrt(2)
        if -10 * s < along < 62 * s and across < 17 * s:
            return (255, 255, 255, 255)
        if 62 * s <= d <= 82 * s:
            return (255, 255, 255, 255)
        if d < 62 * s:
            # bug inside the lens
            bx, by = (x - gx) / s, (y - gy) / s
            if (bx / 22) ** 2 + ((by - 6) / 30) ** 2 <= 1:
                return (230, 57, 70, 255)
            if math.hypot(bx, by + 30) <= 14:
                return (230, 57, 70, 255)
            for lx in (-1, 1):
                for ly in (-14, 4, 22):
                    t = lx * bx
                    if 18 < t < 40 and abs(by - ly - (t - 18) * 0.35 * (1 if ly > 0 else -1)) < 3.2:
                        return (230, 57, 70, 255)
            return (225, 236, 255, 255)
        return (47, 111, 222, 255)

    for y in range(size):
        for x in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(ss):
                for sx in range(ss):
                    c = inside(x + (sx + 0.5) / ss, y + (sy + 0.5) / ss)
                    if c:
                        for k in range(4):
                            acc[k] += c[k] * (c[3] / 255 if k < 3 else 1)
            a = acc[3] / (ss * ss)
            if a:
                px[y][x] = tuple(round(acc[k] / (acc[3] / 255)) for k in range(3)) + (round(a),)
    return px


def png(px):
    h, w = len(px), len(px[0])
    raw = b''.join(b'\x00' + bytes(v for p in row for v in p) for row in px)

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + \
        chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')


def main(out='icon.ico'):
    sizes = [256, 64, 48, 32, 16]
    images = [png(render(s)) for s in sizes]
    header = struct.pack('<HHH', 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b''
    for s, data in zip(sizes, images):
        entries += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    with open(out, 'wb') as f:
        f.write(header + entries + b''.join(images))
    with open('icon.png', 'wb') as f:
        f.write(images[0])


if __name__ == '__main__':
    main()
