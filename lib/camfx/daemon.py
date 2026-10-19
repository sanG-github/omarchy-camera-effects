"""Virtual camera daemon.

Feeds the v4l2loopback device at all times so apps can list it, but opens
the real camera only while some other process has the virtual one open.
Like a Mac, the green light is on only while a call is.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

from . import config
from . import effects as fx

CAPTURE_W, CAPTURE_H = 1760, 1328
IDLE_FPS = 2
POLL_SECONDS = 0.5
RELEASE_SECONDS = 3.0


def log(*args):
    print(*args, file=sys.stderr, flush=True)


def find_camera(setting):
    if setting != "auto":
        return setting
    for node in sorted(Path("/sys/class/video4linux").glob("video*")):
        name = (node / "name").read_text().strip()
        if name == "apple-isp" or "FaceTime" in name:
            return f"/dev/{node.name}"
    for node in sorted(Path("/sys/class/video4linux").glob("video*")):
        if f"/dev/{node.name}" != config.VIRTUAL_DEVICE and "loopback" not in \
                (node / "name").read_text().lower():
            return f"/dev/{node.name}"
    raise RuntimeError("no camera found")


def consumers(device, ours):
    """Processes, other than our own, holding the virtual camera open."""
    found = {}
    target = os.path.realpath(device)
    for pid in os.listdir("/proc"):
        if not pid.isdigit() or int(pid) in ours:
            continue
        try:
            for fd in os.listdir(f"/proc/{pid}/fd"):
                if os.readlink(f"/proc/{pid}/fd/{fd}") == target:
                    found[int(pid)] = Path(f"/proc/{pid}/comm").read_text().strip()
                    break
        except OSError:
            continue
    return found


class Output:
    """ffmpeg writing BGR frames into the loopback device."""

    def __init__(self, device, w, h, fps):
        self.size = (w, h)
        self.proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24",
             "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
             "-f", "v4l2", "-pix_fmt", "yuv420p", device],
            stdin=subprocess.PIPE)

    def write(self, frame):
        self.proc.stdin.write(frame.data)
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait(timeout=5)


class Capture:
    """The real camera in its full-sensor 4:3 mode, read raw with v4l2-ctl:
    16:9 modes crop the sensor, and Center Stage needs the whole of it to
    pan into. A thread keeps only the newest frame so a slow effect drops
    frames instead of falling behind."""

    def __init__(self, device):
        subprocess.run(["v4l2-ctl", "-d", device, "--set-fmt-video",
                        f"width={CAPTURE_W},height={CAPTURE_H},pixelformat=NV12"],
                       check=True, capture_output=True)
        fmt = subprocess.run(["v4l2-ctl", "-d", device, "--get-fmt-video"],
                             check=True, capture_output=True, text=True).stdout
        self.stride = int(re.search(r"Bytes per Line\s*:\s*(\d+)", fmt).group(1))
        self.proc = subprocess.Popen(
            ["v4l2-ctl", "-d", device, "--stream-mmap=4", "--stream-count=0", "--stream-to=-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        self.frame = None
        self.seq = 0
        self.cond = threading.Condition()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        rows = CAPTURE_H * 3 // 2
        size = self.stride * rows
        while True:
            buf = bytearray(size)
            view, got = memoryview(buf), 0
            while got < size:
                n = self.proc.stdout.readinto(view[got:])
                if not n:
                    with self.cond:
                        self.frame = None
                        self.seq = -1
                        self.cond.notify_all()
                    return
                got += n
            nv12 = np.frombuffer(buf, np.uint8).reshape(rows, self.stride)[:, :CAPTURE_W]
            frame = cv2.cvtColor(np.ascontiguousarray(nv12), cv2.COLOR_YUV2BGR_NV12)
            with self.cond:
                self.frame = frame
                self.seq += 1
                self.cond.notify_all()

    def next(self, after, timeout=2.0):
        with self.cond:
            self.cond.wait_for(lambda: self.seq != after, timeout)
            if self.seq < 0 or self.seq == after:
                raise RuntimeError("camera stream ended")
            return self.seq, self.frame

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Daemon:
    def __init__(self):
        self.cfg = config.load()
        self.cfg_mtime = self._mtime()
        self.running = True
        self.status = {"state": "idle", "fps": 0, "consumers": [],
                       "device": config.VIRTUAL_DEVICE}
        models = config.MODELS_DIR
        self.faces = fx.FaceDetector(str(models / "face_detection_yunet_2023mar.onnx"))
        self.matter = fx.Matter(str(models / "rvm_mobilenetv3_fp32.onnx"))
        self.pool = ThreadPoolExecutor(1)
        self.framer = None
        self.background = None
        self.background_key = None

    def _mtime(self):
        try:
            return config.CONFIG_FILE.stat().st_mtime
        except OSError:
            return 0

    def reload_config(self):
        m = self._mtime()
        if m != self.cfg_mtime:
            self.cfg_mtime = m
            old = self.cfg
            self.cfg = config.load()
            log("config reloaded")
            return any(old[k] != self.cfg[k] for k in ("width", "height", "fps"))
        return False

    def publish(self, **kw):
        self.status.update(kw, updated=time.time(), config=self.cfg)
        config.write_status(self.status)

    def stop(self, *_):
        self.running = False

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        while self.running:
            w, h, fps = self.cfg["width"], self.cfg["height"], self.cfg["fps"]
            out = Output(config.VIRTUAL_DEVICE, w, h, fps)
            try:
                self.serve(out, w, h)
            finally:
                out.close()
        self.publish(state="stopped", fps=0, consumers=[])

    def ours(self, out, cap=None):
        pids = {os.getpid(), out.proc.pid}
        if cap:
            pids.add(cap.proc.pid)
        return pids

    def serve(self, out, w, h):
        """Idle until someone opens the virtual camera, then stream."""
        black = np.zeros((h, w, 3), np.uint8)
        seen = 0
        while self.running:
            if self.reload_config():
                return
            users = consumers(config.VIRTUAL_DEVICE, self.ours(out))
            # Two polls in a row, so a device probe does not light the camera.
            seen = seen + 1 if users else 0
            if seen >= 2:
                if not self.stream(out, w, h):
                    return
                seen = 0
                continue
            self.publish(state="idle", fps=0, consumers=sorted(set(users.values())))
            for _ in range(int(POLL_SECONDS * IDLE_FPS) or 1):
                out.write(black)
                time.sleep(1 / IDLE_FPS)

    def stream(self, out, w, h):
        """Run the camera while it has users. Returns False when the output
        has to be rebuilt for a new size."""
        try:
            camera = find_camera(self.cfg["camera"])
            cap = Capture(camera)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
            self.publish(state="error", error=str(e))
            time.sleep(2)
            return True
        log("camera on:", camera)
        self.framer = fx.Framer(w / h)
        self.matter.reset()
        seq, frames, t0 = 0, 0, time.monotonic()
        last_user, last_poll, names = time.monotonic(), 0, []
        pending = None   # matte of the next frame, computed while this one renders
        try:
            seq, raw = cap.next(seq)
            frame, faces = self.prepare(raw, w, h)
            while self.running:
                now = time.monotonic()
                if now - last_poll > POLL_SECONDS:
                    last_poll = now
                    if self.reload_config():
                        return False
                    users = consumers(config.VIRTUAL_DEVICE, self.ours(out, cap))
                    if users:
                        last_user = now
                        names = sorted(set(users.values()))
                    elif now - last_user > RELEASE_SECONDS:
                        break
                    elapsed = now - t0
                    self.publish(state="active", camera=camera, consumers=names,
                                 fps=round(frames / elapsed, 1) if elapsed else 0)
                    if elapsed > 5:
                        frames, t0 = 0, now

                matting = self.needs_matte()
                alpha = pending.result() if pending else (
                    self.matter.alpha(frame) if matting else None)
                # Start the next frame's matte so it runs beside this render.
                seq, raw = cap.next(seq)
                nxt, nfaces = self.prepare(raw, w, h)
                pending = self.pool.submit(self.matter.alpha, nxt) if matting else None
                out.write(self.render(frame, alpha, faces))
                frame, faces = nxt, nfaces
                frames += 1
        except RuntimeError as e:
            self.publish(state="error", error=str(e))
            time.sleep(1)
        finally:
            if pending:
                pending.result()
            cap.close()
            log("camera off")
        return True

    def needs_matte(self):
        c = self.cfg
        return c["portrait"] or c["studioLight"] or bool(c["backgroundImage"])

    def prepare(self, raw, w, h):
        """Detect faces on the full frame, frame the shot, crop to output.
        Faces are found on every other frame: the framing eases far slower
        than that, and it is the cheapest frame budget to win back."""
        self.tick = getattr(self, "tick", 0) + 1
        if self.tick % 2 or not hasattr(self, "last_faces"):
            self.last_faces = self.faces.detect(raw)
        faces = self.last_faces
        fh, fw = raw.shape[:2]
        if self.cfg["centerStage"]:
            self.framer.zoom = self.cfg["centerStageZoom"]
            x, y, cw, ch = self.framer.update(faces, fw, fh)
        else:
            self.framer.reset()
            ch = min(fh, round(fw * h / w))
            cw = round(ch * w / h)
            x, y = (fw - cw) // 2, (fh - ch) // 2
        interp = cv2.INTER_AREA if cw > 1.6 * w else cv2.INTER_LINEAR
        crop = cv2.resize(raw[y:y + ch, x:x + cw], (w, h), interpolation=interp)
        if self.cfg["mirror"]:
            crop = cv2.flip(crop, 1)
        sx, sy = w / cw, h / ch
        faces = [((fx_ - x) * sx, (fy - y) * sy, fw_ * sx, fh_ * sy)
                 for fx_, fy, fw_, fh_ in faces]
        if self.cfg["mirror"]:
            faces = [(w - x_ - fw_, y_, fw_, fh_) for x_, y_, fw_, fh_ in faces]
        return crop, faces

    def background_image(self, w, h):
        path = os.path.expanduser(self.cfg["backgroundImage"])
        key = (path, w, h)
        if key != self.background_key:
            self.background_key = key
            img = cv2.imread(path) if path else None
            self.background = fx.cover(img, w, h) if img is not None else None
            if path and img is None:
                log("cannot read background image:", path)
        return self.background

    def render(self, frame, alpha, faces):
        c = self.cfg
        if alpha is None:
            return frame
        h, w = frame.shape[:2]
        bg = self.background_image(w, h) if c["backgroundImage"] else None
        if bg is None and c["portrait"]:
            bg = fx.portrait_background(frame, alpha, c["portraitStrength"])
        studio = c["studioLightStrength"] if c["studioLight"] else 0.0
        return fx.render(frame, alpha, bg, faces, studio)


def main():
    # Leave cores for onnxruntime, which runs the matte beside OpenCV's work.
    cv2.setNumThreads(4)
    Daemon().run()


if __name__ == "__main__":
    main()
