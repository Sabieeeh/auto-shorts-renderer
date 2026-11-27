import os
import shutil
import subprocess
from PIL import Image

def extract_roto_layer(src_mp4: str, out_webm: str, fps: int = 30) -> bool:
    """Pisahkan subjek utama dari latar belakang menjadi video alpha transparan (VP9 yuva420p)."""
    tmp_dir = out_webm + "_frames"
    try:
        from rembg import remove, new_session
        session = new_session("u2netp")
        os.makedirs(tmp_dir, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", src_mp4, "-r", str(fps), "-vf", "scale=540:-2", f"{tmp_dir}/in_%04d.png"],
            check=True,
        )
        frames = sorted(f for f in os.listdir(tmp_dir) if f.startswith("in_") and f.endswith(".png"))
        if not frames:
            return False
        for fn in frames:
            inp = Image.open(os.path.join(tmp_dir, fn)).convert("RGBA")
            out = remove(inp, session=session)
            out.save(os.path.join(tmp_dir, "out_" + fn[3:]))
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-framerate", str(fps),
                "-i", f"{tmp_dir}/out_%04d.png",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "26", "-an", out_webm,
            ],
            check=True,
        )
        return os.path.exists(out_webm) and os.path.getsize(out_webm) > 2000
    except Exception as e:
        print(f"[roto] dilewati ({str(e)[:120]})")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
