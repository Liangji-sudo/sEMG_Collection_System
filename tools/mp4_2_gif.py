#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mp4_2_gif.py - MP4 转 GIF 工具（带压缩）
========================================

依赖安装:
    pip install moviepy pillow
"""

import sys
import os
import argparse
from pathlib import Path

# 兼容 moviepy 1.x 和 2.x
try:
    from moviepy import VideoFileClip  # moviepy 2.x
except ImportError:
    try:
        from moviepy.editor import VideoFileClip  # moviepy 1.x
    except ImportError:
        print("错误: 请先安装 moviepy")
        print("运行: pip install moviepy")
        sys.exit(1)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def optimize_gif(gif_path, colors=64):
    """
    使用 PIL 优化 GIF 文件大小

    Args:
        gif_path: GIF 文件路径
        colors: 颜色数量 (2-256)，越少文件越小
    """
    if not HAS_PIL:
        return

    try:
        img = Image.open(gif_path)
        frames = []

        # 提取所有帧
        try:
            while True:
                # 转换为调色板模式，减少颜色数
                frame = img.copy().convert('P', palette=Image.ADAPTIVE, colors=colors)
                frames.append(frame)
                img.seek(img.tell() + 1)
        except EOFError:
            pass

        if frames:
            # 保存优化后的 GIF
            frames[0].save(
                gif_path,
                save_all=True,
                append_images=frames[1:],
                optimize=True,
                duration=img.info.get('duration', 100),
                loop=0
            )
    except Exception as e:
        print(f"  优化警告: {e}")


def convert_mp4_to_gif(input_path, output_path, fps=10, scale=0.5, colors=128, width=None):
    """
    将 MP4 转换为 GIF（压缩版）

    Args:
        input_path: 输入 MP4 文件路径
        output_path: 输出 GIF 文件路径
        fps: GIF 帧率（默认 10）
        scale: 缩放比例（默认 0.5，即缩小一半）
        colors: GIF 颜色数（默认 128，范围 2-256）
        width: 指定宽度（优先于 scale）
    """
    print(f"转换中: {input_path}")

    try:
        clip = VideoFileClip(str(input_path))

        # 获取原始尺寸
        orig_w, orig_h = clip.size

        # 确定缩放
        if width:
            # 按指定宽度缩放
            new_scale = width / orig_w
            clip = clip.resized(new_scale)
        elif scale and scale != 1.0:
            clip = clip.resized(scale)

        new_w, new_h = clip.size

        # 转换为 GIF
        clip.write_gif(str(output_path), fps=fps)
        clip.close()

        # 使用 PIL 进一步优化
        if HAS_PIL and colors < 256:
            optimize_gif(output_path, colors=colors)

        # 显示文件大小
        size_kb = os.path.getsize(output_path) / 1024
        if size_kb > 1024:
            size_str = f"{size_kb/1024:.2f} MB"
        else:
            size_str = f"{size_kb:.0f} KB"

        print(f"  -> {output_path}")
        print(f"     尺寸: {orig_w}x{orig_h} -> {new_w}x{new_h}, 帧率: {fps}fps, 颜色: {colors}, 大小: {size_str}")
        return True

    except Exception as e:
        print(f"错误: {e}")
        return False


def convert_single_file(mp4_path, **kwargs):
    """转换单个 MP4 文件"""
    mp4_path = Path(mp4_path)

    if not mp4_path.exists():
        print(f"错误: 文件不存在 - {mp4_path}")
        return False

    if not mp4_path.suffix.lower() == '.mp4':
        print(f"错误: 不是 MP4 文件 - {mp4_path}")
        return False

    # 在当前目录生成同名 GIF
    gif_path = mp4_path.with_suffix('.gif')
    return convert_mp4_to_gif(mp4_path, gif_path, **kwargs)


def convert_directory(dir_path, **kwargs):
    """批量转换目录下所有 MP4 文件"""
    dir_path = Path(dir_path)

    if not dir_path.exists():
        print(f"错误: 目录不存在 - {dir_path}")
        return False

    if not dir_path.is_dir():
        print(f"错误: 不是目录 - {dir_path}")
        return False

    # 查找所有 MP4 文件
    mp4_files = list(dir_path.glob('*.mp4')) + list(dir_path.glob('*.MP4'))

    if not mp4_files:
        print(f"未找到 MP4 文件: {dir_path}")
        return False

    print(f"找到 {len(mp4_files)} 个 MP4 文件")

    # 创建 gif 子目录
    gif_dir = dir_path / 'gif'
    gif_dir.mkdir(exist_ok=True)
    print(f"输出目录: {gif_dir}\n")

    # 批量转换
    success_count = 0
    for i, mp4_file in enumerate(mp4_files, 1):
        print(f"[{i}/{len(mp4_files)}] ", end="")
        gif_path = gif_dir / mp4_file.with_suffix('.gif').name
        if convert_mp4_to_gif(mp4_file, gif_path, **kwargs):
            success_count += 1

    # 统计总大小
    total_size = sum(f.stat().st_size for f in gif_dir.glob('*.gif'))
    total_str = f"{total_size/1024/1024:.2f} MB" if total_size > 1024*1024 else f"{total_size/1024:.0f} KB"

    print(f"\n完成: {success_count}/{len(mp4_files)} 个文件转换成功")
    print(f"总大小: {total_str}")
    return success_count > 0


def main():
    parser = argparse.ArgumentParser(
        description='MP4 转 GIF 工具（带压缩）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用示例:
  %(prog)s video.mp4                    转换单个文件（默认缩小50%%，128色）
  %(prog)s videos/                      批量转换目录下所有 mp4
  %(prog)s video.mp4 --width 320        指定输出宽度为 320 像素
  %(prog)s video.mp4 --fps 8 --colors 64   更低帧率和颜色，更小文件

压缩建议（目标几百KB）:
  - 降低分辨率: --width 320 或 --scale 0.3
  - 降低帧率: --fps 8
  - 减少颜色: --colors 64

依赖安装:
  pip install moviepy pillow
'''
    )

    parser.add_argument('path', help='MP4 文件路径 或 包含 MP4 文件的目录路径')
    parser.add_argument('--fps', type=int, default=10, help='GIF 帧率 (默认: 10)')
    parser.add_argument('--scale', type=float, default=0.5, help='缩放比例 (默认: 0.5，即缩小一半)')
    parser.add_argument('--width', type=int, default=None, help='指定输出宽度（优先于 scale）')
    parser.add_argument('--colors', type=int, default=128, help='GIF 颜色数 2-256 (默认: 128，越少越小)')

    args = parser.parse_args()

    # 验证颜色参数
    if args.colors < 2 or args.colors > 256:
        print("错误: --colors 必须在 2-256 之间")
        sys.exit(1)

    target_path = Path(args.path)

    kwargs = {
        'fps': args.fps,
        'scale': args.scale,
        'colors': args.colors,
        'width': args.width,
    }

    if target_path.is_dir():
        convert_directory(target_path, **kwargs)
    elif target_path.is_file():
        convert_single_file(target_path, **kwargs)
    else:
        print(f"错误: 路径不存在 - {args.path}")
        sys.exit(1)


if __name__ == '__main__':
    main()
