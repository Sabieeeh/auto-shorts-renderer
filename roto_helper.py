"""
roto_helper.py — Ekstraksi subjek dengan alpha channel untuk overlay roto_typo.

═══════════════════════════════════════════════════════════════════════════
PERUBAHAN v2.1 (Batch A fix):
  - Verifikasi 3 lapis sebelum pakai webm:
    1. Alpha channel (pix_fmt yuva) valid?
    2. Rasio opaque wajar (8%–92%)?
    3. Ukuran file wajar (>= 15 KB/detik)?
  - Kalau salah satu gagal → return False → pipeline fallback ke KineticTypo
    (tidak ada lagi layar hitam / subjek melayang di frame).
═══════════════════════════════════════════════════════════════════════════
"""

import os
import shutil
import subprocess

import numpy as np
from PIL import Image


def extract_roto_layer(src_mp4: str, out_webm: str, fps: int = 30) -> bool:
    """
    Pisahkan subjek utama dari latar menjadi video alpha transparan (VP9 yuva420p).

    Returns:
        True  jika webm valid (alpha OK, ukuran wajar, rasio opaque wajar)
        False jika gagal / kualitas alpha rusak → caller WAJIB fallback ke
              overlay non-roto (KineticTypo biasa)
    """
    tmp_dir = out_webm + "_frames"
    try:
        from rembg import remove, new_session

        session = new_session("u2netp")
        os.makedirs(tmp_dir, exist_ok=True)

        # 1. Extract frames dari mp4
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", src_mp4, "-r", str(fps),
                "-vf", "scale=540:-2", f"{tmp_dir}/in_%04d.png",
            ],
            check=True,
        )

        frames = sorted(
            f for f in os.listdir(tmp_dir)
            if f.startswith("in_") and f.endswith(".png")
        )
        if not frames:
            print("[roto] tidak ada frame yang diekstrak")
            return False

        # 2. Remove background per frame + validasi alpha
        for fn in frames:
            inp_path = os.path.join(tmp_dir, fn)
            inp = Image.open(inp_path).convert("RGBA")
            out = remove(inp, session=session)

            # FIX BARU #1: validasi rasio opaque (subjek harus ada & tidak
            # menutupi seluruh frame)
            alpha_arr = np.array(out.split()[3]).astype(np.float32)
            opaque_ratio = alpha_arr.mean() / 255.0
            if opaque_ratio < 0.08 or opaque_ratio > 0.92:
                print(
                    f"[roto] frame {fn} alpha tidak wajar "
                    f"({opaque_ratio * 100:.1f}% opaque) — fallback"
                )
                return False

            out.save(os.path.join(tmp_dir, "out_" + fn[3:]))

        # 3. Encode ke VP9 alpha
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                "-i", f"{tmp_dir}/out_%04d.png",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p",
                "-crf", "26", "-an", out_webm,
            ],
            check=True,
        )

        if not os.path.exists(out_webm):
            return False

        # 4. FIX BARU #2: verifikasi pix_fmt webm
        probe_r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=pix_fmt",
                "-of", "csv=p=0", out_webm,
            ],
            capture_output=True, text=True,
        )
        pix_fmt = (probe_r.stdout or "").strip().lower()
        if "yuva" not in pix_fmt:
            print(f"[roto] webm tanpa alpha channel (pix_fmt={pix_fmt}) — fallback")
            return False

        # 5. FIX BARU #3: verifikasi ukuran file (>= 15 KB/detik)
        size_kb = os.path.getsize(out_webm) / 1024
        dur_est = len(frames) / fps
        min_size = dur_est * 15
        if size_kb < min_size:
            print(
                f"[roto] webm terlalu kecil "
                f"({size_kb:.0f} KB untuk {dur_est:.1f} dtk, min {min_size:.0f} KB) — fallback"
            )
            return False

        print(f"[roto] webm valid: {size_kb:.0f} KB, {dur_est:.1f} dtk, pix_fmt={pix_fmt}")
        return True

    except Exception as e:
        print(f"[roto] dilewati ({str(e)[:160]})")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)