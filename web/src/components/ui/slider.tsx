"use client";

import { Slider as SliderPrimitive } from "@base-ui/react/slider";

import { cn } from "@/utils/cn";

type ThumbProps = Pick<SliderPrimitive.Thumb.Props, "aria-labelledby" | "aria-describedby" | "getAriaValueText">;

/** A one-thumb slider; the whole control takes pointer input. */
function Slider({
  value,
  min,
  max,
  step,
  onValueChange,
  thumb,
  className,
  ...props
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  onValueChange: (value: number) => void;
  thumb: ThumbProps;
  className?: string;
  "data-testid"?: string;
}) {
  return (
    <SliderPrimitive.Root
      data-slot="slider"
      value={value}
      min={min}
      max={max}
      step={step}
      onValueChange={(next) => onValueChange(next)}
      className={cn("w-full", className)}
      {...props}
    >
      <SliderPrimitive.Control
        data-slot="slider-control"
        className="flex h-9 w-full cursor-pointer touch-none items-center select-none max-md:h-11"
      >
        <SliderPrimitive.Track className="bg-foreground/15 relative h-1.5 w-full rounded-full">
          <SliderPrimitive.Indicator className="bg-primary rounded-full" />
          <SliderPrimitive.Thumb
            {...thumb}
            className="border-primary bg-background has-[:focus-visible]:ring-ring/50 block size-4 rounded-full border-2 shadow-sm outline-none has-[:focus-visible]:ring-3 max-md:size-6"
          />
        </SliderPrimitive.Track>
      </SliderPrimitive.Control>
    </SliderPrimitive.Root>
  );
}

export { Slider };
