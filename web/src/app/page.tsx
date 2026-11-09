import { BookOpen } from "lucide-react";

import { buttonClass } from "@/components/ui/button";

export default function Home() {
  return (
    <section
      data-testid="aldwin-stub"
      className="mx-auto flex w-full max-w-[680px] flex-1 flex-col items-start justify-center gap-4 px-4 py-16 sm:px-6"
    >
      <span className="chip">Local</span>
      <h1 className="text-h1 font-semibold">Aldwin</h1>
      <p className="text-md text-fg-secondary">
        Your personal assistant, running on this computer.
      </p>
      <a href="https://askaldwin.com" className={buttonClass({ variant: "primary" })}>
        <BookOpen aria-hidden className="size-4" />
        Read the docs
      </a>
    </section>
  );
}
