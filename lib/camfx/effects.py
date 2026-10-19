"""The image side: face tracking, matting and the four Mac-style effects."""

import time

import cv2
import numpy as np
import onnxruntime as ort


class FaceDetector:
    """YuNet on a small copy of the full sensor frame."""

    WIDTH = 320

    def __init__(self, model):
        self.model = model
        self.net = None
        self.size = None

    def detect(self, frame):
        h, w = frame.shape[:2]
        sw, sh = self.WIDTH, round(self.WIDTH * h / w)
        if self.size != (sw, sh):
            self.net = cv2.FaceDetectorYN.create(self.model, "", (sw, sh), 0.7)
            self.size = (sw, sh)
        _, faces = self.net.detect(cv2.resize(frame[::2, ::2], (sw, sh), interpolation=cv2.INTER_LINEAR))
        if faces is None:
            return []
        scale = w / sw
        return [tuple(f[:4] * scale) for f in faces]


class Framer:
    """Center Stage: a virtual camera operator that pans and zooms a crop of
    the full frame to keep the people in shot, with a dead zone so it holds
    still while you talk and an ease so moves read as camera moves."""

    HOLD_SECONDS = 2.0   # keep the last framing this long after faces vanish
    EASE = 0.06          # fraction of the remaining distance moved per frame

    def __init__(self, aspect):
        self.aspect = aspect
        self.zoom = 0.6      # 0 = loose framing, 1 = tight on the face
        self.crop = None     # current (cx, cy, h), floats
        self.target = None
        self.last_seen = 0.0

    def update(self, faces, fw, fh):
        full = (fw / 2, fh / 2, min(fh, fw / self.aspect))
        if self.crop is None:
            self.crop = self.target = full

        now = time.monotonic()
        if faces:
            self.last_seen = now
            x0 = min(f[0] for f in faces)
            y0 = min(f[1] for f in faces)
            x1 = max(f[0] + f[2] for f in faces)
            y1 = max(f[1] + f[3] for f in faces)
            fw_, fh_ = x1 - x0, y1 - y0
            # From head and shoulders (face a third of the height) at zoom 0
            # to a close-up (face half the height) at 1; a group still gets
            # room on both sides.
            max_zoom = 1.6 + 1.4 * self.zoom
            h = max(fh_ * (3.2 - 1.2 * self.zoom), fw_ * 1.9 / self.aspect, full[2] / max_zoom)
            h = min(h, full[2])
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2 + h * 0.12   # eyes on the upper third
            want = self._clamp(cx, cy, h, fw, fh)
            tx, ty, th = self.target
            # Dead zone: only retarget when the subject has really moved.
            if (abs(want[0] - tx) > th * 0.08 or abs(want[1] - ty) > th * 0.08
                    or abs(want[2] - th) > th * 0.12):
                self.target = want
        elif now - self.last_seen > self.HOLD_SECONDS:
            self.target = full

        c, t = self.crop, self.target
        self.crop = tuple(a + (b - a) * self.EASE for a, b in zip(c, t))
        return self.rect(fw, fh)

    def reset(self):
        self.crop = self.target = None

    def rect(self, fw, fh):
        cx, cy, h = self._clamp(*self.crop, fw, fh)
        w = h * self.aspect
        x, y = int(round(cx - w / 2)), int(round(cy - h / 2))
        return x, y, int(round(w)), int(round(h))

    def _clamp(self, cx, cy, h, fw, fh):
        w = h * self.aspect
        cx = min(max(cx, w / 2), fw - w / 2)
        cy = min(max(cy, h / 2), fh - h / 2)
        return cx, cy, h


class Matter:
    """Robust Video Matting: a recurrent model, so its state carries edges
    from frame to frame and hair stays stable instead of flickering."""

    def __init__(self, model, width=512, threads=6):
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(model, so, providers=["CPUExecutionProvider"])
        self.width = width
        self.reset()

    def reset(self):
        self.rec = [np.zeros((1, 1, 1, 1), np.float32)] * 4

    def alpha(self, frame):
        h, w = frame.shape[:2]
        sw = self.width
        sh = round(sw * h / w / 4) * 4
        small = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
        src = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32)
        src = (src * (1 / 255)).transpose(2, 0, 1)[None]
        _, pha, *self.rec = self.session.run(None, {
            "src": src,
            "r1i": self.rec[0], "r2i": self.rec[1], "r3i": self.rec[2], "r4i": self.rec[3],
            "downsample_ratio": np.array([0.5], np.float32),
        })
        return cv2.resize(pha[0, 0], (w, h), interpolation=cv2.INTER_LINEAR)


def portrait_background(frame, alpha, strength):
    """Shallow depth of field, blurred at quarter size: cheaper, and a wider,
    softer blur than a full-size kernel of the same cost. The person is cut
    out before blurring and the hole filled from around it, so they do not
    smear into a bright halo behind themselves."""
    h, w = frame.shape[:2]
    size = (w // 4, h // 4)
    small = cv2.resize(frame, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    k = 1 + 2 * int(2 + strength * 14)
    if alpha is None:
        small = cv2.GaussianBlur(small, (k, k), 0)
    else:
        room = 1 - cv2.resize(alpha, size, interpolation=cv2.INTER_AREA)
        room = np.clip(room * 1.5 - 0.5, 0, 1)   # pull the cut inside the edge
        num = cv2.GaussianBlur(small * room[..., None], (k, k), 0)
        den = cv2.GaussianBlur(room, (k, k), 0)
        small = num / np.maximum(den, 1e-3)[..., None]
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR) * (1 / 255)


def cover(image, w, h):
    """Resize and centre-crop a background image to fill the frame."""
    ih, iw = image.shape[:2]
    s = max(w / iw, h / ih)
    image = cv2.resize(image, (round(iw * s), round(ih * s)), interpolation=cv2.INTER_AREA)
    y = (image.shape[0] - h) // 2
    x = (image.shape[1] - w) // 2
    return image[y:y + h, x:x + w].astype(np.float32) * (1 / 255)


def face_mask(shape, faces):
    """Soft ellipses over the faces, sized a little past the face box."""
    h, w = shape[:2]
    s = 8
    m = np.zeros((h // s, w // s), np.float32)
    for x, y, fw, fh in faces:
        center = (int((x + fw / 2) / s), int((y + fh / 2) / s))
        axes = (max(1, int(fw * 0.75 / s)), max(1, int(fh * 0.95 / s)))
        cv2.ellipse(m, center, axes, 0, 0, 360, 1.0, -1)
    k = 1 + 2 * (max(w, h) // s // 12)
    m = cv2.GaussianBlur(m, (k, k), 0)
    return cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)


def render(frame, alpha=None, background=None, faces=(), studio=0.0):
    """One float pass over the frame for every effect that is on.

    background: float image in 0..1 to put behind the person (needs alpha).
    studio: Studio Light strength; lights the faces like a key light and,
    with alpha, dims the room behind."""
    img = frame.astype(np.float32) * (1 / 255)
    a = None if alpha is None else alpha[..., None]
    if a is not None and background is not None:
        img = background + (img - background) * a
    if studio > 0:
        if faces:
            lift = face_mask(frame.shape, faces)[..., None] * (1.4 * studio)
            # A shoulder curve: lifts shadows and midtones, never clips.
            img = img + lift * img * (1 - img)
        if a is not None:
            img = img * (1 - 0.45 * studio * (1 - a))
    return cv2.convertScaleAbs(img, alpha=255)
