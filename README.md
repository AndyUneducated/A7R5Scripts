# batch_shrink.py 使用说明

输入目录：`input`  
输出目录：`output`

## 功能概述

`batch_shrink.py` 是一个用于 **批量压缩 / 转换照片格式的工具**，特别适用于 **Sony A7R V 等高像素相机照片的分享与归档**。

脚本主要功能：

1. 递归扫描输入目录中的图片
2. 解码 RAW / JPEG / HEIF 等格式
3. 自动修正 EXIF Orientation（旋转方向）
4. 按最长边缩放图片
5. 转换为 **HEIF 或 JPEG**
6. 并行处理以提升速度
7. 写入输出目录

该脚本 **会重新编码图像文件**，但不会改变图像内容，仅压缩或调整分辨率。

## 支持的文件类型

脚本默认支持以下格式：

| 类型 | 扩展名 |
|---|---|
|Sony RAW | `.arw` |
|Sony HEIF | `.hif` `.hifc` |
|HEIF | `.heif` |
|HEIC | `.heic` |
|JPEG | `.jpg` `.jpeg` |
|PNG | `.png` |

说明：

- `.arw` 使用 **rawpy** 解码
- `.heic / .heif / .hif` 使用 **pillow-heif**
- `.jpg / .png` 使用 **Pillow**

## 输出文件格式

支持两种输出格式：

| 格式 | 说明 |
|---|---|
|HEIF | 默认输出格式，压缩率高 |
|JPEG | 兼容性最高 |

默认输出扩展名为 **`.heic`**。

## 图像处理流程

每张图片处理步骤：

1. 解码图像（RAW / HEIF / JPEG）
2. 自动修正 EXIF Orientation
3. 处理透明图像（PNG / HEIF alpha）
4. 转换为 RGB
5. 按最长边缩放
6. 编码为 HEIF 或 JPEG

## 参数说明

| 参数 | 是否必需 | 默认值 | 示例 | 说明 |
|---|---|---|---|---|
| `in_dir` | 是 | 无 | `input` | 输入目录，脚本会递归扫描该目录中的所有支持格式文件 |
| `out_dir` | 是 | 无 | `output` | 输出目录，处理后的文件会写入该目录 |
| `--out-format` | 否 | `heif` | `--out-format jpg` | 输出格式，可选 `heif` 或 `jpg` |
| `--max-edge` | 否 | `6000` | `--max-edge 4096` | 输出图片最长边像素，超过该值会等比例缩放 |
| `--quality` | 否 | `80` | `--quality 85` | 输出质量，JPEG 为 1–95，HEIF 为 1–100 |
| `--bg` | 否 | `white` | `--bg 255,255,255` | 透明图片背景颜色，可为 `white`、`black` 或 `R,G,B` |
| `--workers` | 否 | CPU 核心数 | `--workers 8` | 并行处理进程数量 |
| `--overwrite` | 否 | `false` | `--overwrite` | 若输出文件已存在，则覆盖 |
| `--strip` | 否 | `false` | `--strip` | （仅 JPEG）删除所有 EXIF metadata |
| `--keep-orientation-only` | 否 | `false` | `--keep-orientation-only` | （仅 JPEG）仅保留 EXIF Orientation 字段 |

## 命令示例

| 场景 | 命令 |
|---|---|
|默认压缩为 HEIF | `python batch_shrink.py input output` |
|生成社交媒体尺寸（4096px） | `python batch_shrink.py input output --max-edge 4096` |
|高压缩版本 | `python batch_shrink.py input output --max-edge 2048 --quality 70` |
|输出 JPEG | `python batch_shrink.py input output --out-format jpg` |
|覆盖已有文件 | `python batch_shrink.py input output --overwrite` |
|指定并行进程 | `python batch_shrink.py input output --workers 8` |
|指定背景颜色 | `python batch_shrink.py input output --bg 255,255,255` |
|高质量归档 | `python batch_shrink.py input output --max-edge 6000 --quality 85` |
|社交媒体推荐参数 | `python batch_shrink.py input output --max-edge 4096 --quality 80 --workers 8` |
|手机分享版本 | `python batch_shrink.py input output --max-edge 2048 --quality 75` |

# fix_photo_time.py 使用说明

输入目录：`input`  
输出目录：`output`

## 功能概述

`fix_photo_time.py` 是一个用于**批量修复照片与视频时间 metadata 的工具**。  
它通过调用 **ExifTool** 修改文件中的时间相关 metadata，而**不会改变图像或视频的像素数据**。

该脚本适用于相机时间设置错误、夏令时（DST）未开启、时区设置错误等场景。

脚本工作流程：

1. 递归扫描输入目录中的文件
2. 将文件复制到输出目录（保持原有目录结构）
3. 使用 ExifTool 修改 metadata 时间
4. 同步更新 EXIF、XMP、文件系统时间等信息
5. 可选更新 EXIF 时区字段

整个过程 **不会重新编码图像或视频数据**，仅修改 metadata。

## 支持的文件类型

脚本默认支持以下格式：

|类型|扩展名|
|---|---|
Sony RAW | `.arw` |
Sony HEIF | `.hif` |
HEIF | `.heif` |
HEIC | `.heic` |
JPEG | `.jpg` `.jpeg` |
Video | `.mp4` |

可通过 `--ext` 参数扩展支持其他格式。

## 修改的 metadata 字段

脚本会同步更新多个时间字段，以确保不同软件（Lightroom / Capture One / Finder 等）显示一致。

### EXIF

- DateTimeOriginal
- CreateDate
- ModifyDate
- OffsetTime
- OffsetTimeOriginal
- OffsetTimeDigitized

### XMP

- CreateDate
- ModifyDate
- DateCreated

### 文件系统时间

- FileModifyDate
- FileCreateDate

### 预览图 metadata

- IFD1:ModifyDate

## 参数说明

| 参数 | 是否必需 | 示例 | 说明 |
|---|---|---|---|
| `-i`, `--input` | 必需 | `-i input` | 输入目录。脚本会递归扫描该目录中的文件。 |
| `-o`, `--output` | 可选 | `-o output` | 输出目录。处理后的文件会写入该目录，并保持原有目录结构。默认 `output`。 |
| `--shift` | 可选 | `--shift=+01:00:00` | 手动时间偏移。格式 `±HH:MM:SS`。例如 `+01:00:00` 或 `-00:30:00`。 |
| `--from-offset` | 可选 | `--from-offset=-08:00` | 原始时区偏移。格式 `±HH:MM`。通常表示相机错误记录的时区。 |
| `--to-offset` | 可选 | `--to-offset=-07:00` | 目标时区偏移。脚本会自动计算时间差：`to-offset - from-offset`。 |
| `--set-offset` | 可选 | `--set-offset=-07:00` | 写入新的 EXIF 时区字段。会更新 `OffsetTime`、`OffsetTimeOriginal`、`OffsetTimeDigitized`。 |
| `--ext` | 可选 | `--ext=mov` | 额外处理的文件扩展名。可重复使用，例如 `--ext=mov --ext=avi`。 |
| `--dry-run` | 可选 | `--dry-run` | 仅打印 ExifTool 命令，不修改文件。用于验证参数。 |

## 命令示例

| 场景 | 命令 |
|---|---|
|修复 DST（相机记录为 UTC-8，但实际为 UTC-7） | `python fix_photo_time.py -i input -o output --from-offset=-08:00 --to-offset=-07:00 --set-offset=-07:00` |
|时区转换（东京 UTC+9 → 美国西海岸 UTC-7） | `python fix_photo_time.py -i input -o output --from-offset=+09:00 --to-offset=-07:00 --set-offset=-07:00` |
|手动偏移 +1 小时（典型 DST 修复） | `python fix_photo_time.py -i input -o output --shift=+01:00:00 --set-offset=-07:00` |
|手动偏移 -30 分钟 | `python fix_photo_time.py -i input -o output --shift=-00:30:00` |
|只修改 EXIF 时区（不改变时间） | `python fix_photo_time.py -i input -o output --shift=+00:00:00 --set-offset=-07:00` |
|Dry Run（仅查看将执行的 ExifTool 命令，不实际修改） | `python fix_photo_time.py -i input -o output --shift=+01:00:00 --set-offset=-07:00 --dry-run` |

## 结果验证

```bash
exiftool -time:all -a -G1 -s DSC05267.ARW
```