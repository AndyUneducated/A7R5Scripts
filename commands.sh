# batch_shrink
python3 batch_shrink.py ./input ./output

python3 batch_shrink.py ./input ./output --out-format heif --quality 25 --max-edge 6000

python3 batch_shrink.py ./input ./output --out-format jpg --quality 25 --strip

python3 batch_shrink.py ./input ./output --out-format jpg --quality 25 --keep-orientation-only

# fix_timezone
python3 fix_timezone.py -i ./input -o ./output
