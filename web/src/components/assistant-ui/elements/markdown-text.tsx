"use client";

import "@assistant-ui/react-markdown/styles/dot.css";

import {
  type CodeHeaderProps,
  escapeCurrencyDollars,
  MarkdownTextPrimitive,
  type MarkdownTextPrimitiveProps,
  memoizeMarkdownComponents,
  normalizeMathDelimiters,
  type SyntaxHighlighterProps as FenceHighlighterProps,
  useIsMarkdownCodeBlock,
} from "@assistant-ui/react-markdown";
import { INTERNAL, useAuiState } from "@assistant-ui/react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { type FC, memo, useEffect, useState } from "react";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { TooltipIconButton } from "@/components/assistant-ui/elements/tooltip-icon-button";
import copy from "@/data/chat/markdown.json";
import { useCopyToClipboard } from "@/hooks/use-copy-to-clipboard";
import { cn } from "@/utils/cn";
import { LINK_REL, sanitizeSchema } from "@/utils/markdown";

type ShikiModule = typeof import("./shiki-highlighter");
type MermaidModule = typeof import("./mermaid-diagram");

/** Loads a renderer once per page and re-renders its callers when it lands. */
function lazyModule<T>(load: () => Promise<T>): () => T | null {
  let loaded: T | null = null;
  let pending: Promise<T> | null = null;
  return function useLoaded() {
    const [mod, setMod] = useState<T | null>(loaded);
    useEffect(() => {
      if (mod) return;
      let live = true;
      pending ??= load().then((m) => (loaded = m));
      void pending.then((m) => {
        if (live) setMod(m);
      });
      return () => {
        live = false;
      };
    }, [mod]);
    return mod;
  };
}

const useShiki = lazyModule<ShikiModule>(() => import("./shiki-highlighter"));
const useMermaid = lazyModule<MermaidModule>(() => import("./mermaid-diagram"));

type LoadedMathPlugins = {
  remarkPlugins: NonNullable<MarkdownTextPrimitiveProps["remarkPlugins"]>;
  rehypePlugins: NonNullable<MarkdownTextPrimitiveProps["rehypePlugins"]>;
};

let cachedMathPlugins: LoadedMathPlugins | null = null;
let mathLoadPromise: Promise<LoadedMathPlugins> | null = null;

function ensureMathPlugins(): Promise<LoadedMathPlugins> {
  if (cachedMathPlugins) return Promise.resolve(cachedMathPlugins);
  if (!mathLoadPromise) {
    mathLoadPromise = Promise.all([
      import("remark-math"),
      import("rehype-katex"),
      import("katex/dist/katex.min.css"),
    ]).then(([remarkMathMod, rehypeKatexMod]) => {
      cachedMathPlugins = {
        remarkPlugins: [remarkGfm, remarkMathMod.default],
        rehypePlugins: [
          [rehypeSanitize, sanitizeSchema],
          rehypeKatexMod.default,
        ],
      };
      return cachedMathPlugins;
    });
  }
  return mathLoadPromise;
}

const baseRemarkPlugins: NonNullable<MarkdownTextPrimitiveProps["remarkPlugins"]> = [remarkGfm];
const baseRehypePlugins: NonNullable<MarkdownTextPrimitiveProps["rehypePlugins"]> = [
  [rehypeSanitize, sanitizeSchema],
];

function expandSingleLineDisplayMath(text: string): string {
  // Split around fenced code blocks so code fences are never rewritten.
  const segments = text.split(/(^```[\s\S]*?^```|^~~~[\s\S]*?^~~~)/gm);
  return segments
    .map((seg, idx) =>
      idx % 2 === 1
        ? seg
        : seg.replace(
            /\$\$([^\n$]+?)\$\$/g,
            (_match, inner: string) => `\n$$\n${inner.trim()}\n$$\n`,
          ),
    )
    .join("");
}

const preprocessMath = (text: string) =>
  expandSingleLineDisplayMath(escapeCurrencyDollars(normalizeMathDelimiters(text)));

type FenceNode = FenceHighlighterProps["node"];

/**
 * Whether a code block is still being revealed. The text animates in behind the message, so this follows the
 * revealed text's status, and a block counts as closed only once its own closing fence line is on screen.
 */
function useIsFenceStreaming(node: FenceNode, code: string): boolean {
  const revealing = INTERNAL.useSmoothStatus().type === "running";
  if (!revealing) return false;
  const start = node?.position?.start?.line;
  const end = node?.position?.end;
  if (start === undefined || end === undefined) return true;
  const codeLines = code === "" ? 0 : code.replace(/\n$/, "").split("\n").length;
  // An open block whose text ends in a newline also reaches the next line, but only at its first column.
  const closed = end.line - start >= codeLines + 1 && end.column > 1;
  return !closed;
}

// Throttles highlighting while a block streams, so colour arrives with the text.
const STREAMING_HIGHLIGHT_DELAY_MS = 120;

const CodeFenceHighlighter: FC<FenceHighlighterProps> = ({
  node,
  components: { Pre, Code },
  language,
  code,
}) => {
  const fenceStreaming = useIsFenceStreaming(node, code);
  const shiki = useShiki();
  if (!language || !shiki) {
    return (
      <Pre>
        <Code>{code}</Code>
      </Pre>
    );
  }
  return (
    <shiki.SyntaxHighlighter
      language={language}
      code={code}
      delay={fenceStreaming ? STREAMING_HIGHLIGHT_DELAY_MS : 0}
    />
  );
};

const MermaidFenceHighlighter: FC<FenceHighlighterProps> = ({ node, code }) => {
  const fenceStreaming = useIsFenceStreaming(node, code);
  const mermaid = useMermaid();
  // A half-written diagram would flash its source or a parse error, so it waits behind one placeholder.
  if (!mermaid) return <DiagramPlaceholder />;
  return <mermaid.MermaidDiagram code={code} streaming={fenceStreaming} />;
};

function DiagramPlaceholder() {
  return (
    <div
      data-slot="mermaid-skeleton"
      aria-label={copy.mermaidDrawing}
      className="bg-muted flex h-32 animate-pulse items-center justify-center rounded-b-lg motion-reduce:animate-none"
    />
  );
}

const componentsByLanguage: MarkdownTextPrimitiveProps["componentsByLanguage"] = {
  mermaid: {
    SyntaxHighlighter: MermaidFenceHighlighter,
  },
};

const MarkdownTextImpl: FC = () => {
  const streaming = useAuiState((s) => s.part.status.type === "running");
  const rawText = useAuiState((s) => (s.part.type === "text" ? s.part.text : ""));
  const hasMathSyntax =
    rawText.includes("$") || rawText.includes("\\(") || rawText.includes("\\[");
  const [mathPlugins, setMathPlugins] = useState<LoadedMathPlugins | null>(cachedMathPlugins);

  useEffect(() => {
    if (!hasMathSyntax || mathPlugins) return;
    let active = true;
    void ensureMathPlugins().then((loaded) => {
      if (active) setMathPlugins(loaded);
    });
    return () => {
      active = false;
    };
  }, [hasMathSyntax, mathPlugins]);

  const activePlugins = hasMathSyntax && mathPlugins ? mathPlugins : null;

  return (
    <div data-testid="assistant-message" aria-busy={streaming || undefined}>
      <MarkdownTextPrimitive
        skipHtml
        preprocess={activePlugins ? preprocessMath : undefined}
        remarkPlugins={activePlugins ? activePlugins.remarkPlugins : baseRemarkPlugins}
        rehypePlugins={activePlugins ? activePlugins.rehypePlugins : baseRehypePlugins}
        className="aui-md"
        components={defaultComponents}
        componentsByLanguage={componentsByLanguage}
        defer
      />
    </div>
  );
};

export const MarkdownText = memo(MarkdownTextImpl);

const CodeHeader: FC<CodeHeaderProps> = ({ language, code }) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const onCopy = () => {
    if (!code || isCopied) return;
    copyToClipboard(code);
  };

  return (
    <div className="aui-code-header-root border-border/50 bg-muted/50 mt-3 flex items-center justify-between rounded-t-xl border border-b-0 px-3.5 py-1.5 text-xs">
      <span className="aui-code-header-language text-muted-foreground font-medium lowercase">
        {language}
      </span>
      <TooltipIconButton tooltip={isCopied ? copy.copied : copy.copyCode} onClick={onCopy}>
        {!isCopied && (
          <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
        )}
        {isCopied && (
          <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
        )}
      </TooltipIconButton>
    </div>
  );
};

const defaultComponents = memoizeMarkdownComponents({
  h1: ({ className, ...props }) => (
    <h1
      className={cn(
        "aui-md-h1 mt-5 mb-2 scroll-m-20 text-xl font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }) => (
    <h2
      className={cn(
        "aui-md-h2 mt-5 mb-2 scroll-m-20 text-lg font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }) => (
    <h3
      className={cn(
        "aui-md-h3 mt-4 mb-1.5 scroll-m-20 text-base font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h4: ({ className, ...props }) => (
    <h4
      className={cn(
        "aui-md-h4 mt-3.5 mb-1 scroll-m-20 text-base font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h5: ({ className, ...props }) => (
    <h5
      className={cn(
        "aui-md-h5 mt-3 mb-1 text-sm font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h6: ({ className, ...props }) => (
    <h6
      className={cn(
        "aui-md-h6 mt-3 sm:mt-3 mb-1 text-sm font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  p: ({ className, ...props }) => (
    <p
      className={cn(
        "aui-md-p my-3 leading-relaxed first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  a: ({ className, ...props }) => (
    <a
      className={cn(
        "aui-md-a text-primary hover:text-primary/80 underline underline-offset-2",
        className,
      )}
      {...props}
      target="_blank"
      rel={LINK_REL}
    />
  ),
  img: ({ src, alt }) =>
    typeof src === "string" ? (
      <a
        href={src}
        target="_blank"
        rel={LINK_REL}
        className="aui-md-a text-primary hover:text-primary/80 underline underline-offset-2"
      >
        {alt || src}
      </a>
    ) : null,
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "aui-md-blockquote border-muted-foreground/30 text-muted-foreground my-3 border-s-2 ps-4",
        className,
      )}
      {...props}
    />
  ),
  ul: ({ className, ...props }) => (
    <ul
      className={cn(
        "aui-md-ul marker:text-muted-foreground my-3 ms-5 list-disc [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  ol: ({ className, ...props }) => (
    <ol
      className={cn(
        "aui-md-ol marker:text-muted-foreground my-3 ms-5 list-decimal [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  hr: ({ className, ...props }) => (
    <hr
      className={cn("aui-md-hr border-muted-foreground/20 my-3", className)}
      {...props}
    />
  ),
  table: ({ className, ...props }) => (
    <div className="aui-md-table-wrapper my-3 overflow-x-auto">
      <table
        className={cn(
          "aui-md-table w-full border-separate border-spacing-0",
          className,
        )}
        {...props}
      />
    </div>
  ),
  th: ({ className, ...props }) => (
    <th
      className={cn(
        "aui-md-th bg-muted px-3 py-1.5 text-start font-medium first:rounded-ss-lg last:rounded-se-lg [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }) => (
    <td
      className={cn(
        "aui-md-td border-muted-foreground/20 border-s border-b px-3 py-1.5 text-start last:border-e [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  tr: ({ className, ...props }) => (
    <tr
      className={cn(
        "aui-md-tr m-0 border-b p-0 first:border-t [&:last-child>td:first-child]:rounded-es-lg [&:last-child>td:last-child]:rounded-ee-lg",
        className,
      )}
      {...props}
    />
  ),
  li: ({ className, ...props }) => (
    <li className={cn("aui-md-li leading-relaxed", className)} {...props} />
  ),
  strong: ({ className, ...props }) => (
    <strong
      className={cn("aui-md-strong font-semibold", className)}
      {...props}
    />
  ),
  sup: ({ className, ...props }) => (
    <sup
      className={cn("aui-md-sup [&>a]:text-xs [&>a]:no-underline", className)}
      {...props}
    />
  ),
  pre: ({ className, ...props }) => (
    <pre
      className={cn(
        "aui-md-pre border-border/50 bg-muted/30 overflow-x-auto rounded-t-none rounded-b-xl border border-t-0 p-3.5 text-[13px] leading-relaxed",
        className,
      )}
      {...props}
    />
  ),
  code: function Code({ className, ...props }) {
    const isCodeBlock = useIsMarkdownCodeBlock();
    return (
      <code
        className={cn(
          !isCodeBlock &&
            "aui-md-inline-code bg-muted rounded-md px-1.5 py-0.5 font-mono text-[0.85em]",
          className,
        )}
        {...props}
      />
    );
  },
  CodeHeader,
  SyntaxHighlighter: CodeFenceHighlighter,
});
