"use client";

import type { ReactNode } from "react";

import { Mark } from "@/components/brand/mark";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CodeBlock } from "@/components/ui/code-block";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DotMatrix } from "@/components/ui/dot-matrix";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Step, Steps } from "@/components/ui/steps";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import copy from "@/data/dev/kit.json";

function Item({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div data-kit-item={name} className="flex flex-col gap-2">
      <p className="text-muted-foreground font-mono text-xs">{name}</p>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

function Gallery() {
  const s = copy.sample;
  return (
    <div className="bg-background text-foreground flex flex-col gap-6 p-6">
      <Item name="mark"><Mark className="size-8" /></Item>
      <Item name="button">
        <Button>{s.button}</Button>
        <Button variant="outline">{s.button}</Button>
        <Button variant="secondary">{s.button}</Button>
        <Button variant="ghost">{s.button}</Button>
        <Button variant="destructive">{s.button}</Button>
      </Item>
      <Item name="input"><Input placeholder={s.input} /></Item>
      <Item name="field"><Field id="kit-field" label={s.input}><Input id="kit-field" /></Field></Item>
      <Item name="textarea"><Textarea placeholder={s.input} /></Item>
      <Item name="label"><Label>{s.input}</Label></Item>
      <Item name="badge"><Badge>{s.badge}</Badge><Badge variant="outline">{s.badge}</Badge></Item>
      <Item name="callout">
        <Callout type="warning">{s.callout}</Callout>
        <Callout type="error">{s.callout}</Callout>
      </Item>
      <Item name="kbd"><Kbd>{s.kbd}</Kbd></Item>
      <Item name="switch"><Switch defaultChecked /></Item>
      <Item name="radio-group">
        <RadioGroup defaultValue="a">
          <RadioGroupItem value="a" aria-label={s.option} />
          <RadioGroupItem value="b" aria-label={s.option} />
        </RadioGroup>
      </Item>
      <Item name="avatar"><Avatar><AvatarFallback>AL</AvatarFallback></Avatar></Item>
      <Item name="skeleton"><Skeleton className="h-6 w-40" /></Item>
      <Item name="separator"><Separator /></Item>
      <Item name="dot-matrix"><DotMatrix state="loading" /><DotMatrix state="success" /></Item>
      <Item name="steps"><Steps><Step>{s.step}</Step><Step>{s.step}</Step></Steps></Item>
      <Item name="code-block"><CodeBlock title="ts" copyText={s.code}><pre><code>{s.code}</code></pre></CodeBlock></Item>
      <Item name="collapsible">
        <Collapsible defaultOpen>
          <CollapsibleTrigger>{s.option}</CollapsibleTrigger>
          <CollapsibleContent>{s.dialog}</CollapsibleContent>
        </Collapsible>
      </Item>
      <Item name="tabs">
        <Tabs defaultValue="one">
          <TabsList><TabsTrigger value="one">{s.tab}</TabsTrigger><TabsTrigger value="two">{s.tab}</TabsTrigger></TabsList>
          <TabsContent value="one">{s.dialog}</TabsContent>
        </Tabs>
      </Item>
    </div>
  );
}

/** Every kit component in light and dark, side by side, for the eye and for screenshots. */
export default function KitPage() {
  return (
    <div className="grid flex-1 md:grid-cols-2">
      <section aria-label={copy.light} className="light"><Gallery /></section>
      <section aria-label={copy.dark} className="dark"><Gallery /></section>
    </div>
  );
}
