
# Dataset setup

## Usage

### 1. Calibrate once on your dataset (percentile-based min/max per axis)
```sh
python beat2_to_ohbot.py calibrate --beat2-root ./BEAT2 --out-dir ./ohbot_data
```

### 2. Convert every matched (motion, audio) pair
```sh
python beat2_to_ohbot.py convert --beat2-root ./BEAT2 --out-dir ./ohbot_data --control-hz 20
```

# Training pipeline: audio -> Ohbot servo motion

Files:
- `features.py` -- log-mel spectrogram extraction, frame-aligned to your control_hz (pure numpy, tested)
- `ohbot_dataset.py` -- PyTorch `Dataset` reading `manifest.csv`/`clips/*.npz` from `beat2_to_ohbot.py`
- `model.py` -- small Conv1d + GRU model, outputs 7 (or 8) servo values in [0, 10]
- `train.py` -- training loop, with a **pipeline smoke test** built in

## Install

```bash
pip install torch numpy
```
(no torchaudio/librosa needed -- feature extraction is plain numpy)

## Step 1: verify the pipeline, not the model

This is the important step given what you said you want right now. Run:

```bash
python train.py --data-dir ./ohbot_data --overfit-one-batch --epochs 200
```

This grabs one batch of clips and trains repeatedly only on those, with no
train/val split. If everything is wired correctly (data loads, features
align with motion targets, shapes match, gradients flow), a model this
small should be able to memorize one batch and drive `train_loss` down
close to 0 within a couple hundred epochs -- that's the whole point of an
overfit test: it isolates "is the plumbing correct?" from "does the model
generalize?".

If the loss plateaus far above 0 (and above the printed baseline number),
something upstream is broken -- most likely candidates, in order of
likelihood:
1. Feature/label misalignment (check `--n-mels`, hop length vs `control_hz`)
2. A bug in `collate_fn` padding/masking
3. Data itself has near-zero signal (e.g. all-constant motion channels --
   this can legitimately happen for axes like `EYETILT` if your BEAT2
   subset barely moves the eyes vertically)

## Step 2: a real (still fast) training run

```bash
python train.py --data-dir ./ohbot_data --epochs 30 --batch-size 4
```

This does a real train/val split (`--val-frac`, default 0.15), trains the
same small model, and saves the best checkpoint (by val loss) to
`./training_run/best_model.pt`. Watch that `val_loss` actually drops below
the printed baseline ("always predict rest position") -- if it doesn't,
the model isn't learning anything useful yet.

Useful flags for quick iteration:
- `--limit 20` -- only use the first 20 clips (fast iteration on pipeline changes)
- `--n-mels`, `--conv-channels`, `--gru-hidden` -- model size knobs, all small by default
- `--device cpu` / `--device cuda` -- override auto-detection

## What's NOT covered yet (next steps once this works)

- An inference script that takes a new `.wav`, runs the model, and turns
  the (T, 7) output into real `ohbot.move()` calls at the right control
  rate -- happy to write this once you've confirmed training works.
- Scaling up model size/data once the small model verifies the pipeline.
- Proper train/val/test splits by *speaker* (not just by clip) if you want
  to test generalization to a held-out voice, since BEAT2 clips from the
  same speaker will otherwise leak into both splits.
