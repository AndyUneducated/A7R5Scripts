# mediakit

照片压缩、视频切分/重组、拍摄时间修复。一条 CLI：

```bash
mediakit photo shrink ./DCIM ./out
mediakit video split movie.mp4 --duration 600
mediakit video merge ./parts -o movie.mp4 --verify -y
```

设计原则：**无损优先，绝不静默转码**；**先计划后执行**（`--dry-run`）；照片与视频后端不同，只共享扫描、计划与进度。

---

## 两种最常用场景

把路径换成你的文件即可。视频命令加 `-y` 跳过确认；想先看命令再执行可加 `--dry-run`。

### ① 照片批量压缩（社交分享 / 归档）

递归扫描、镜像目录、保留 EXIF。默认 HEIF，最长边 6000px：

```bash
mediakit photo shrink ./DCIM ./out
mediakit photo shrink ./DCIM ./out --max-edge 2048 --quality 75
mediakit photo shrink ./DCIM ./out --out-format jpg --max-edge 3072 --max-size 3mb
```

支持 JPEG / PNG / HEIF / Sony RAW (`.arw`)。Orientation 烘进像素；RAW 的 EXIF 取自内嵌 JPEG 预览。

### ② 视频切分 / 无损合并

整片压成一个小视频（默认 720p）：

```bash
mediakit video shrink movie.mp4 -y
# → movie_720p.mp4
```

按时长或按大小切开：

```bash
mediakit video split movie.mp4 --duration 600          # 每段 10 分钟，默认无损
mediakit video split movie.mp4 --size 200               # 每段 ≤200MB，默认转码
mediakit video split movie.mp4 --profile social -y      # ≤30s、1080p、H.264
```

分段无损拼回一部电影（参数不一致会直接报错，绝不偷偷转码）：

```bash
mediakit video merge part1.mp4 part2.mp4 -o movie.mp4 --verify -y
mediakit video merge ./parts/ -o movie.mp4 --verify -y
```

---

## 安装

```bash
# 系统依赖
brew install ffmpeg exiftool libheif   # macOS

# 本工具
pip install -e ".[dev]"
```

也可以 `python -m mediakit …`。旧命令 `vclip duration …` 仍可用，等价于 `mediakit video duration …`。

需要 Python 3.10+。视频能力本体无第三方依赖，调用系统里的 ffmpeg / ffprobe。

运行测试：

```bash
python -m pytest tests
```

---

## 命令一览

| 命令 | 作用 |
|---|---|
| `mediakit photo shrink IN OUT` | 批量压缩照片 |
| `mediakit video shrink FILE` | 整片压缩为单个小视频（默认 720p） |
| `mediakit video split FILE --duration N` | 按时长切分（默认无损） |
| `mediakit video split FILE --size MB` | 按大小切分（默认转码） |
| `mediakit video split FILE --profile social\|share` | 社交 / 分享预设 |
| `mediakit video trim FILE --from S [--to S]` | 裁剪子片段（默认无损） |
| `mediakit video merge INPUTS -o OUT` | 无损重组 |
| `mediakit video verify WHOLE PARTS` | 逐帧校验拼接 |
| `mediakit video info FILE` | 分辨率 / 码率 / HDR 等 |
| `mediakit video caps` | 本机 ffmpeg 能力 |
| `mediakit time -i DIR --shift=+01:00:00` | 修复拍摄时间（不重新编码） |

视频切分的旧子命令仍可用：`size`、`duration`、`social`、`share`。

通用开关：`--dry-run` 只打印计划；视频写文件的命令加 `-y` 跳过确认。

---

## 照片压缩

处理流程：扫描（跳过隐藏文件与 `._xxx`）→ 解码 → Orientation 烘进像素 → 透明图铺背景 → 缩放或按体积搜索 → 编码 HEIF/JPEG，保留 EXIF 与 ICC → 镜像写出。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--out-format` | `heif` | `heif` 或 `jpg` |
| `--max-edge` | `6000` | 最长边；设了 `--max-size` 时默认不限制 |
| `--quality` | `80` | JPEG 1–95，HEIF 1–100 |
| `--max-size` | 无 | 体积上限（如 `3mb`）。先降质量、再降分辨率 |
| `--naming` | `source-ext` | `DSC1.arw.heic`；`plain` 则为 `DSC1.heic` |
| `--workers` | CPU 核数（上限 8） | 并行进程数 |
| `--overwrite` | 关 | 覆盖已存在的输出 |
| `--strip` | 关 | 不写 EXIF / ICC |

退出码：`0` 成功，`1` 没有可处理文件，`2` 参数错误，`3` HEIF 不可用，`10` 有文件失败，`130` 被中断。

---

## 视频

底层调用 ffmpeg。**`merge` 永远无损**；**`shrink` 永远转码成单个文件**（默认 720p）；**`split --duration` / `trim` 默认无损**（加 `--transcode` 才转码）；**`split --size` 默认转码**（加 `--lossless` 才无损）。

编码选项（转码时生效）：`-r/--resolution`、`--codec`、`--encoder`（auto 优先硬件）、`--crf`、`--bitrate`、`--hdr auto|sdr|keep`、`-j/--jobs`。

`mediakit video caps` 可查看本机软/硬件编码器与 HDR→SDR 能力。

无损合并前会比对编码、分辨率、像素格式、帧率、SAR、音频参数；不一致即退出码 1 并列出差异。`--verify` 再逐帧像素哈希实证。

---

## 拍摄时间修复

不重新编码。需要 ExifTool。适用于相机 DST/时区设错。

```bash
mediakit time -i ./photos -o ./out --from-offset=-08:00 --to-offset=-07:00 --set-offset=-07:00
mediakit time -i ./photos --in-place --shift=+01:00:00
mediakit time -i ./photos -o ./out --shift=+01:00:00 --dry-run
```

负偏移必须写成 `--from-offset=-08:00`（带等号），否则 argparse 会当成开关。

---

## 仓库结构

```
mediakit/
  cli/          命令行派发（photo / video / time）
  core/         扫描、进度、Plan + Reporter
  photo/        解码 / EXIF / ShrinkPlan
  video/        ffmpeg 探测、编码决策、切分、合并、校验
  time/         时间偏移解析与 FixPlan
tests/
```

## 许可证

MIT
