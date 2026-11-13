import {
  EYE_HEIGHT,
  EYE_WIDTH,
  MARK_BLOB,
  MARK_BOUNDS,
  MARK_EYES,
  MARK_EYES_CLOSED,
  MARK_EYES_HAPPY,
} from "@/constants/brand-mark";
import { cn } from "@/utils/cn";

const EYE_FILL = "var(--background)";

function OpenEye({ index }: { index: 0 | 1 }) {
  const eye = MARK_EYES[index];
  return <rect x={eye.x} y={eye.y} width={EYE_WIDTH} height={EYE_HEIGHT} fill={EYE_FILL} />;
}

function ClosedEye({ index }: { index: 0 | 1 }) {
  const eye = MARK_EYES_CLOSED[index];
  return <rect x={eye.x} y={eye.y} width={eye.width} height={eye.height} fill={EYE_FILL} />;
}

/** Eyes are cut out to the page background; `animated` cycles blink, happy and wink. */
export function Mark({ className, title, animated = false }: { className?: string; title?: string; animated?: boolean }) {
  const { x, y, width, height } = MARK_BOUNDS;
  return (
    <svg
      viewBox={`${x} ${y} ${width} ${height}`}
      fill="currentColor"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      data-brand-mark=""
      data-animated={animated || undefined}
      className={cn("size-5 shrink-0", className)}
    >
      {title ? <title>{title}</title> : null}
      <path d={MARK_BLOB} />
      {animated ? (
        <>
          {/* Each face fades on the same seven-second clock; with reduced motion only the open eyes show. */}
          <g className="animate-mark-open" data-mood="open">
            <OpenEye index={0} />
            <OpenEye index={1} />
          </g>
          <g className="animate-mark-closed opacity-0" data-mood="closed">
            <ClosedEye index={0} />
            <ClosedEye index={1} />
          </g>
          <g className="animate-mark-happy opacity-0" data-mood="happy">
            {MARK_EYES_HAPPY.map((points) => (
              <polyline
                key={points}
                points={points}
                fill="none"
                stroke={EYE_FILL}
                strokeWidth={14}
                strokeLinecap="square"
                strokeLinejoin="miter"
              />
            ))}
          </g>
          <g className="animate-mark-wink opacity-0" data-mood="wink">
            <OpenEye index={0} />
            <ClosedEye index={1} />
          </g>
        </>
      ) : (
        <>
          <OpenEye index={0} />
          <OpenEye index={1} />
        </>
      )}
    </svg>
  );
}
