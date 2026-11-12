"use client";

import Markdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { LINK_REL, sanitizeSchema } from "@/utils/markdown";

// Raw HTML is dropped and the sanitiser keeps only what its schema lists, as in chat answers.
const REMARK = [remarkGfm];
const REHYPE: Parameters<typeof Markdown>[0]["rehypePlugins"] = [[rehypeSanitize, sanitizeSchema]];

const STYLE = [
  "text-sm leading-6 [&>*+*]:mt-3",
  "[&_h1]:text-lg [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_h3]:font-semibold [&_h4]:font-medium",
  "[&_ul]:list-disc [&_ol]:list-decimal [&_ul]:ps-5 [&_ol]:ps-5 [&_li+li]:mt-1",
  "[&_a]:underline [&_a]:underline-offset-4 [&_strong]:font-semibold",
  "[&_code]:bg-muted [&_code]:rounded [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]",
  "[&_pre]:bg-muted [&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0",
  "[&_blockquote]:border-s-2 [&_blockquote]:ps-3 [&_blockquote]:text-muted-foreground",
  "[&_table]:w-full [&_th]:border-b [&_th]:text-start [&_td]:border-b [&_td]:py-1 [&_th]:py-1",
].join(" ");

/** Markdown outside a message, such as a skill's instructions. Images become links, so nothing is fetched. */
export function MarkdownDocument({ text, testId }: { text: string; testId?: string }) {
  return (
    <div data-testid={testId} className={STYLE}>
      <Markdown
        skipHtml
        remarkPlugins={REMARK}
        rehypePlugins={REHYPE}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel={LINK_REL}>
              {children}
            </a>
          ),
          img: ({ src, alt }) =>
            typeof src === "string" ? (
              <a href={src} target="_blank" rel={LINK_REL}>
                {alt || src}
              </a>
            ) : null,
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}
