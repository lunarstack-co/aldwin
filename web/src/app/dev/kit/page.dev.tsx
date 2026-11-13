"use client";

import { Search } from "lucide-react";
import type { ReactNode } from "react";

import { BrandMark } from "@/components/brand/brand-mark";
import { Mark } from "@/components/brand/mark";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { CodeBlock } from "@/components/ui/code-block";
import { Command, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DotMatrix } from "@/components/ui/dot-matrix";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Step, Steps } from "@/components/ui/steps";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { SkillText } from "@/components/organisms/chat/user-message";
import { ApprovalCard } from "@/components/assistant-ui/elements/approval-card";
import { CodeDiff } from "@/components/assistant-ui/elements/code-diff";
import { ContextBreakdown } from "@/components/assistant-ui/elements/context-breakdown";
import { ContextDisplay } from "@/components/assistant-ui/elements/context-display";
import { DataTable } from "@/components/assistant-ui/elements/data-table";
import { DaySeparator } from "@/components/assistant-ui/elements/day-separator";
import { FileTree } from "@/components/assistant-ui/elements/file-tree";
import { GuardrailNotice } from "@/components/assistant-ui/elements/guardrail-notice";
import { LinkPreview } from "@/components/assistant-ui/elements/link-preview";
import { MemoryChips } from "@/components/assistant-ui/elements/memory-chips";
import { MermaidDiagram } from "@/components/assistant-ui/elements/mermaid-diagram";
import { MessageTiming } from "@/components/assistant-ui/elements/message-timing";
import { PermissionGrant } from "@/components/assistant-ui/elements/permission-grant";
import { SyntaxHighlighter } from "@/components/assistant-ui/elements/shiki-highlighter";
import { StoppedRun } from "@/components/assistant-ui/elements/stopped-run";
import { TerminalBlock } from "@/components/assistant-ui/elements/terminal-block";
import { TodoList } from "@/components/assistant-ui/elements/todo-list";
import { ToolError } from "@/components/assistant-ui/elements/tool-error";
import { WebSearch } from "@/components/assistant-ui/elements/web-search";
import markdownCopy from "@/data/chat/markdown.json";
import copy from "@/data/dev/kit.json";
import transcriptCopy from "@/data/chat/transcript.json";
import approvalCopy from "@/data/chat/approval.json";

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
      <Item name="input-group">
        <InputGroup className="max-w-72">
          <InputGroupInput placeholder={s.search} aria-label={s.search} />
          <InputGroupAddon><Search aria-hidden /></InputGroupAddon>
        </InputGroup>
      </Item>
      <Item name="command">
        <Command className="max-w-72 border">
          <CommandInput placeholder={s.search} aria-label={s.search} />
          <CommandList>
            <CommandGroup heading={s.group}>
              <CommandItem>{s.model}</CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </Item>
      <Item name="directive-text"><SkillText text={s.skillText} /></Item>
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
      <Item name="code-block"><CodeBlock title="ts" copyText={s.code} copyLabel={markdownCopy.copyCode}><pre><code>{s.code}</code></pre></CodeBlock></Item>
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
      <Item name="approval-card">
        <ApprovalCard state="request" title={approvalCopy.titles.run_command} subtitle="ls -la" onAllowOnce={() => {}} onDeny={() => {}} />
      </Item>
      <Item name="permission-grant">
        <PermissionGrant capability="Read files in ~/Documents" requester="Aldwin" reach={["~/Documents"]} scope="pending" onGrant={() => {}} />
      </Item>
      <Item name="terminal-block">
        <TerminalBlock command="echo hi" lines={["hi"]} visibleCount={1} done exitCode={0} durationMs={12} />
      </Item>
      <Item name="code-diff">
        <CodeDiff filename="notes.md" additions={1} deletions={1} lines={[{ kind: "removed", text: "old" }, { kind: "added", text: "new" }]} cycle={1} />
      </Item>
      <Item name="file-tree">
        <FileTree nodes={[{ path: "src", name: "src", depth: 0, kind: "folder" }, { path: "src/app.ts", name: "app.ts", depth: 1, kind: "file" }]} visibleCount={2} totalAdditions={0} totalDeletions={0} />
      </Item>
      <Item name="web-search">
        <WebSearch query="aldwin" results={[{ title: "Aldwin", domain: "askaldwin.com", url: "https://askaldwin.com" }]} visibleResults={1} searching={false} cycle={1} />
      </Item>
      <Item name="link-preview">
        <LinkPreview href="https://askaldwin.com" title="Aldwin" siteName="askaldwin.com" description="Local assistant." />
      </Item>
      <Item name="data-table">
        <DataTable columns={[{ key: "name", label: "Name", sortable: true }]} rows={[{ name: "Ada" }]} />
      </Item>
      <Item name="memory-chips">
        <MemoryChips chips={[{ id: "m1", text: "Likes tea", change: "added", label: "Added" }]} />
      </Item>
      <Item name="guardrail-notice">
        <GuardrailNotice title="Blocked" policy="blocked_command" explanation="Command refused." alternatives={[]} />
      </Item>
      <Item name="brand-marks">
        <span className="flex items-center gap-3">
          <BrandMark name="linear" kind="connector" className="size-5" />
          <BrandMark name="stripe" kind="connector" className="size-5" />
          <BrandMark name="monday" kind="connector" className="size-5" />
          <BrandMark name="canva" kind="connector" className="size-5" />
          <BrandMark name="acme" kind="connector" className="size-5" />
        </span>
      </Item>
      <Item name="tool-error">
        <ToolError name="read_file" target="missing.txt" message="not found" />
      </Item>
      <Item name="todo-list">
        <TodoList items={[{ id: "t1", text: "Write tests", status: "active" }]} />
      </Item>
      <Item name="stopped-run">
        <StoppedRun words={["Partial", "answer"]} reason="stopped" onContinue={() => {}} onDiscard={() => {}} />
      </Item>
      <Item name="day-separator">
        <DaySeparator messages={[{ id: "d1", day: "Today", time: "10:00", role: "user", text: "Hi" }]} />
      </Item>
      <Item name="context-breakdown">
        <ContextBreakdown limit={128000} segments={[{ label: "Instructions", tokens: 1000, tint: "bg-blue-500" }]} />
      </Item>
      <Item name="context-display">
        <ContextDisplay.Ring modelContextWindow={128000} usage={{ inputTokens: 4000, outputTokens: 200 }} />
      </Item>
      <Item name="message-timing">
        <MessageTiming stats={[{ label: transcriptCopy.ttft, value: "0.8 s" }, { label: "speed", value: "50 tok/s" }]} />
      </Item>
      <Item name="shiki-highlighter">
        <SyntaxHighlighter language="python" code="x = 1" />
      </Item>
      <Item name="mermaid-diagram">
        <MermaidDiagram code="graph TD; A-->B" />
      </Item>
    </div>
  );
}

export default function KitPage() {
  return (
    <div className="grid flex-1 md:grid-cols-2">
      <section aria-label={copy.light} className="light"><Gallery /></section>
      <section aria-label={copy.dark} className="dark"><Gallery /></section>
    </div>
  );
}
