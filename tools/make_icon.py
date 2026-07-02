"""生成外贸助手应用图标 (1024x1024 PNG)。

纯 Python 实现，不依赖 PIL/numpy，用 zlib + struct 直接写 PNG。

设计：
- 渐变背景：左上深蓝 → 右下青绿（外贸/海洋主题）
- 中央深蓝地球（圆 + 白色经纬线）
- 地球中心白色向上箭头（代表出口/增长）

用法: python tools/make_icon.py [output.png]
之后在 macOS 上用 sips + iconutil 转 .icns（见 build_mac.sh）
"""
import sys
import zlib
import struct
from pathlib import Path

W = H = 1024
CX = CY = 512
R = 340  # 地球半径

# 颜色
BG_TOP = (0x0F, 0x3D, 0x5C)      # 深蓝
BG_BOT = (0x16, 0xA0, 0x85)      # 青绿
EARTH = (0x0F, 0x3D, 0x5C)       # 地球深蓝
WHITE = (0xFF, 0xFF, 0xFF)
ARROW = (0xFF, 0xFF, 0xFF)


def make_png(path: Path):
    """生成 PNG 文件。"""
    rows = []
    for y in range(H):
        row = bytearray(W * 4 + 1)
        row[0] = 0  # PNG filter: None
        for x in range(W):
            dx = x - CX
            dy = y - CY
            dist_sq = dx * dx + dy * dy
            r_sq = R * R

            if dist_sq <= r_sq:
                # 地球内：深蓝底 + 白色经纬线 + 箭头
                r, g, b = EARTH
                # 经纬线（白色）
                # 赤道
                if abs(dy) <= 3:
                    r, g, b = WHITE
                # 两条纬线
                elif abs(dy - 130) <= 3 or abs(dy + 130) <= 3:
                    r, g, b = WHITE
                # 中央经线
                elif abs(dx) <= 3:
                    r, g, b = WHITE
                # 两侧经线（限定纬度范围，模拟球面）
                elif (abs(dx - 170) <= 3 or abs(dx + 170) <= 3) and abs(dy) < 230:
                    r, g, b = WHITE
                # 向上箭头（三角形）：中心，白色
                # 箭头从 y=CY+120 指向 y=CY-120，宽度随 y 变化
                elif -120 <= dy <= 120:
                    # 箭头杆 + 箭头头
                    if dy >= -60:
                        # 箭头杆：宽 40
                        if abs(dx) <= 20:
                            r, g, b = ARROW
                    else:
                        # 箭头头：随 dy 从 -60 到 -120，宽度从 40 扩到 90
                        progress = (-60 - dy) / 60  # 0~1
                        half_w = int(20 + 70 * progress)
                        if abs(dx) <= half_w and abs(dx) + (60 + dy) >= half_w * 0.4:
                            r, g, b = ARROW
            else:
                # 背景：对角渐变
                t = (x + y) / (W + H)
                r = int(BG_TOP[0] * (1 - t) + BG_BOT[0] * t)
                g = int(BG_TOP[1] * (1 - t) + BG_BOT[1] * t)
                b = int(BG_TOP[2] * (1 - t) + BG_BOT[2] * t)

            idx = 1 + x * 4
            row[idx] = r
            row[idx + 1] = g
            row[idx + 2] = b
            row[idx + 3] = 0xFF
        rows.append(bytes(row))
        if y % 128 == 0:
            print(f"  生成中... {y*100//H}%", file=sys.stderr)

    raw = b"".join(rows)
    compressed = zlib.compress(raw, 9)

    # PNG 文件
    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return c

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", compressed)
    png += chunk(b"IEND", b"")

    path.write_bytes(png)
    print(f"图标已生成: {path} ({len(png) // 1024} KB)")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/icon.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    make_png(out)
