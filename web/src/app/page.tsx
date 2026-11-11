"use client";

import { Chip } from "@/components/atoms/chip";
import copy from "@/data/home.json";
import { useSetup } from "@/hooks/use-setup";
import { CenteredLayout } from "@/layouts/centered-layout";

export default function Home() {
  const { status } = useSetup();
  return (
    <CenteredLayout width="wide" className="items-start gap-4" data-testid="aldwin-gates-clear">
      <Chip>{status.model}</Chip>
      <h1 className="text-h1 font-semibold">{copy.title}</h1>
      <p className="text-md text-fg-secondary" role="status">
        {status.host_ready ? copy.running : copy.starting}
      </p>
    </CenteredLayout>
  );
}
