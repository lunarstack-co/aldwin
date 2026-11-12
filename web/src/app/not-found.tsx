import type { Metadata } from "next";
import Link from "next/link";

import { Mark } from "@/components/brand/mark";
import { buttonVariants } from "@/components/ui/button";
import copy from "@/data/app.json";

export const metadata: Metadata = {
  title: copy.pages.notFound.title,
  description: copy.pages.notFound.description,
};

export default function NotFound() {
  const page = copy.pages.notFound;
  return (
    <div data-testid="not-found" className="flex flex-1 flex-col items-center justify-center gap-4 px-4 text-center">
      <Mark className="size-10" />
      <h1 className="text-2xl">{page.title}</h1>
      <p className="text-muted-foreground max-w-[420px] text-base">{page.body}</p>
      <Link href="/" className={buttonVariants({ className: "max-md:h-10" })}>
        {page.action}
      </Link>
    </div>
  );
}
