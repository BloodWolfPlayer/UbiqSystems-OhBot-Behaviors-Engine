#!/usr/bin/env python3
"""
beat2_to_ohbot.py

Convert the BEAT2 dataset (SMPL-X body + FLAME face motion capture, paired
with speech audio) into training data for an Ohbot robot head:

    audio waveform  -->  [HEADNOD, HEADTURN, EYETURN, EYETILT, LIDBLINK,
                          TOPLIP, BOTTOMLIP]   (each 0-10, Ohbot's native range)

------------------------------------------------------------------------------
WHAT THIS SCRIPT ASSUMES ABOUT THE DATA (please sanity-check on your copy)
------------------------------------------------------------------------------
BEAT2 motion files (*.npz) contain, per the EMAGE/PantoMatrix release format:
    poses        : (T, 165) or (T, 55, 3)  axis-angle rotations, SMPL-X's
                   standard 55-joint order (pelvis ... jaw, leye, reye, hands)
    expressions  : (T, 100)  FLAME expression PCA coefficients
    trans        : (T, 3)    global translation
    betas        : (300,) or similar        body shape (unused here)
    mocap_frame_rate : usually 30.0

Audio is 16kHz mono speech, one .wav per .npz, matched by filename stem.

The exact subfolder layout of different BEAT2 releases has varied
(e.g. <speaker>/smplxflame_30/*.npz + <speaker>/wave16k/*.wav, or flatter
layouts). Rather than hard-code one layout, this script WALKS the whole
root directory, indexes every .npz and .wav it finds by filename stem, and
pairs them up. Point --beat2-root at whatever directory you unzipped/cloned
BEAT2 into and it should find everything.

------------------------------------------------------------------------------
SMPL-X JOINT INDICES USED (standard 55-joint order)
------------------------------------------------------------------------------
12 = neck, 15 = head, 22 = jaw, 23 = left_eye, 24 = right_eye
(neck and head are composed together to approximate overall head
orientation; jaw and the eyes are already expressed relative to the head
joint, which is exactly what we want for EYETURN/EYETILT/mouth.)

------------------------------------------------------------------------------
KNOWN LIMITATIONS (read before trusting the output blindly)
------------------------------------------------------------------------------
1. Blinking (LIDBLINK): FLAME's expression PCA basis does not have a labeled
   "eyelid" channel, so blink amount cannot be reliably pulled out of
   `expressions` without fitting the actual FLAME mesh and measuring eyelid
   vertex distance (out of scope here). LIDBLINK is set to a constant
   "eyes open" value by default. See `--lidblink-default` and the
   `estimate_blink_placeholder()` function if you want to extend this later.

2. Axis semantics (which Euler channel is "nod" vs "turn" vs "roll", and
   which sign means "up" vs "down"): these depend on SMPL-X's rest-pose
   convention and can't be verified without looking at real data. Run with
   --debug-plot on one known clip first (e.g. one where the speaker is
   clearly nodding) and confirm the channel/sign before trusting the
   calibration. You may need to flip a sign with --invert-axis.

3. Mouth mapping is a heuristic: SMPL-X/FLAME jaw rotation (mouth opening)
   is split between TOPLIP and BOTTOMLIP using --top-lip-ratio (default 0.25,
   i.e. the bottom lip does most of the opening, like a real jaw), not a
   physically derived quantity.

------------------------------------------------------------------------------
USAGE
------------------------------------------------------------------------------
# 1) Calibrate: scan the dataset once to get robust per-axis min/max
python beat2_to_ohbot.py calibrate --beat2-root /path/to/BEAT2 --out-dir ./ohbot_data

# 2) Convert: extract every matched (motion, audio) pair into Ohbot units
python beat2_to_ohbot.py convert --beat2-root /path/to/BEAT2 --out-dir ./ohbot_data

# 3) (optional) sanity-check axis semantics on one file
python beat2_to_ohbot.py debug-plot --npz /path/to/one_clip.npz --out-dir ./ohbot_data

Requires: numpy, scipy, soundfile  (pip install numpy scipy soundfile)
Optional: matplotlib (only for --debug-plot)
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial.transform import Rotation as R

try:
    import soundfile as sf
except ImportError:
    sf = None

try:
    from scipy.io import wavfile as scipy_wavfile
except ImportError:
    scipy_wavfile = None


# ==============================================================================
# Constants
# ==============================================================================

# SMPL-X 55-joint order, indices we need (standard order used by BEAT2/EMAGE)
J_NECK, J_HEAD, J_JAW, J_LEYE, J_REYE = 12, 15, 22, 23, 24

# Ohbot servo axis names, matching ohbot-python's HEADNOD/HEADTURN/... constants.
# Ohbot's native command range for every motor is 0-10 (see ohbot.move(axis, 0-10)).
OHBOT_AXES = ["HEADNOD", "HEADTURN", "EYETURN", "EYETILT", "LIDBLINK", "TOPLIP", "BOTTOMLIP"]
OHBOT_AXES_WITH_ROLL = OHBOT_AXES + ["HEADROLL"]  # optional 8th servo on some builds

OHBOT_MIN, OHBOT_MAX, OHBOT_REST = 0.0, 10.0, 5.0


# ==============================================================================
# Loading BEAT2 files
# ==============================================================================

def find_beat2_pairs(root):
    """Walk `root`, index every .npz and .wav by filename stem, and return
    matched (npz_path, wav_path) pairs. Prints a warning for anything that
    doesn't have a match on the other side (so you can check your data layout
    if the match rate looks low)."""
    npz_by_stem, wav_by_stem = {}, {}
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            stem, ext = os.path.splitext(fn)
            full = os.path.join(dirpath, fn)
            if ext.lower() == ".npz":
                npz_by_stem[stem] = full
            elif ext.lower() == ".wav":
                wav_by_stem[stem] = full

    common = sorted(set(npz_by_stem) & set(wav_by_stem))
    only_npz = sorted(set(npz_by_stem) - set(wav_by_stem))
    only_wav = sorted(set(wav_by_stem) - set(npz_by_stem))

    print(f"[find_beat2_pairs] {len(npz_by_stem)} npz files, {len(wav_by_stem)} wav files, "
          f"{len(common)} matched pairs.")
    if only_npz:
        print(f"[find_beat2_pairs] WARNING: {len(only_npz)} .npz files had no matching .wav "
              f"(e.g. {only_npz[:3]})")
    if only_wav:
        print(f"[find_beat2_pairs] WARNING: {len(only_wav)} .wav files had no matching .npz "
              f"(e.g. {only_wav[:3]})")

    return [(npz_by_stem[s], wav_by_stem[s]) for s in common]


def load_motion_npz(path):
    """Load a BEAT2 motion .npz and return poses reshaped to (T, 55, 3)
    axis-angle, plus fps. Raises a clear error if expected keys are missing,
    rather than failing cryptically deep in the pipeline."""
    data = np.load(path, allow_pickle=True)
    keys = set(data.keys())
    if "poses" not in keys:
        raise KeyError(f"{path}: expected key 'poses' not found, got keys={sorted(keys)}. "
                        f"Your BEAT2 release may use a different npz schema than assumed here "
                        f"-- open the file and check np.load(path).files")
    poses = np.asarray(data["poses"], dtype=np.float32)
    if poses.ndim == 2 and poses.shape[1] % 3 == 0:
        n_joints = poses.shape[1] // 3
        poses = poses.reshape(poses.shape[0], n_joints, 3)
    elif poses.ndim != 3:
        raise ValueError(f"{path}: unexpected poses shape {poses.shape}")

    if poses.shape[1] < J_REYE + 1:
        raise ValueError(f"{path}: poses has only {poses.shape[1]} joints, need at least "
                          f"{J_REYE + 1} (right_eye) for this mapping.")

    fps = float(np.asarray(data["mocap_frame_rate"]).reshape(-1)[0]) if "mocap_frame_rate" in keys else 30.0
    return poses, fps


# ==============================================================================
# Motion -> raw angle channels (degrees), before any Ohbot-unit normalization
# ==============================================================================

def _rotvec_to_euler_deg(rotvec, order="YXZ"):
    """rotvec: (T, 3) axis-angle -> (T, 3) euler angles in degrees, given order."""
    return R.from_rotvec(rotvec).as_euler(order, degrees=True)


def extract_raw_channels(poses, top_lip_ratio=0.25):
    """
    poses: (T, 55, 3) axis-angle
    Returns a dict of raw (uncalibrated) per-frame channels in degrees / arbitrary
    units, one array of shape (T,) per Ohbot axis (except LIDBLINK, which has
    no reliable signal here -- see module docstring).
    """
    T = poses.shape[0]

    # Head orientation: compose neck (parent) then head (child) local rotations
    # to approximate overall head pose relative to the upper spine.
    r_neck = R.from_rotvec(poses[:, J_NECK, :])
    r_head_local = R.from_rotvec(poses[:, J_HEAD, :])
    r_head_global = r_neck * r_head_local
    head_euler = r_head_global.as_euler("YXZ", degrees=True)  # [:,0]=yaw,[:,1]=pitch,[:,2]=roll

    head_turn = head_euler[:, 0]   # yaw   -> HEADTURN
    head_nod = head_euler[:, 1]    # pitch -> HEADNOD
    head_roll = head_euler[:, 2]   # roll  -> HEADROLL (optional)

    # Eyes: already local to the head joint, so this is gaze *relative to head*,
    # which is what EYETURN/EYETILT should represent. Average L/R since Ohbot
    # only has one shared pair of eye servos.
    leye_euler = _rotvec_to_euler_deg(poses[:, J_LEYE, :], order="YXZ")
    reye_euler = _rotvec_to_euler_deg(poses[:, J_REYE, :], order="YXZ")
    eye_turn = (leye_euler[:, 0] + reye_euler[:, 0]) / 2.0   # yaw   -> EYETURN
    eye_tilt = (leye_euler[:, 1] + reye_euler[:, 1]) / 2.0   # pitch -> EYETILT

    # Jaw: mouth-opening magnitude, relative to head. Use the pitch component.
    jaw_euler = _rotvec_to_euler_deg(poses[:, J_JAW, :], order="YXZ")
    mouth_open = np.abs(jaw_euler[:, 1])  # magnitude only; sign isn't meaningful here
    top_lip = mouth_open * top_lip_ratio
    bottom_lip = mouth_open * (1.0 - top_lip_ratio)

    return {
        "HEADNOD": head_nod,
        "HEADTURN": head_turn,
        "HEADROLL": head_roll,
        "EYETURN": eye_turn,
        "EYETILT": eye_tilt,
        "TOPLIP": top_lip,
        "BOTTOMLIP": bottom_lip,
        # LIDBLINK deliberately omitted -- see module docstring / estimate_blink_placeholder()
    }


def estimate_blink_placeholder(n_frames, value=8.0):
    """Placeholder for LIDBLINK: a constant "mostly open eyes" signal.
    Replace this with a real extraction (e.g. FLAME eyelid-vertex distance)
    if/when you have a reliable ground-truth blink signal. `value` is in
    Ohbot's native 0-10 units already (not raw degrees), since there's no
    real calibration to normalize."""
    return np.full(n_frames, value, dtype=np.float32)


# ==============================================================================
# Calibration: per-axis percentile-based min/max across the whole dataset
# ==============================================================================

def calibrate(pairs, top_lip_ratio, sample_every=1, pct=(1.0, 99.0)):
    """First pass over the dataset: collect raw channel values so we can map
    them to Ohbot's 0-10 range using robust (percentile-based) min/max instead
    of a handful of outlier frames blowing up the whole scale."""
    collected = {ax: [] for ax in OHBOT_AXES_WITH_ROLL if ax != "LIDBLINK"}

    for i, (npz_path, _wav_path) in enumerate(pairs):
        try:
            poses, _fps = load_motion_npz(npz_path)
        except Exception as e:
            print(f"[calibrate] skipping {npz_path}: {e}")
            continue
        raw = extract_raw_channels(poses, top_lip_ratio=top_lip_ratio)
        for ax in collected:
            collected[ax].append(raw[ax][::sample_every])
        if (i + 1) % 100 == 0:
            print(f"[calibrate] scanned {i + 1}/{len(pairs)} files")

    calibration = {}
    for ax, chunks in collected.items():
        if not chunks:
            continue
        all_vals = np.concatenate(chunks)
        lo, hi = np.percentile(all_vals, pct)
        if hi - lo < 1e-6:  # degenerate axis (e.g. no motion at all)
            hi = lo + 1e-6
        calibration[ax] = {"lo": float(lo), "hi": float(hi)}
        print(f"[calibrate] {ax:10s} raw range (p{pct[0]:.0f}-p{pct[1]:.0f}): "
              f"[{lo:.2f}, {hi:.2f}] degrees")

    return calibration


def normalize_to_ohbot(raw_value, lo, hi, invert=False):
    """Linearly map raw_value from [lo, hi] -> Ohbot's [0, 10], clipping
    outliers. If invert=True, flips which end is 0 vs 10 (use this if you
    discover an axis moves the wrong direction during --debug-plot review)."""
    x = np.clip((raw_value - lo) / (hi - lo), 0.0, 1.0)
    if invert:
        x = 1.0 - x
    return x * (OHBOT_MAX - OHBOT_MIN) + OHBOT_MIN


# ==============================================================================
# Resampling motion to a control rate, loading/resampling audio
# ==============================================================================

def resample_channel(x, src_hz, dst_hz):
    """Simple linear-interpolation resample of a 1D signal from src_hz to dst_hz."""
    if src_hz == dst_hz:
        return x.astype(np.float32)
    n_src = len(x)
    duration = n_src / src_hz
    n_dst = max(1, int(round(duration * dst_hz)))
    t_src = np.linspace(0, duration, n_src, endpoint=False)
    t_dst = np.linspace(0, duration, n_dst, endpoint=False)
    return np.interp(t_dst, t_src, x).astype(np.float32)


def load_audio(path, target_sr=16000):
    if sf is not None:
        audio, sr = sf.read(path, dtype="float32", always_2d=False)
    elif scipy_wavfile is not None:
        sr, audio = scipy_wavfile.read(path)
        audio = audio.astype(np.float32)
        if np.issubdtype(audio.dtype, np.integer):
            pass  # already cast above; normalize below regardless of source dtype
        # normalize common PCM int ranges to [-1, 1]
        max_abs = np.max(np.abs(audio)) if audio.size else 1.0
        if max_abs > 1.0:
            audio = audio / 32768.0
    else:
        raise RuntimeError("Neither soundfile nor scipy.io.wavfile is available. "
                            "Run: pip install soundfile")
    if audio.ndim > 1:  # stereo -> mono
        audio = audio.mean(axis=1)
    if sr != target_sr:
        # Lightweight resample without extra deps; for best audio quality
        # for real ML training, consider using librosa.resample instead.
        duration = len(audio) / sr
        n_dst = int(round(duration * target_sr))
        t_src = np.linspace(0, duration, len(audio), endpoint=False)
        t_dst = np.linspace(0, duration, n_dst, endpoint=False)
        audio = np.interp(t_dst, t_src, audio).astype(np.float32)
        sr = target_sr
    return audio.astype(np.float32), sr


# ==============================================================================
# Main conversion pipeline
# ==============================================================================

@dataclass
class ConvertConfig:
    control_hz: float = 20.0
    top_lip_ratio: float = 0.25
    include_headroll: bool = False
    lidblink_default: float = 8.0
    invert_axes: list = field(default_factory=list)  # e.g. ["HEADTURN"]
    audio_sr: int = 16000
    min_duration_s: float = 0.5


def convert_one(npz_path, wav_path, calibration, cfg: ConvertConfig):
    poses, fps = load_motion_npz(npz_path)
    raw = extract_raw_channels(poses, top_lip_ratio=cfg.top_lip_ratio)

    axes = list(OHBOT_AXES)
    if cfg.include_headroll:
        axes.append("HEADROLL")

    T = poses.shape[0]
    duration = T / fps
    if duration < cfg.min_duration_s:
        raise ValueError(f"clip too short ({duration:.2f}s)")

    n_ctrl = max(1, int(round(duration * cfg.control_hz)))
    motion_out = np.zeros((n_ctrl, len(axes)), dtype=np.float32)

    for j, ax in enumerate(axes):
        if ax == "LIDBLINK":
            motion_out[:, j] = estimate_blink_placeholder(n_ctrl, cfg.lidblink_default)
            continue
        resampled = resample_channel(raw[ax], fps, cfg.control_hz)
        # pad/truncate in case rounding made lengths differ by a frame
        if len(resampled) < n_ctrl:
            resampled = np.pad(resampled, (0, n_ctrl - len(resampled)), mode="edge")
        resampled = resampled[:n_ctrl]
        cal = calibration.get(ax)
        if cal is None:
            raise ValueError(f"no calibration found for axis {ax}; run `calibrate` first")
        invert = ax in cfg.invert_axes
        motion_out[:, j] = normalize_to_ohbot(resampled, cal["lo"], cal["hi"], invert=invert)

    audio, sr = load_audio(wav_path, target_sr=cfg.audio_sr)

    return {
        "ohbot_motion": motion_out,        # (n_ctrl, len(axes)), values in [0, 10]
        "ohbot_axis_names": np.array(axes),
        "control_hz": np.float32(cfg.control_hz),
        "audio": audio,                    # (n_samples,) float32, mono
        "audio_sr": np.int32(sr),
        "source_motion_file": os.path.basename(npz_path),
        "source_audio_file": os.path.basename(wav_path),
    }


def run_calibrate(args):
    pairs = find_beat2_pairs(args.beat2_root)
    if args.limit:
        pairs = pairs[: args.limit]
    calibration = calibrate(pairs, top_lip_ratio=args.top_lip_ratio, sample_every=args.sample_every)
    os.makedirs(args.out_dir, exist_ok=True)
    cal_path = os.path.join(args.out_dir, "calibration.json")
    with open(cal_path, "w") as f:
        json.dump(calibration, f, indent=2)
    print(f"[calibrate] wrote {cal_path}")


def run_convert(args):
    cal_path = args.calibration_file or os.path.join(args.out_dir, "calibration.json")
    if not os.path.exists(cal_path):
        print(f"ERROR: calibration file not found at {cal_path}. Run `calibrate` first.")
        sys.exit(1)
    with open(cal_path) as f:
        calibration = json.load(f)

    cfg = ConvertConfig(
        control_hz=args.control_hz,
        top_lip_ratio=args.top_lip_ratio,
        include_headroll=args.include_headroll,
        lidblink_default=args.lidblink_default,
        invert_axes=args.invert_axis or [],
        audio_sr=args.audio_sr,
    )

    pairs = find_beat2_pairs(args.beat2_root)
    if args.limit:
        pairs = pairs[: args.limit]

    out_motion_dir = os.path.join(args.out_dir, "clips")
    os.makedirs(out_motion_dir, exist_ok=True)

    manifest_path = os.path.join(args.out_dir, "manifest.csv")
    n_ok, n_fail = 0, 0
    with open(manifest_path, "w") as manifest:
        manifest.write("clip_id,out_path,source_motion_file,source_audio_file,n_frames,duration_s\n")
        for npz_path, wav_path in pairs:
            clip_id = os.path.splitext(os.path.basename(npz_path))[0]
            try:
                result = convert_one(npz_path, wav_path, calibration, cfg)
            except Exception as e:
                print(f"[convert] skipping {clip_id}: {e}")
                n_fail += 1
                continue
            out_path = os.path.join(out_motion_dir, f"{clip_id}.npz")
            np.savez_compressed(out_path, **result)
            n_frames = result["ohbot_motion"].shape[0]
            duration_s = len(result["audio"]) / float(result["audio_sr"])
            manifest.write(f"{clip_id},{out_path},{result['source_motion_file']},"
                            f"{result['source_audio_file']},{n_frames},{duration_s:.3f}\n")
            n_ok += 1
            if n_ok % 100 == 0:
                print(f"[convert] {n_ok} clips done")

    print(f"[convert] done. {n_ok} clips written, {n_fail} skipped. Manifest: {manifest_path}")


def run_debug_plot(args):
    """Plot raw (uncalibrated) per-axis angle curves for one clip, so you can
    visually confirm which channel corresponds to nod/turn/roll and which
    sign is which, before trusting the calibration on the whole dataset."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    poses, fps = load_motion_npz(args.npz)
    raw = extract_raw_channels(poses, top_lip_ratio=args.top_lip_ratio)
    t = np.arange(poses.shape[0]) / fps

    fig, axs = plt.subplots(len(raw), 1, figsize=(10, 2 * len(raw)), sharex=True)
    for ax, (name, values) in zip(axs, raw.items()):
        ax.plot(t, values)
        ax.set_ylabel(name)
        ax.grid(True, alpha=0.3)
    axs[-1].set_xlabel("time (s)")
    fig.tight_layout()

    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, "debug_plot.png")
    fig.savefig(out_path, dpi=120)
    print(f"[debug-plot] wrote {out_path} -- compare against the video/rendering of this "
          f"clip to confirm axis semantics before trusting calibration.")


# ==============================================================================
# CLI
# ==============================================================================

def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--beat2-root", required=True, help="Root folder of your BEAT2 download")
    common.add_argument("--out-dir", required=True, help="Where to write calibration/clips/manifest")
    common.add_argument("--top-lip-ratio", type=float, default=0.25,
                         help="Fraction of mouth-opening assigned to TOPLIP vs BOTTOMLIP (default 0.25)")
    common.add_argument("--limit", type=int, default=None, help="Only process the first N pairs (for testing)")

    p_cal = sub.add_parser("calibrate", parents=[common], help="Scan dataset, compute per-axis min/max")
    p_cal.add_argument("--sample-every", type=int, default=1, help="Subsample frames for speed (e.g. 5)")
    p_cal.set_defaults(func=run_calibrate)

    p_conv = sub.add_parser("convert", parents=[common], help="Convert matched (motion, audio) pairs")
    p_conv.add_argument("--calibration-file", default=None, help="Defaults to <out-dir>/calibration.json")
    p_conv.add_argument("--control-hz", type=float, default=20.0, help="Output control rate for servo signals")
    p_conv.add_argument("--include-headroll", action="store_true",
                         help="Also output HEADROLL (only if your Ohbot build has that 8th servo)")
    p_conv.add_argument("--lidblink-default", type=float, default=8.0,
                         help="Constant LIDBLINK value in 0-10 units (see limitations in module docstring)")
    p_conv.add_argument("--invert-axis", action="append",
                         help="Axis name to invert (0<->10), e.g. --invert-axis HEADTURN. Repeatable.")
    p_conv.add_argument("--audio-sr", type=int, default=16000)
    p_conv.set_defaults(func=run_convert)

    p_dbg = sub.add_parser("debug-plot", parents=[common], help="Plot raw angle curves for one clip")
    p_dbg.add_argument("--npz", required=True, help="Path to a single BEAT2 motion .npz to inspect")
    p_dbg.set_defaults(func=run_debug_plot)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()