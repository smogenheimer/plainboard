import { ChevronLeft, ChevronRight, ExternalLink, X, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

export interface LightboxImage {
  src: string;
  alt: string;
  /** A video: played with its own controls, not zoomed. */
  video?: boolean;
}

/** How the image lies in its frame: its size relative to "fits the window", and how far its centre is moved. */
interface View {
  scale: number;
  x: number;
  y: number;
  /** Changed by a button, a key or a double click: it moves there. A wheel, a drag and a pinch follow the hand. */
  smooth: boolean;
}

const FITS: View = { scale: 1, x: 0, y: 0, smooth: true };
const STEP = 1.5; // one press of a zoom button
const DOUBLE = 2.5; // a double click or double tap
const LOOP_MAX_S = 30; // a video up to this long loops
const TAP_MS = 300;
const TAP_PX = 24;
const MOVED_PX = 4; // a pointer that went further than this dragged, it did not click

/** Without an alt text, the file name from the address; a data: address has none. */
function shownName(src: string) {
  if (src.startsWith("data:")) return "";
  const last = src.split(/[?#]/)[0].split("/").pop() ?? "";
  try {
    return decodeURIComponent(last);
  } catch {
    return last;
  }
}

function ControlButton({
  label,
  onClick,
  autoFocus,
  disabled,
  className,
  children,
}: {
  label: string;
  onClick: () => void;
  autoFocus?: boolean;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      autoFocus={autoFocus}
      disabled={disabled}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      className={`flex h-10 min-w-10 items-center justify-center rounded-sm bg-black/50 px-2 text-white transition-colors hover:bg-black/75 focus-visible:outline-white disabled:opacity-40 disabled:hover:bg-black/50 ${className ?? ""}`}
    >
      {children}
    </button>
  );
}

/** Full-size image view with optional gallery navigation. The image can be enlarged: mouse wheel,
    double click, the buttons or + and -, two fingers on a touch screen; enlarged, it is moved by dragging. */
export function Lightbox({ images, start = 0, onClose }: { images: LightboxImage[]; start?: number; onClose: () => void }) {
  const { t } = useTranslation();
  const [index, setIndex] = useState(start);
  const current = Math.min(index, images.length - 1);
  const image = images[current];
  const many = images.length > 1;
  const step = (by: number) => setIndex((current + by + images.length) % images.length);

  const stage = useRef<HTMLDivElement>(null);
  const picture = useRef<HTMLImageElement>(null);
  const [view, setView] = useState<View>(FITS);
  const [dragging, setDragging] = useState(false);
  // The fingers or the mouse button that are down on the image's frame, and what they have done so far.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef({ moved: false, onImage: false, tapAt: 0, tapX: 0, tapY: 0 });

  useEffect(() => setView(FITS), [current]); // every image starts as a whole

  /** As far in as twice the image's own pixels, and at least four times what fits. */
  const limit = () => {
    const img = picture.current;
    const own = img && img.offsetWidth ? img.naturalWidth / img.offsetWidth : 1;
    return Math.min(16, Math.max(4, own * 2));
  };

  /** Not smaller than fitting, and not moved further than its edge reaches the frame's edge. */
  const within = (next: View): View => {
    const frame = stage.current;
    const img = picture.current;
    const scale = Math.min(limit(), Math.max(1, next.scale));
    if (!frame || !img || scale === 1) return { ...next, scale, x: 0, y: 0 };
    const roomX = Math.max(0, (img.offsetWidth * scale - frame.clientWidth) / 2);
    const roomY = Math.max(0, (img.offsetHeight * scale - frame.clientHeight) / 2);
    return { ...next, scale, x: Math.min(roomX, Math.max(-roomX, next.x)), y: Math.min(roomY, Math.max(-roomY, next.y)) };
  };

  /** Enlarge by a factor so that the point under the pointer stays where it is; without a point, the middle. */
  const zoom = (factor: number, at?: { x: number; y: number }, smooth = false) =>
    setView((v) => {
      if (image?.video) return v; // a video is not zoomed
      const frame = stage.current?.getBoundingClientRect();
      const cx = at && frame ? at.x - frame.left - frame.width / 2 : 0;
      const cy = at && frame ? at.y - frame.top - frame.height / 2 : 0;
      const scale = Math.min(limit(), Math.max(1, v.scale * factor));
      const by = scale / v.scale;
      return within({ scale, x: cx - (cx - v.x) * by, y: cy - (cy - v.y) * by, smooth });
    });

  useEffect(() => {
    // Capture + stopPropagation: Escape closes only this view, not the modal listening on window.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && many) step(-1);
      else if (e.key === "ArrowRight" && many) step(1);
      else if (e.key === "+" || e.key === "=") zoom(STEP, undefined, true);
      else if (e.key === "-") zoom(1 / STEP, undefined, true);
      else if (e.key === "0") setView(FITS);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  useEffect(() => {
    // By hand, not onWheel: React listens passively, and the page behind must not scroll or zoom.
    const frame = stage.current;
    if (!frame) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // A pinch on a trackpad arrives as a wheel with ctrl held, in small steps.
      zoom(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), { x: e.clientX, y: e.clientY });
    };
    frame.addEventListener("wheel", onWheel, { passive: false });
    return () => frame.removeEventListener("wheel", onWheel);
  });

  const spread = () => {
    const [a, b] = [...pointers.current.values()];
    return { distance: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  };

  const down = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as HTMLElement).closest("button, video") || (e.pointerType === "mouse" && e.button !== 0)) return;
    try {
      e.currentTarget.setPointerCapture(e.pointerId); // a drag goes on when the pointer leaves the frame
    } catch {
      // A pointer the browser no longer knows: it is followed as far as its events reach the frame.
    }
    if (!pointers.current.size) gesture.current = { ...gesture.current, moved: false, onImage: e.target === picture.current };
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size > 1) gesture.current.moved = true; // two fingers: never a click
  };

  const move = (e: ReactPointerEvent<HTMLDivElement>) => {
    const last = pointers.current.get(e.pointerId);
    if (!last) return;
    if (pointers.current.size === 1) {
      const [dx, dy] = [e.clientX - last.x, e.clientY - last.y];
      if (!gesture.current.moved && Math.hypot(dx, dy) < MOVED_PX) return; // still a click
      gesture.current.moved = true;
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (view.scale > 1) {
        setDragging(true);
        setView((v) => within({ ...v, x: v.x + dx, y: v.y + dy, smooth: false }));
      }
      return;
    }
    const before = spread();
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const after = spread();
    if (before.distance > 0) zoom(after.distance / before.distance, after);
    setView((v) => within({ ...v, x: v.x + after.x - before.x, y: v.y + after.y - before.y, smooth: false }));
  };

  const up = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!pointers.current.delete(e.pointerId) || pointers.current.size) return;
    setDragging(false);
    const { moved, onImage, tapAt, tapX, tapY } = gesture.current;
    if (moved || e.type === "pointercancel") return;
    if (!onImage) return onClose(); // next to the image: the view closes, as it always did
    const again = e.timeStamp - tapAt < TAP_MS && Math.hypot(e.clientX - tapX, e.clientY - tapY) < TAP_PX;
    gesture.current = { ...gesture.current, tapAt: again ? 0 : e.timeStamp, tapX: e.clientX, tapY: e.clientY };
    if (!again) return;
    if (view.scale > 1) setView(FITS);
    else zoom(DOUBLE, { x: e.clientX, y: e.clientY }, true);
  };

  if (!image) return null;
  const enlarged = view.scale > 1;
  const video = image.video === true;
  const name = image.alt || shownName(image.src);
  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={name}
      // React events bubble through portals: keep them away from the surrounding modal.
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
      className="fixed inset-0 z-[70] flex flex-col bg-black/90 pb-[env(safe-area-inset-bottom)]"
    >
      <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 p-3 text-white">
        {/* On a phone the name has its own line under the buttons: next to them only a few letters would fit. */}
        <span className="flex min-w-0 flex-1 items-baseline gap-2 max-sm:order-last max-sm:basis-full">
          <span title={name} className="min-w-0 truncate">
            {name}
          </span>
          {many && <span className="shrink-0 text-white/60 tabular-nums">{t("lightbox.position", { current: current + 1, total: images.length })}</span>}
        </span>
        {!video && (
          <>
            <ControlButton label={t("lightbox.zoomOut")} onClick={() => zoom(1 / STEP, undefined, true)} disabled={!enlarged}>
              <ZoomOut className="size-4" />
            </ControlButton>
            <ControlButton label={t("lightbox.zoomReset")} onClick={() => setView(FITS)} disabled={!enlarged} className="w-14 tabular-nums max-sm:hidden">
              {Math.round(view.scale * 100)}%
            </ControlButton>
            <ControlButton label={t("lightbox.zoomIn")} onClick={() => zoom(STEP, undefined, true)} disabled={view.scale >= limit()}>
              <ZoomIn className="size-4" />
            </ControlButton>
          </>
        )}
        <a
          href={image.src}
          target="_blank"
          rel="noreferrer"
          aria-label={t("lightbox.openOriginal")}
          title={t("lightbox.openOriginal")}
          onClick={(e) => e.stopPropagation()}
          className="flex size-10 items-center justify-center rounded-sm bg-black/50 text-white transition-colors hover:bg-black/75 focus-visible:outline-white"
        >
          <ExternalLink className="size-4" />
        </a>
        <ControlButton label={t("common.close")} onClick={onClose} autoFocus>
          <X className="size-5" />
        </ControlButton>
      </div>
      <div
        ref={stage}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
        // A click here was decided when the pointer went up: a drag closes nothing.
        onClick={(e) => e.stopPropagation()}
        className="relative mx-3 mb-3 flex min-h-0 flex-1 touch-none items-center justify-center overflow-hidden select-none"
      >
        {video ? (
          <video
            key={image.src}
            src={image.src}
            aria-label={image.alt}
            controls
            autoPlay
            muted
            playsInline
            // A short clip loops like a GIF; a long one plays once.
            onLoadedMetadata={(e) => (e.currentTarget.loop = e.currentTarget.duration <= LOOP_MAX_S)}
            className="max-h-full max-w-full rounded-sm bg-black object-contain"
          />
        ) : (
          <img
            ref={picture}
            src={image.src}
            alt={image.alt}
            draggable={false}
            onLoad={() => setView((v) => within(v))}
            style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}
            className={`max-h-full max-w-full rounded-sm object-contain ${view.smooth && !dragging ? "transition-transform duration-150 motion-reduce:transition-none" : ""} ${
              dragging ? "cursor-grabbing" : enlarged ? "cursor-grab" : "cursor-zoom-in"
            }`}
          />
        )}
        {many && (
          <>
            <ControlButton label={t("lightbox.previous")} onClick={() => step(-1)} className="absolute left-0 top-1/2 -translate-y-1/2">
              <ChevronLeft className="size-5" />
            </ControlButton>
            <ControlButton label={t("lightbox.next")} onClick={() => step(1)} className="absolute right-0 top-1/2 -translate-y-1/2">
              <ChevronRight className="size-5" />
            </ControlButton>
          </>
        )}
      </div>
    </div>,
    document.body,
  );
}
