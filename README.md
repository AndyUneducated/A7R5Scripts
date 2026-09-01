# A7R5Scripts

Sony A7R V 照片工作流脚本：

- `batch_shrink.py`：批量压缩 / 转换照片格式（会重新编码像素）
- `fix_photo_time.py`：批量修复照片与视频的时间 metadata（不重新编码像素）

## 环境准备

需要 Python 3.10 及以上（代码使用了 `X | None` 类型语法）。

```bash
pip install -r requirements.txt

# fix_photo_time.py 额外需要 ExifTool
brew install exiftool

# 若 HEIF 读写报错，补装系统库
brew install libheif
```

运行测试：

```bash
python -m pytest tests
```

## 仓库结构

| 路径 | 说明 |
|---|---|
| `batch_shrink.py` | 压缩 / 格式转换 CLI |
| `fix_photo_time.py` | 时间 metadata 修复 CLI |
| `a7r5/imaging.py` | 解码、缩放、编码（含体积上限搜索） |
| `a7r5/exifmeta.py` | EXIF 读取与改写 |
| `a7r5/timeshift.py` | 时间偏移与时区解析 |
| `a7r5/fsutil.py` | 目录扫描、路径映射、体积解析 |
| `a7r5/progress.py` | 进度显示 |
| `tests/` | 单元测试 |

---

# batch_shrink.py

## 功能概述

用于 **批量压缩 / 转换照片格式**，适合高像素相机照片的分享与归档。

处理流程：

1. 递归扫描输入目录（自动跳过隐藏文件与 `._xxx` AppleDouble 伴生文件）
2. 解码 RAW / HEIF / JPEG / PNG
3. 把 EXIF Orientation 烘进像素，并从写出的 EXIF 中移除该字段
4. 透明图像合成到背景色，统一转 RGB
5. 按最长边缩放，或按目标文件体积搜索最佳参数
6. 编码为 HEIF 或 JPEG，**保留 EXIF 与 ICC**
7. 按输入目录结构镜像写入输出目录

该脚本会重新编码图像，但不改变画面内容，仅压缩或调整分辨率。

## 支持的输入格式

| 类型 | 扩展名 | 解码器 |
|---|---|---|
| Sony RAW | `.arw` | rawpy |
| Sony HEIF | `.hif` `.hifc` | pillow-heif |
| HEIF / HEIC | `.heif` `.heic` | pillow-heif |
| JPEG | `.jpg` `.jpeg` | Pillow |
| PNG | `.png` | Pillow |

输出格式为 HEIF（默认，扩展名 `.heic`）或 JPEG。

## 输出目录与命名

输出**镜像输入目录结构**，`in/day1/DSC1.ARW` → `out/day1/DSC1.arw.heic`。

| `--naming` | 结果 | 说明 |
|---|---|---|
| `source-ext`（默认） | `DSC1.arw.heic` | 名字里保留源扩展名，同一目录下的 `DSC1.ARW` 与 `DSC1.JPG` 不会撞名 |
| `plain` | `DSC1.heic` | 名字更干净；若检测到撞名，会自动回退为 `source-ext` 形式并打印 WARNING |

## 元数据

| 输出格式 | EXIF | ICC |
|---|---|---|
| HEIF | 保留 | 保留 |
| JPEG | 保留 | 保留 |

- RAW 本身经 rawpy 解码后不携带 metadata，脚本会从 **ARW 内嵌 JPEG 预览** 中提取 EXIF 复用，因此拍摄时间、机身、镜头、曝光参数、GPS 都会保留。
- 写出的 EXIF 会移除 Orientation（方向已烘进像素），并把 `PixelXDimension` / `PixelYDimension` 改为实际输出尺寸。
- MakerNote 依赖文件内的绝对偏移，重写后必然失效，因此会被丢弃（镜头型号等信息在标准 EXIF 字段中，不受影响）。
- 用 `--strip` 可以不写任何 EXIF 与 ICC。

## 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `in_dir` | 必需 | 输入目录，递归扫描 |
| `out_dir` | 必需 | 输出目录，镜像输入结构；若位于输入目录内部会自动排除 |
| `--out-format` | `heif` | 输出格式：`heif` 或 `jpg` |
| `--max-edge` | `6000` | 输出最长边像素；设置了 `--max-size` 时默认不限制 |
| `--quality` | `80` | JPEG 为 1–95，HEIF 为 1–100；设置了 `--max-size` 时默认取上限 |
| `--max-size` | 无 | 输出体积上限（如 `3mb`，二进制单位）。在内存中搜索能放进该体积的最佳编码 |
| `--subsampling` | `auto` | 仅 JPEG。`auto` 按质量选 4:4:4 / 4:2:2 / 4:2:0，也可显式指定 `444` `422` `420` |
| `--raw-wb` | `camera` | 仅 RAW。`camera` 用相机白平衡（与机内 JPEG 一致），`auto` 自动白平衡，`none` 不做白平衡 |
| `--raw-half-size` | 关闭 | 仅 RAW。半分辨率解码，输出尺寸不大时能快数倍 |
| `--naming` | `source-ext` | 输出文件命名方式，见上文 |
| `--strip` | 关闭 | 不写 EXIF 与 ICC |
| `--bg` | `white` | 透明图像背景色：`white` / `black` / `R,G,B` |
| `--workers` | CPU 核数（上限 8） | 并行进程数。一张 A7R V RAW 每进程约需 200 MB 内存 |
| `--overwrite` | 关闭 | 覆盖已存在的输出，默认跳过 |

`--max-size` 的搜索策略是**先降质量、再降分辨率**：优先保住像素，只有连最低质量都超标时才缩小尺寸，且每次缩放都从原图重采样。所有尝试都在内存中完成，最终结果通过临时文件原子写入，中断不会留下半成品。

退出码：`0` 成功，`1` 没有可处理文件，`2` 参数错误，`3` HEIF 不可用，`10` 有文件失败，`130` 被中断。

## 命令示例

| 场景 | 命令 |
|---|---|
| 默认压缩为 HEIF | `python batch_shrink.py input output` |
| 社交媒体尺寸 | `python batch_shrink.py input output --max-edge 4096` |
| 高压缩版本 | `python batch_shrink.py input output --max-edge 2048 --quality 70` |
| 输出 JPEG | `python batch_shrink.py input output --out-format jpg` |
| 限制单文件 3 MB | `python batch_shrink.py input output --out-format jpg --max-size 3mb` |
| 高质量归档 | `python batch_shrink.py input output --max-edge 6000 --quality 90` |
| 手机分享版本 | `python batch_shrink.py input output --max-edge 2048 --quality 75` |
| 微信传播（JPEG，兼容安卓） | `python batch_shrink.py input output --out-format jpg --max-edge 3072 --max-size 3mb` |
| RAW 快速预览 | `python batch_shrink.py input output --max-edge 2048 --raw-half-size` |
| 去掉全部元数据 | `python batch_shrink.py input output --out-format jpg --strip` |

---

# fix_photo_time.py

## 功能概述

用于**批量修复照片与视频的时间 metadata**，适用于相机时间设置错误、夏令时（DST）未开启、时区设置错误等场景。

它通过 ExifTool 修改时间相关 metadata，**不会改变图像或视频的像素数据**。

处理流程：

1. 递归扫描输入目录（跳过隐藏文件与 `._xxx`）
2. 复制到输出目录并保持目录结构（`--in-place` 则直接改原文件）
3. 按文件类型分批调用 ExifTool 平移 metadata 时间，并可写入 EXIF 时区
4. 再跑一遍，把文件系统时间设为修正后的拍摄时间

## 支持的文件类型

| 类型 | 扩展名 |
|---|---|
| 图片 | `.arw` `.dng` `.hif` `.heif` `.heic` `.jpg` `.jpeg` `.tif` `.tiff` |
| 视频 | `.mp4` `.mov` `.m4v` |

可通过 `--ext` 追加其他扩展名。

## 修改的 metadata 字段

图片：

- EXIF：`DateTimeOriginal`、`CreateDate`、`ModifyDate`（即 `AllDates`）
- EXIF 时区（仅 `--set-offset`）：`OffsetTime`、`OffsetTimeOriginal`、`OffsetTimeDigitized`
- XMP：`CreateDate`、`ModifyDate`、`DateCreated`
- 预览图：`IFD1:ModifyDate`

视频：

- QuickTime：`AllDates`、`TrackCreateDate`、`TrackModifyDate`、`MediaCreateDate`、`MediaModifyDate`
- XMP：`CreateDate`、`ModifyDate`

文件系统时间（除非 `--no-file-times`）：

- `FileModifyDate`、`FileCreateDate`，取值来自修正后的拍摄时间。
  直接平移这两个字段会平移"复制文件的时刻"，所以这里是从 metadata 反写的。

## 参数说明

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-i`, `--input` | 必需 | 输入目录，递归扫描 |
| `-o`, `--output` | `output` | 输出目录，镜像输入结构 |
| `--in-place` | 关闭 | 直接修改输入文件，不复制。避免 RAW 库占用双倍磁盘 |
| `--shift` | 无 | 手动时间偏移，格式 `±HH:MM:SS`，秒会被正确处理 |
| `--from-offset` | 无 | 原始时区偏移，格式 `±HH:MM` |
| `--to-offset` | 无 | 目标时区偏移，偏移量按 `to - from` 计算 |
| `--set-offset` | 无 | 写入 EXIF 时区字段（仅图片） |
| `--ext` | 无 | 追加处理的扩展名，可重复 |
| `--no-file-times` | 关闭 | 不修改文件系统时间 |
| `--workers` | `4` | 并行复制 / ExifTool 任务数 |
| `--dry-run` | 关闭 | 只打印将执行的 ExifTool 命令，**不复制也不修改任何文件** |

注意：

- **时区偏移必须带符号**。`10:00` 会直接报错而不是被猜成某个值。
- argparse 无法把以 `-` 开头的值当作参数值，所以负偏移要写成 `--from-offset=-08:00` 这种带等号的形式。
- 偏移量为 0 且没有 `--set-offset` 时，脚本会报错退出，因为没有任何事情要做。

退出码：`0` 成功，`1` 没有匹配文件，`2` 参数错误，`10` 有文件失败，`130` 被中断。

## 命令示例

| 场景 | 命令 |
|---|---|
| 修复 DST（相机记为 UTC-8，实际 UTC-7） | `python fix_photo_time.py -i input -o output --from-offset=-08:00 --to-offset=-07:00 --set-offset=-07:00` |
| 时区转换（东京 UTC+9 → 美西 UTC-7） | `python fix_photo_time.py -i input -o output --from-offset=+09:00 --to-offset=-07:00 --set-offset=-07:00` |
| 手动偏移 +1 小时 | `python fix_photo_time.py -i input -o output --shift=+01:00:00 --set-offset=-07:00` |
| 手动偏移 -30 分 15 秒 | `python fix_photo_time.py -i input -o output --shift=-00:30:15` |
| 只写 EXIF 时区，不改时间 | `python fix_photo_time.py -i input -o output --shift=+00:00:00 --set-offset=-07:00` |
| 原地修改，不复制 | `python fix_photo_time.py -i input --in-place --shift=+01:00:00` |
| Dry Run | `python fix_photo_time.py -i input -o output --shift=+01:00:00 --dry-run` |

## 结果验证

```bash
exiftool -time:all -a -G1 -s DSC05267.ARW
```

## 关于 ExifTool 的偏移写法

脚本生成的是 `+=0:0:0 1:00:00` 这种 `Y:M:D h:m:s` 完整写法，而不是 `+=1:00:00`。
ExifTool 对只给一个参数的写法会按值的类型来猜：`1:00:00` 作用在 date/time 值上是"1 小时"，
但作用在只有日期的值（例如某些文件的 `XMP:DateCreated`）上会被理解为"1 年"。
写全两段可以消除这种歧义。
