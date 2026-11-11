import { EYE_HEIGHT, EYE_WIDTH, MARK_BLOB, MARK_BOUNDS, MARK_EYES } from "@/constants/brand-mark";
import { cn } from "@/utils/cn";

/** The Aldwin mark in the current text colour, eyes cut out to the page background. */
export function Mark({ className, title }: { className?: string; title?: string }) {
  const { x, y, width, height } = MARK_BOUNDS;
  return (
    <svg
      viewBox={`${x} ${y} ${width} ${height}`}
      fill="currentColor"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      className={cn("size-5 shrink-0", className)}
    >
      {title ? <title>{title}</title> : null}
      <path d={MARK_BLOB} />
      {MARK_EYES.map((eye) => (
        <rect
          key={eye.x}
          x={eye.x}
          y={eye.y}
          width={EYE_WIDTH}
          height={EYE_HEIGHT}
          fill="var(--bg-base)"
        />
      ))}
    </svg>
  );
}
